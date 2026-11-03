"""#116 T04: preparation and final command stay single-attempt under competition.

Public HTTP/workflow surface, real temporary SQLite, recordable adapters. Every
ledger claim is re-read through an independent sqlite3 connection.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest
from httpx import ASGITransport

from app.adapters.base import ProviderOutcome
from app.main import create_app
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import AuthClient
from tests.support.t01_prompt import action, prepared
from tests.support.t02_prompt import responder
from tests.test_t65_generation_cycle import _drain, _environment
from tests.test_v2_t40_prompt_review import png_bytes

COMMAND_ID = "22222222-2222-4222-8222-222222222114"
REQUEST_ID = "11111111-1111-4111-8111-111111111113"


def _ledger(path: str) -> dict[str, int]:
    with sqlite3.connect(path) as db:
        def count(sql: str) -> int:
            return db.execute(sql).fetchone()[0]

        return {
            "intents": count("SELECT count(*) FROM generation_intents"),
            "usage": count("SELECT count(*) FROM generation_usage"),
            "slots": count("SELECT count(*) FROM generation_slots"),
            "live_slots": count("SELECT count(*) FROM generation_slots WHERE released_at IS NULL"),
            "snapshots": count("SELECT count(*) FROM prepared_prompts"),
            "authorizations": count("SELECT count(*) FROM final_authorizations"),
            "generations": count("SELECT count(*) FROM generations"),
            "jobs": count("SELECT count(*) FROM generation_jobs"),
        }


async def _scopes(path: str) -> list[tuple[str, str]]:
    with sqlite3.connect(path) as db:
        return db.execute(
            "SELECT scope, scope_id FROM generation_slots WHERE released_at IS NULL ORDER BY scope"
        ).fetchall()


async def confirm(client, project_id, view, command_id=COMMAND_ID):
    preview = view["prompt_preview"]
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/confirm_prompt",
        json={
            "expected_revision": view["project_revision"],
            "payload": {
                "snapshot_id": preview["id"],
                "snapshot_digest": preview["request_digest"],
                "command_id": command_id,
                "command_context": preview["command_context"],
            },
        },
    )


async def _requirements_payload(client, project_id, request_id=REQUEST_ID, revision_delta=0):
    view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
    card = next(
        x for x in view["artifacts"] if x["kind"] == "final_brief" and x["status"] == "approved"
    )
    return {
        "expected_revision": view["project_revision"],
        "payload": {
            "artifact_id": card["id"],
            "artifact_digest": card["digest"],
            "requirement_revision": view["requirement_revision"] + revision_delta,
            "request_id": request_id,
        },
    }


async def _prepare_project(client) -> str:
    project = (await client.post("/api/projects", json={})).json()
    project_id = project["id"]
    await action(
        client,
        project_id,
        "submit_request",
        text="请制作一个白色陶瓷杯商品主图，禁止价格标签和促销文案",
    )
    for name in ("compose_brief", "plan_creative", "plan_visual", "finalize_requirements"):
        await action(client, project_id, name)
    return project_id


@pytest.mark.asyncio
async def test_preparation_double_click_accepts_once(full_settings):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await _prepare_project(client)
        body = await _requirements_payload(client, project_id)
        url = f"/api/projects/{project_id}/workflow/actions/confirm_requirements"
        results = await asyncio.gather(
            client.post(url, json=body),
            client.post(url, json=body),
        )
        codes = sorted(r.status_code for r in results)
        assert codes[0] == 200 and codes[1] in (200, 409), [r.text for r in results]
        await _drain(app)
        ledger = _ledger(full_settings.database_path)
        assert ledger["intents"] == 1 and ledger["usage"] == 1
        assert ledger["slots"] == 3 and ledger["live_slots"] == 3
        assert visual.submit_calls == []
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert view["usage"]["used"] == 1


@pytest.mark.asyncio
async def test_preparation_same_request_id_with_other_parameters_is_a_conflict(full_settings):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await _prepare_project(client)
        first = await _requirements_payload(client, project_id)
        accepted = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements", json=first
        )
        assert accepted.status_code == 200, accepted.text
        conflicting = await _requirements_payload(client, project_id, revision_delta=1)
        rejected = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements", json=conflicting
        )
        assert rejected.status_code == 409, rejected.text
        ledger = _ledger(full_settings.database_path)
        assert ledger["intents"] == 1 and ledger["usage"] == 1 and ledger["slots"] == 3
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_busy_quota_refuses_without_accepting_queueing_or_calling(full_settings):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        first = await prepared(client)
        await _drain(app)
        second = await _prepare_project(client)
        body = await _requirements_payload(client, second)
        refused = await client.post(
            f"/api/projects/{second}/workflow/actions/confirm_requirements", json=body
        )
        assert refused.status_code == 429, refused.text
        assert "GENERATION_SLOT_BUSY" in refused.text
        assert (await client.get(f"/api/projects/{second}")).json()["generations"] == []
        view = (await client.get(f"/api/projects/{second}/workflow")).json()
        assert view["status"] in {"active", "needs_input"}
        assert view["usage"]["used"] == 1  # the refused project adds no ledger row
        ledger = _ledger(full_settings.database_path)
        assert ledger["intents"] == 1 and ledger["usage"] == 1
        assert visual.submit_calls == []
        # Nothing runs later by itself: the refused request is not a queued job.
        await _drain(app)
        assert (await client.get(f"/api/projects/{second}")).json()["generations"] == []
        assert _ledger(full_settings.database_path)["usage"] == 1
        assert first != second


@pytest.mark.asyncio
async def test_rolling_window_and_three_slot_scopes_gate_new_acceptance(full_settings):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        first = await prepared(client)
        await _drain(app)
        with sqlite3.connect(full_settings.database_path) as db:
            account_id = db.execute(
                "SELECT operator_account_id FROM generation_intents"
            ).fetchone()[0]
        scopes = await _scopes(full_settings.database_path)
        assert sorted(x[0] for x in scopes) == ["instance", "personal", "project"]
        assert ("personal", account_id) in scopes
        assert ("instance", "instance") in scopes
        assert ("project", first) in scopes

        with sqlite3.connect(full_settings.database_path) as db:
            db.execute(
                "UPDATE generation_usage SET window_start_ms=window_start_ms-172800000, "
                "window_expires_ms=window_expires_ms-172800000"
            )
            db.execute(
                "UPDATE generation_slots SET released_at='2026-01-01T00:00:00Z' "
                "WHERE scope='personal'"
            )
        second = await _prepare_project(client)
        body = await _requirements_payload(client, second)
        accepted = await client.post(
            f"/api/projects/{second}/workflow/actions/confirm_requirements", json=body
        )
        assert accepted.status_code == 200, accepted.text
        with sqlite3.connect(full_settings.database_path) as db:
            rows = db.execute(
                "SELECT scope, released_at FROM generation_slots WHERE scope='instance'"
            ).fetchall()
        assert len(rows) == 2 and all(row[1] is None for row in rows)
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_same_final_command_replay_after_lost_response_returns_one_task(full_settings):
    gate = asyncio.Event()
    visual = VisualStub(
        gate=gate,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        ),
    )
    llm = LLMStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        first = await confirm(client, project_id, view)
        assert first.status_code == 200, first.text
        # The first response is never observed by the client: replay the same command.
        replay = await confirm(client, project_id, view)
        assert replay.status_code == 200, replay.text
        assert replay.json()["generation_job"]["id"] == first.json()["generation_job"]["id"]
        ledger = _ledger(full_settings.database_path)
        assert ledger["authorizations"] == 1 and ledger["generations"] == 1
        assert ledger["jobs"] == 1 and ledger["usage"] == 1
        gate.set()
        await _drain(app)
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_distinct_command_id_on_same_snapshot_is_an_explicit_conflict(full_settings):
    gate = asyncio.Event()
    visual = VisualStub(
        gate=gate,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        ),
    )
    async with _environment(full_settings, LLMStub(), visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        first = await confirm(client, project_id, view)
        assert first.status_code == 200, first.text
        other = await confirm(
            client, project_id, view, command_id="33333333-3333-4333-8333-333333333333"
        )
        assert other.status_code == 409, other.text
        assert "FINAL_ALREADY_AUTHORIZED" in other.text
        gate.set()
        await _drain(app)
        assert len(visual.submit_calls) == 1
        ledger = _ledger(full_settings.database_path)
        assert ledger["authorizations"] == 1 and ledger["generations"] == 1 and ledger["jobs"] == 1


@pytest.mark.asyncio
async def test_reused_command_id_with_other_parameters_is_a_conflict(full_settings):
    gate = asyncio.Event()
    visual = VisualStub(
        gate=gate,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        ),
    )
    async with _environment(full_settings, LLMStub(), visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        first = await confirm(client, project_id, view)
        assert first.status_code == 200, first.text
        tampered = dict(view)
        tampered["prompt_preview"] = dict(view["prompt_preview"])
        tampered["prompt_preview"]["command_context"] = "0" * 64
        reused = await confirm(client, project_id, tampered)
        assert reused.status_code == 409, reused.text
        assert visual.submit_calls == []
        gate.set()
        await _drain(app)
        assert len(visual.submit_calls) == 1
        assert _ledger(full_settings.database_path)["jobs"] == 1


@pytest.mark.asyncio
async def test_two_independent_apps_with_distinct_commands_execute_once(full_settings):
    gate = asyncio.Event()
    visual = VisualStub(
        gate=gate,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        ),
    )
    llm = LLMStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        other_app = create_app(
            settings=full_settings, registry=build_test_registry(llm=llm, visual=visual)
        )
        async with other_app.router.lifespan_context(other_app):
            project_id = await prepared(client)
            await _drain(app)
            view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
            async with AuthClient(
                transport=ASGITransport(app=other_app),
                base_url="https://testserver",
                cookies=client.cookies,
                csrf_token=client.csrf_token,
            ) as second:
                results = await asyncio.gather(
                    confirm(client, project_id, view),
                    confirm(
                        second,
                        project_id,
                        view,
                        command_id="55555555-5555-4555-8555-555555555555",
                    ),
                )
            assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
            gate.set()
            await _drain(app)
            await _drain(other_app)
            assert len(visual.submit_calls) == 1
            ledger = _ledger(full_settings.database_path)
            assert ledger["authorizations"] == 1 and ledger["jobs"] == 1
            assert ledger["generations"] == 1 and ledger["usage"] == 1


@pytest.mark.asyncio
async def test_independent_connection_holds_the_single_attempt_ledger(full_settings):
    gate = asyncio.Event()
    visual = VisualStub(
        gate=gate,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        ),
    )
    async with _environment(full_settings, LLMStub(), visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert (await confirm(client, project_id, view)).status_code == 200
        with sqlite3.connect(full_settings.database_path) as db:
            intent = db.execute(
                "SELECT status, request_id FROM generation_intents WHERE project_id=?",
                (project_id,),
            ).fetchall()
            assert len(intent) == 1 and intent[0][0] == "executing"
            assert intent[0][1] == REQUEST_ID
            usage = db.execute(
                "SELECT count(*), min(window_expires_ms - window_start_ms) FROM generation_usage"
            ).fetchone()
            assert usage == (1, 86400000)
            live = db.execute(
                "SELECT count(*) FROM generation_slots WHERE released_at IS NULL"
            ).fetchone()[0]
            assert live == 3
            jobs = db.execute(
                "SELECT count(*), count(DISTINCT generation_id) FROM generation_jobs"
            ).fetchone()
            assert jobs == (1, 1)
        gate.set()
        await _drain(app)
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_accepted_failure_keeps_the_counted_use(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="failed", error_code="bad_request", error_message="x")
    )
    async with _environment(full_settings, LLMStub(), visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert (await confirm(client, project_id, view)).status_code == 200
        await _drain(app)
        assert len(visual.submit_calls) == 1
        after = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert after["usage"]["used"] == 1
        assert _ledger(full_settings.database_path)["usage"] == 1


@pytest.mark.parametrize(
    ("point", "operation", "condition"),
    [
        ("generation_slots", "INSERT", "1"),
        ("generation_usage", "INSERT", "1"),
        ("audit_events", "INSERT", "NEW.event_type='generation_intent_accepted'"),
    ],
)
@pytest.mark.asyncio
async def test_preparation_write_fault_rolls_back_every_ledger_row(
    full_settings, point, operation, condition
):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await _prepare_project(client)
        body = await _requirements_payload(client, project_id)
        before = len(llm.role_calls_received)
        with sqlite3.connect(full_settings.database_path) as db:
            db.execute(
                f"CREATE TRIGGER t04_fault BEFORE {operation} ON {point} WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT,'controlled T04 failure'); END"
            )
        async with _as_raw(app, client) as raw:
            response = await raw.post(
                f"/api/projects/{project_id}/workflow/actions/confirm_requirements", json=body
            )
        assert response.status_code == 500, response.text
        ledger = _ledger(full_settings.database_path)
        assert ledger["intents"] == 0 and ledger["usage"] == 0 and ledger["slots"] == 0
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert view["usage"]["used"] == 0 and view["prompt_preview"] is None
        assert len(llm.role_calls_received) == before and visual.submit_calls == []
        with sqlite3.connect(full_settings.database_path) as db:
            db.execute("DROP TRIGGER t04_fault")
        retry = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements", json=body
        )
        assert retry.status_code == 200, retry.text
        ledger = _ledger(full_settings.database_path)
        assert ledger["intents"] == 1 and ledger["usage"] == 1 and ledger["slots"] == 3


@pytest.mark.parametrize(
    ("point", "condition"),
    [
        ("final_authorizations", "1"),
        ("generations", "1"),
        ("generation_jobs", "1"),
        ("audit_events", "NEW.event_type='prompt.final_authorized'"),
        ("prepared_prompts", "1"),
    ],
)
@pytest.mark.asyncio
async def test_final_write_fault_leaves_no_authorization_or_provider_call(
    full_settings, point, condition
):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        operation = "UPDATE" if point == "prepared_prompts" else "INSERT"
        with sqlite3.connect(full_settings.database_path) as db:
            db.execute(
                f"CREATE TRIGGER t04_final_fault BEFORE {operation} ON {point} WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT,'controlled T04 final failure'); END"
            )
        async with _as_raw(app, client) as raw:
            response = await confirm(raw, project_id, view)
        assert response.status_code == 500, response.text
        ledger = _ledger(full_settings.database_path)
        assert ledger["authorizations"] == 0 and ledger["generations"] == 0
        assert ledger["jobs"] == 0 and ledger["usage"] == 1
        after = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert after["generation_job"] is None
        assert after["prompt_preview"]["request_digest"] == view["prompt_preview"]["request_digest"]
        assert visual.submit_calls == []
        with sqlite3.connect(full_settings.database_path) as db:
            db.execute("DROP TRIGGER t04_final_fault")
        retry = await confirm(client, project_id, view)
        assert retry.status_code == 200, retry.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        assert _ledger(full_settings.database_path)["usage"] == 1


@pytest.mark.asyncio
async def test_same_final_command_concurrent_two_clients_returns_one_task(full_settings):
    gate = asyncio.Event()
    visual = VisualStub(
        gate=gate,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        ),
    )
    llm = LLMStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        other_app = create_app(
            settings=full_settings, registry=build_test_registry(llm=llm, visual=visual)
        )
        async with other_app.router.lifespan_context(other_app):
            project_id = await prepared(client)
            await _drain(app)
            view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
            async with AuthClient(
                transport=ASGITransport(app=other_app),
                base_url="https://testserver",
                cookies=client.cookies,
                csrf_token=client.csrf_token,
            ) as second:
                results = await asyncio.gather(
                    confirm(client, project_id, view),
                    confirm(second, project_id, view),
                )
            assert all(r.status_code in (200, 409) for r in results), [r.text for r in results]
            assert any(r.status_code == 200 for r in results)
            if all(r.status_code == 200 for r in results):
                assert (
                    results[0].json()["generation_job"]["id"]
                    == results[1].json()["generation_job"]["id"]
                )
            gate.set()
            await _drain(app)
            await _drain(other_app)
        assert len(visual.submit_calls) == 1
        ledger = _ledger(full_settings.database_path)
        assert ledger["authorizations"] == 1 and ledger["jobs"] == 1
        assert ledger["generations"] == 1 and ledger["usage"] == 1


@pytest.mark.asyncio
async def test_three_concurrent_final_requests_keep_one_authorization(full_settings):
    gate = asyncio.Event()
    visual = VisualStub(
        gate=gate,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        ),
    )
    llm = LLMStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        other_app = create_app(
            settings=full_settings, registry=build_test_registry(llm=llm, visual=visual)
        )
        async with other_app.router.lifespan_context(other_app):
            project_id = await prepared(client)
            await _drain(app)
            view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
            async with AuthClient(
                transport=ASGITransport(app=other_app),
                base_url="https://testserver",
                cookies=client.cookies,
                csrf_token=client.csrf_token,
            ) as second, AuthClient(
                transport=ASGITransport(app=other_app),
                base_url="https://testserver",
                cookies=client.cookies,
                csrf_token=client.csrf_token,
            ) as third:
                results = await asyncio.gather(
                    confirm(client, project_id, view),
                    confirm(second, project_id, view),
                    confirm(third, project_id, view),
                )
            assert all(r.status_code in (200, 409) for r in results), [r.text for r in results]
            assert any(r.status_code == 200 for r in results)
            gate.set()
            await _drain(app)
            await _drain(other_app)
        assert len(visual.submit_calls) == 1
        ledger = _ledger(full_settings.database_path)
        assert ledger["authorizations"] == 1 and ledger["jobs"] == 1
        assert ledger["generations"] == 1 and ledger["usage"] == 1


@pytest.mark.asyncio
async def test_daily_quota_exhausted_refuses_before_acceptance(full_settings):
    settings = full_settings.model_copy(update={"generation_daily_limit": 1})
    llm, visual = LLMStub(), VisualStub()
    async with _environment(settings, llm, visual, responder) as (app, client):
        first = await _prepare_project(client)
        body = await _requirements_payload(client, first)
        assert (
            await client.post(
                f"/api/projects/{first}/workflow/actions/confirm_requirements", json=body
            )
        ).status_code == 200
        await _drain(app)
        second = await _prepare_project(client)
        refused = await client.post(
            f"/api/projects/{second}/workflow/actions/confirm_requirements",
            json=await _requirements_payload(
                client, second, request_id="66666666-6666-4666-8666-666666666666"
            ),
        )
        assert refused.status_code == 429, refused.text
        assert "GENERATION_LIMIT_REACHED" in refused.text
        ledger = _ledger(settings.database_path)
        assert ledger["intents"] == 1 and ledger["usage"] == 1 and ledger["slots"] == 3
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_conflict_mapper_names_command_and_snapshot_races(full_settings):
    from app.confirmation_conflicts import resolve_final_integrity_conflict

    visual = VisualStub()
    async with _environment(full_settings, LLMStub(), visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert (await confirm(client, project_id, view)).status_code == 200
        by_command = await resolve_final_integrity_conflict(
            app.state.session_factory,
            project_id=project_id,
            command_id=COMMAND_ID,
            command_digest="0" * 64,
            snapshot_id=view["prompt_preview"]["id"],
        )
        assert by_command.code == "FINAL_COMMAND_CONFLICT"
        by_snapshot = await resolve_final_integrity_conflict(
            app.state.session_factory,
            project_id=project_id,
            command_id="77777777-7777-4777-8777-777777777777",
            command_digest="0" * 64,
            snapshot_id=view["prompt_preview"]["id"],
        )
        assert by_snapshot.code == "FINAL_ALREADY_AUTHORIZED"


def _as_raw(app, client):
    return AuthClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
        cookies=client.cookies,
        csrf_token=client.csrf_token,
    )

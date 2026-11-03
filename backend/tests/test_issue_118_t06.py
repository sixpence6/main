from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from contextlib import aclosing
from datetime import UTC, datetime

import pytest

from app.adapters.base import ProviderError, ProviderOutcome
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import run_client
from tests.support.t01_prompt import action, prepared
from tests.support.t02_prompt import final_click
from tests.support.t02_prompt import responder as preparation_responder
from tests.test_t62_background_ownership import _add_account, _login_user
from tests.test_t65_generation_cycle import _drain, _environment
from tests.test_v2_t40_prompt_review import png_bytes


def _rows(path: str, sql: str, params: tuple = ()) -> list[dict]:
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql, params).fetchall()]


def _execute(path: str, sql: str, params: tuple = ()) -> None:
    with sqlite3.connect(path) as db:
        db.execute(sql, params)


def _intent(path: str, project_id: str) -> dict:
    rows = _rows(
        path,
        "SELECT * FROM generation_intents WHERE project_id=? ORDER BY created_at DESC, id DESC",
        (project_id,),
    )
    assert rows
    return rows[0]


def _budget(path: str, project_id: str) -> dict:
    rows = _rows(
        path,
        "SELECT b.* FROM revision_budgets b JOIN workflow_runs r ON r.id=b.run_id "
        "WHERE r.project_id=? AND b.artifact_kind='prompt_plan'",
        (project_id,),
    )
    assert rows
    return rows[0]


def _live_slots(path: str, intent_id: str) -> int:
    rows = _rows(
        path,
        "SELECT COUNT(*) AS n FROM generation_slots "
        "WHERE intent_id=? AND released_at IS NULL",
        (intent_id,),
    )
    return int(rows[0]["n"])


def _revision_responder(extra_positive: str = "杯身加金色把手"):
    engineers = {"count": 0}

    def responder(request):
        base = preparation_responder(request)
        if request.payload["task"]["role"] == "generation_engineer":
            engineers["count"] += 1
            if engineers["count"] > 1:
                base["data"]["positive_prompt"] += f"；{extra_positive}"
        return base

    return responder


def _reviewer_failure_once():
    failed = {"value": False}

    def responder(request):
        if (
            request.payload["task"]["role"] == "output_reviewer"
            and request.payload["task"]["stage"] == "S07"
            and not failed["value"]
        ):
            failed["value"] = True
            raise ProviderError(
                "review timeout", evidence="uncertain", error_code="LLM_TIMEOUT"
            )
        return preparation_responder(request)

    return responder


async def _workflow(client, project_id: str) -> dict:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


def _action_ids(view: dict) -> set[str]:
    return {item["id"] for item in view["available_actions"]}


@pytest.mark.asyncio
async def test_text_repair_keeps_intent_usage_budget_and_requires_new_confirmation(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(
        full_settings, llm, visual, _revision_responder()
    ) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        assert before["stage"] == "S07" and before["status"] == "needs_input"
        preview = before["prompt_preview"]
        assert preview is not None and preview["can_confirm"] is True
        assert {
            "confirm_prompt",
            "revise_prompt",
            "abandon_generation",
            "revise_requirements",
        } <= _action_ids(before)

        old_intent = before["intent"]
        old_request_id = old_intent["request_id"]
        old_requirement_revision = before["requirement_revision"]
        old_revision = before["project_revision"]
        assert before["usage"]["used"] == 1
        assert _budget(full_settings.database_path, project_id)["used"] == 1
        assert _live_slots(full_settings.database_path, old_intent["id"]) == 3

        revised = await action(
            client,
            project_id,
            "revise_prompt",
            text="杯身加金色把手，其他保持已确认内容",
        )
        assert revised["stage"] == "S07" and revised["status"] == "needs_input"
        repaired = revised["prompt_preview"]
        assert repaired is not None and repaired["can_confirm"] is True
        assert repaired["id"] != preview["id"]
        assert repaired["request"]["intent_id"] == old_intent["id"]
        assert "杯身加金色把手" in repaired["request"]["prompt"]
        assert revised["intent"]["id"] == old_intent["id"]
        assert revised["intent"]["request_id"] == old_request_id
        assert revised["requirement_revision"] == old_requirement_revision
        assert revised["project_revision"] == old_revision
        assert revised["usage"]["used"] == 1

        budget = _budget(full_settings.database_path, project_id)
        assert budget["used"] == 2
        old_rows = _rows(
            full_settings.database_path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (preview["id"],),
        )
        assert old_rows == [{"status": "invalidated"}]
        new_rows = _rows(
            full_settings.database_path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (repaired["id"],),
        )
        assert new_rows == [{"status": "awaiting_confirmation"}]
        assert _rows(
            full_settings.database_path,
            "SELECT COUNT(*) AS n FROM generation_jobs WHERE project_id=?",
            (project_id,),
        )[0]["n"] == 0

        old_click = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_prompt",
            json={
                "expected_revision": revised["project_revision"],
                "payload": {
                    "snapshot_id": preview["id"],
                    "snapshot_digest": preview["request_digest"],
                    "command_id": str(uuid.uuid4()),
                    "command_context": preview["command_context"],
                },
            },
        )
        assert old_click.status_code == 409, old_click.text
        assert old_click.json()["error"]["code"] == "PROMPT_STALE"
        assert visual.submit_calls == []

        confirmed = await final_click(
            client,
            {"project_id": project_id, "workflow": revised},
            command_id=str(uuid.uuid4()),
        )
        assert confirmed.status_code == 200, confirmed.text
        await _drain(app)
        after = await _workflow(client, project_id)
        assert after["stage"] == "S10"
        assert after["usage"]["used"] == 1
        assert len(visual.submit_calls) == 1
        reviewer_calls = [
            call
            for call in llm.role_calls_received
            if call.payload["task"]["role"] == "output_reviewer"
            and call.payload["task"]["stage"] == "S07"
        ]
        assert len(reviewer_calls) == 2
        reviewed_request = reviewer_calls[-1].payload["review_targets"][0][
            "effective_request"
        ]
        assert reviewed_request["prompt"] == repaired["request"]["prompt"]
        assert "杯身加金色把手" in reviewed_request["prompt"]

        authorizations = _rows(
            full_settings.database_path,
            "SELECT snapshot_id, snapshot_digest, intent_id "
            "FROM final_authorizations WHERE project_id=?",
            (project_id,),
        )
        assert len(authorizations) == 1
        assert authorizations[0]["snapshot_id"] == repaired["id"]
        assert authorizations[0]["snapshot_digest"] == repaired["request_digest"]
        assert authorizations[0]["intent_id"] == old_intent["id"]
        assert _rows(
            full_settings.database_path,
            "SELECT COUNT(*) AS n FROM final_authorizations WHERE snapshot_id=?",
            (preview["id"],),
        )[0]["n"] == 0
        assert visual.submit_calls[0].prompt == repaired["request"]["prompt"]
        assert after["intent"]["request_id"] == old_request_id
        assert _rows(
            full_settings.database_path,
            "SELECT COUNT(*) AS n FROM generation_intents WHERE project_id=?",
            (project_id,),
        )[0]["n"] == 1


@pytest.mark.asyncio
async def test_budget_survives_restart_and_exhaustion_keeps_last_candidate(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    responder = _revision_responder("每次调整都保留白色杯身")
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        first = await _workflow(client, project_id)
        assert first["usage"]["used"] == 1
        await action(client, project_id, "revise_prompt", text="第一次调整")
        second = await _workflow(client, project_id)
        assert second["prompt_preview"]["id"] != first["prompt_preview"]["id"]
        assert _budget(full_settings.database_path, project_id)["used"] == 2

    registry = build_test_registry(llm=llm, visual=visual)
    async with run_client(full_settings, registry) as client:
        reopened = await _workflow(client, project_id)
        assert reopened["prompt_preview"]["id"] == second["prompt_preview"]["id"]
        assert reopened["intent"]["status"] == "accepted"
        assert reopened["usage"]["used"] == 1
        assert _budget(full_settings.database_path, project_id)["used"] == 2

        await asyncio.sleep(0.05)
        waited = await _workflow(client, project_id)
        assert waited["prompt_preview"]["id"] == second["prompt_preview"]["id"]
        assert waited["intent"]["status"] == "accepted"
        assert _budget(full_settings.database_path, project_id)["used"] == 2

        third = await action(client, project_id, "revise_prompt", text="第二次调整")
        assert third["prompt_preview"] is not None
        assert _budget(full_settings.database_path, project_id)["used"] == 3

        exhausted_view = await _workflow(client, project_id)
        rejected = await client.post(
            f"/api/projects/{project_id}/workflow/actions/revise_prompt",
            json={
                "expected_revision": exhausted_view["project_revision"],
                "text": "第三次调整应被持久预算拦住",
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "REVISION_BUDGET_EXHAUSTED"
        after = await _workflow(client, project_id)
        assert after["prompt_preview"]["id"] == third["prompt_preview"]["id"]
        assert after["usage"]["used"] == 1
        assert _budget(full_settings.database_path, project_id)["used"] == 3


@pytest.mark.asyncio
async def test_explicit_abandon_is_bound_idempotent_and_never_claims_remote_cancel(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (
        app,
        client,
    ):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        preview = before["prompt_preview"]
        intent = before["intent"]
        command_id = str(uuid.uuid4())
        payload = {
            "snapshot_id": preview["id"],
            "snapshot_digest": preview["request_digest"],
            "command_id": command_id,
        }

        abandoned = await action(
            client, project_id, "abandon_generation", payload=payload
        )
        assert abandoned["stage"] == "S07"
        assert abandoned["status"] == "blocked"
        assert abandoned["issue"]["code"] == "PROMPT_ABANDONED"
        assert abandoned["prompt_preview"] is None
        assert abandoned["intent"]["id"] == intent["id"]
        assert abandoned["intent"]["status"] == "abandoned"
        assert abandoned["requirement_revision"] == before["requirement_revision"]
        assert abandoned["usage"]["used"] == 1
        assert _action_ids(abandoned) == {"revise_requirements"}

        path = full_settings.database_path
        assert _intent(path, project_id)["status"] == "abandoned"
        assert _live_slots(path, intent["id"]) == 0
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM generation_usage WHERE project_id=?",
            (project_id,),
        )[0]["n"] == 1
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM generation_jobs WHERE project_id=?",
            (project_id,),
        )[0]["n"] == 0
        assert _rows(
            path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (preview["id"],),
        ) == [{"status": "invalidated"}]
        audits = _rows(
            path,
            "SELECT * FROM audit_events WHERE event_type='generation_prompt_abandoned'",
        )
        assert len(audits) == 1
        detail = json.loads(audits[0]["detail_json"])
        assert audits[0]["correlation_id"] == command_id
        assert detail["intent_id"] == intent["id"]
        assert detail["snapshot_id"] == preview["id"]
        assert detail["snapshot_digest"] == preview["request_digest"]
        assert detail["request_id"] == intent["request_id"]
        run = _rows(
            path,
            "SELECT id FROM workflow_runs WHERE project_id=?",
            (project_id,),
        )[0]
        assert detail["project_id"] == project_id
        assert detail["run_id"] == run["id"]
        assert detail["project_revision"] == before["project_revision"]
        assert detail["remote_cancelled"] is False
        assert detail["image_attempt_exists"] is False
        assert visual.submit_calls == []

        replay = await action(
            client, project_id, "abandon_generation", payload=payload
        )
        assert replay["intent"]["status"] == "abandoned"
        assert replay["usage"]["used"] == 1
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM audit_events "
            "WHERE event_type='generation_prompt_abandoned'",
        )[0]["n"] == 1
        assert _live_slots(path, intent["id"]) == 0

        view = await _workflow(client, project_id)
        conflict = await client.post(
            f"/api/projects/{project_id}/workflow/actions/abandon_generation",
            json={
                "expected_revision": view["project_revision"],
                "payload": {**payload, "snapshot_digest": "f" * 64},
            },
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "COMMAND_ID_CONFLICT"

        new_command = await client.post(
            f"/api/projects/{project_id}/workflow/actions/abandon_generation",
            json={
                "expected_revision": view["project_revision"],
                "payload": {**payload, "command_id": str(uuid.uuid4())},
            },
        )
        assert new_command.status_code == 409, new_command.text
        assert new_command.json()["error"]["code"] == "PROMPT_NOT_AWAITING_CONFIRMATION"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_abandon_http_boundary_keeps_identity_owner_origin_and_csrf(
    full_settings,
):
    from httpx import ASGITransport, AsyncClient

    llm = LLMStub()
    visual = VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (
        app,
        client,
    ):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}"
        before = await _workflow(client, project_id)
        preview = before["prompt_preview"]
        body = {
            "expected_revision": before["project_revision"],
            "payload": {
                "snapshot_id": preview["id"],
                "snapshot_digest": preview["request_digest"],
                "command_id": str(uuid.uuid4()),
            },
        }

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as anon:
            anonymous = await anon.post(
                f"{path}/workflow/actions/abandon_generation", json=body
            )
            assert anonymous.status_code == 401, anonymous.text

        other = await _add_account(app, "t06-other@example.com")
        async with aclosing(
            await _login_user(app, other.email_normalized)
        ) as other_client:
            assert (await other_client.get(f"{path}/workflow")).status_code == 404
            foreign = await other_client.post(
                f"{path}/workflow/actions/abandon_generation", json=body
            )
            assert foreign.status_code == 404, foreign.text

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://testserver",
            cookies=client.cookies,
        ) as raw:
            for headers in (
                {"Origin": "https://evil.invalid", "X-CSRF-Token": client.csrf_token},
                {"Origin": "https://testserver"},
            ):
                guarded = await raw.post(
                    f"{path}/workflow/actions/abandon_generation",
                    headers=headers,
                    json=body,
                )
                assert guarded.status_code == 403, guarded.text

        invalid = await client.post(
            f"{path}/workflow/actions/abandon_generation",
            json={
                "expected_revision": before["project_revision"],
                "payload": {**body["payload"], "snapshot_digest": "not-a-digest"},
            },
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"

        after = await _workflow(client, project_id)
        assert after["prompt_preview"]["id"] == preview["id"]
        assert after["intent"]["status"] == "accepted"
        assert after["usage"]["used"] == 1
        assert _live_slots(full_settings.database_path, before["intent"]["id"]) == 3
        assert _rows(
            full_settings.database_path,
            "SELECT COUNT(*) AS n FROM audit_events "
            "WHERE event_type='generation_prompt_abandoned'",
        )[0]["n"] == 0
        assert visual.submit_calls == []


@pytest.mark.parametrize(
    "job_status", ["submit_pending", "submitted", "remote_pending", "unknown"]
)
@pytest.mark.asyncio
async def test_waiting_does_not_expire_and_any_attempt_prevents_settlement(
    full_settings, job_status
):
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (
        app,
        client,
    ):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        intent = before["intent"]
        preview = before["prompt_preview"]

        await asyncio.sleep(0.1)
        waited = await _workflow(client, project_id)
        assert waited["status"] == "needs_input"
        assert waited["intent"]["status"] == "accepted"
        assert waited["usage"]["used"] == 1
        assert waited["prompt_preview"]["id"] == preview["id"]

        path = full_settings.database_path
        run = _rows(
            path,
            "SELECT id FROM workflow_runs WHERE project_id=?",
            (project_id,),
        )[0]
        now = datetime.now(UTC).isoformat()
        _execute(
            path,
            "INSERT INTO generation_jobs (id, project_id, run_id, intent_id, "
            "request_id, request_digest, confirmed_snapshot_digest, status, "
            "submit_attempted, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                str(uuid.uuid4()),
                project_id,
                run["id"],
                intent["id"],
                str(uuid.uuid4()),
                "a" * 64,
                "b" * 64,
                job_status,
                now,
                now,
            ),
        )
        command = {
            "snapshot_id": preview["id"],
            "snapshot_digest": preview["request_digest"],
            "command_id": str(uuid.uuid4()),
        }
        refused = await client.post(
            f"/api/projects/{project_id}/workflow/actions/abandon_generation",
            json={
                "expected_revision": waited["project_revision"],
                "payload": command,
            },
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["error"]["code"] == "INTENT_ATTEMPT_EXISTS"

        revise_refused = await client.post(
            f"/api/projects/{project_id}/workflow/actions/revise_requirements",
            json={
                "expected_revision": waited["project_revision"],
                "text": "改成黑色杯身",
            },
        )
        assert revise_refused.status_code == 409, revise_refused.text
        assert revise_refused.json()["error"]["code"] == "INTENT_ATTEMPT_EXISTS"

        after = await _workflow(client, project_id)
        assert after["intent"]["status"] == "accepted"
        assert after["usage"]["used"] == 1
        assert _live_slots(path, intent["id"]) == 3
        assert after["prompt_preview"]["id"] == preview["id"]
        assert after["project_revision"] == waited["project_revision"]
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_requirements_revision_settles_only_the_unsubmitted_intent(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (
        app,
        client,
    ):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        intent = before["intent"]

        revised = await action(
            client,
            project_id,
            "revise_requirements",
            text="杯身改为哑光黑色，其他保持已确认内容",
        )
        assert revised["requirement_revision"] > before["requirement_revision"]
        path = full_settings.database_path
        assert _intent(path, project_id)["status"] == "abandoned"
        assert _intent(path, project_id)["id"] == intent["id"]
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM generation_usage WHERE project_id=?",
            (project_id,),
        )[0]["n"] == 1
        assert _live_slots(path, intent["id"]) == 0
        audits = _rows(
            path,
            "SELECT * FROM audit_events "
            "WHERE event_type='generation_intent_settled_unsubmitted'",
        )
        assert len(audits) == 1
        detail = json.loads(audits[0]["detail_json"])
        assert detail["reason"] == "requirements_revision"
        assert detail["image_attempt_exists"] is False
        assert detail["remote_cancelled"] is False
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_s12_review_replay_then_text_repair_keeps_one_intent_and_budget(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(
        full_settings, llm, visual, _reviewer_failure_once()
    ) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        failed = await _workflow(client, project_id)
        assert failed["stage"] == "S07" and failed["status"] == "failed"
        assert failed["issue"]["code"] == "REVIEW_UNAVAILABLE"
        assert _action_ids(failed) == {"continue_workflow"}
        assert failed["prompt_preview"] is None
        assert failed["usage"]["used"] == 1
        intent = failed["intent"]
        reviewer_calls = [
            call
            for call in llm.role_calls_received
            if call.payload["task"]["role"] == "output_reviewer"
            and call.payload["task"]["stage"] == "S07"
        ]
        assert len(reviewer_calls) == 1
        assert _budget(full_settings.database_path, project_id)["used"] == 1

        await asyncio.sleep(0.05)
        still_failed = await _workflow(client, project_id)
        assert still_failed["status"] == "failed"
        assert len(
            [
                call
                for call in llm.role_calls_received
                if call.payload["task"]["role"] == "output_reviewer"
                and call.payload["task"]["stage"] == "S07"
            ]
        ) == 1

        resumed = await action(client, project_id, "continue_workflow")
        assert resumed["stage"] == "S07" and resumed["status"] == "needs_input"
        assert resumed["prompt_preview"] is not None
        assert resumed["intent"]["id"] == intent["id"]
        assert resumed["usage"]["used"] == 1
        assert _budget(full_settings.database_path, project_id)["used"] == 1

        repaired = await action(
            client, project_id, "revise_prompt", text="杯身加金色把手"
        )
        assert repaired["prompt_preview"] is not None
        assert repaired["intent"]["id"] == intent["id"]
        assert repaired["intent"]["request_id"] == intent["request_id"]
        assert repaired["usage"]["used"] == 1
        assert _budget(full_settings.database_path, project_id)["used"] == 2
        reviewer_calls = [
            call
            for call in llm.role_calls_received
            if call.payload["task"]["role"] == "output_reviewer"
            and call.payload["task"]["stage"] == "S07"
        ]
        assert len(reviewer_calls) == 3

from __future__ import annotations

import sqlite3

import pytest

from tests.support.adapters import LLMStub, VisualStub
from tests.support.t01_prompt import prepared
from tests.support.t02_prompt import responder
from tests.test_t65_generation_cycle import _drain, _environment
from tests.test_v2_t17_restart_recovery import _seed_s08_async


@pytest.mark.parametrize(
    "field",
    [
        "artifact_digest",
        "requirement_snapshot_digest",
        "approved_by",
        "rules_version",
        "prompt_version",
        "payload",
    ],
)
@pytest.mark.asyncio
async def test_f1_valid_history_reads_but_mismatched_prompt_approval_is_hidden(
    full_settings, field
):
    seed = await _seed_s08_async(full_settings, pending_job=True)
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (_app, client):
        path = f"/api/projects/{seed.project_id}/workflow"
        before = (await client.get(path)).json()
        prompt = next(x for x in before["artifacts"] if x["kind"] == "prompt_plan")
        assert prompt["payload"] is not None
        with sqlite3.connect(full_settings.database_path) as db:
            if field == "payload":
                db.execute("UPDATE artifacts SET payload_json='{}' WHERE id=?", (prompt["id"],))
            else:
                value = "user" if field == "approved_by" else "invalid"
                db.execute(
                    f"UPDATE approvals SET {field}=? WHERE approval_type='generation_prompt'",
                    (value,),
                )
        after = (await client.get(path)).json()
        assert next(x for x in after["artifacts"] if x["kind"] == "prompt_plan")["payload"] is None
        assert after["prompt_preview"] is None
        assert llm.role_calls_received == [] and visual.submit_calls == []


@pytest.mark.asyncio
async def test_f2_query_configuration_changes_invalidate_wait_without_exposing_query(full_settings):
    full_settings.visual_base_url = "https://visual.invalid/v1?secret-route=test-secret-A"
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = await client.get(path)
        assert before.json()["prompt_preview"] is not None
        assert "secret-route" not in before.text and "test-secret-A" not in before.text
        app.state.settings.visual_base_url = "https://visual.invalid/v1?secret-route=test-secret-B"
        after = await client.get(path)
        assert after.json()["prompt_preview"] is None
        assert "test-secret-B" not in after.text
        assert all(
            x["payload"] is None for x in after.json()["artifacts"] if x["kind"] == "prompt_plan"
        )
        assert visual.submit_calls == []


async def confirm(client, project_id, view, command_id="22222222-2222-4222-8222-222222222114"):
    preview = view["prompt_preview"]
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/confirm_prompt",
        json={
            "expected_revision": view["project_revision"],
            "payload": {
                "snapshot_id": preview["id"],
                "snapshot_digest": preview["request_digest"],
                "command_id": command_id,
                "command_context": preview.get("command_context", ""),
            },
        },
    )


@pytest.mark.asyncio
async def test_final_click_executes_only_the_frozen_request_and_publishes_same_task(full_settings):
    from app.adapters.base import ProviderOutcome
    from tests.support.t01_prompt import action
    from tests.test_v2_t40_prompt_review import png_bytes

    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()
        expected = before["prompt_preview"]["request"]
        result = await confirm(client, project_id, before)
        assert result.status_code == 200, result.text
        job_id = result.json()["generation_job"]["id"]
        await _drain(app)
        after = (await client.get(path)).json()
        assert len(visual.submit_calls) == 1
        submitted = visual.submit_calls[0]
        assert submitted.prompt == expected["prompt"]
        assert submitted.image_size == "1024x1024"
        assert submitted.reference_path is None
        assert (
            submitted.brief.product_description == expected["requirements"]["product_description"]
        )
        assert after["generation_job"]["id"] == job_id
        assert after["usage"]["used"] == 1
        assert after["stage"] == "S10", after
        await action(client, project_id, "prepare_delivery")
        await action(client, project_id, "approve_delivery")
        published = await action(client, project_id, "publish_delivery")
        assert published["delivery"]["status"] == "published"
        assert published["versions"][-1]["generation_job_id"] == job_id
        image = await client.get(f"/api/assets/{published['delivery']['asset_id']}")
        assert image.status_code == 200
        assert published["usage"]["used"] == 1
        assert published["usage"]["project_active"] == 0


@pytest.mark.asyncio
async def test_final_replays_same_command_but_new_command_cannot_repeat_or_recount(full_settings):
    import asyncio

    from app.adapters.base import ProviderOutcome
    from tests.test_v2_t40_prompt_review import png_bytes

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
        path = f"/api/projects/{project_id}/workflow"
        view = (await client.get(path)).json()
        # The UUID is deliberately the preparation UUID: two independent command domains.
        command = "11111111-1111-4111-8111-111111111113"
        first = await confirm(client, project_id, view, command)
        assert first.status_code == 200, first.text
        replay = await confirm(client, project_id, view, command)
        assert replay.status_code == 200, replay.text
        assert replay.json()["generation_job"]["id"] == first.json()["generation_job"]["id"]
        different = await confirm(client, project_id, view, "33333333-3333-4333-8333-333333333333")
        assert different.status_code == 409, different.text
        forged = {**view, "prompt_preview": {**view["prompt_preview"], "command_context": "0" * 64}}
        assert (await confirm(client, project_id, forged, command)).status_code == 409
        gate.set()
        await _drain(app)
        again = await confirm(client, project_id, view, command)
        assert again.status_code == 200, again.text
        assert again.json()["generation_job"]["id"] == first.json()["generation_job"]["id"]
        assert len(visual.submit_calls) == 1 and again.json()["usage"]["used"] == 1


@pytest.mark.asyncio
async def test_old_login_context_wrong_owner_and_missing_payload_fail_before_attempt(full_settings):
    from contextlib import aclosing

    from tests.support.auth import login
    from tests.test_t62_background_ownership import _add_account, _login_user

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()
        missing = await client.post(
            f"{path}/actions/confirm_prompt", json={"expected_revision": before["project_revision"]}
        )
        assert missing.status_code == 422
        other = await _add_account(app, "t02-other@example.com")
        async with aclosing(await _login_user(app, other.email_normalized)) as other_client:
            assert (await confirm(other_client, project_id, before)).status_code == 404
        assert (await client.post("/api/auth/logout")).status_code == 204
        client.csrf_token = None
        await login(client)
        assert (await confirm(client, project_id, before)).status_code == 409
        fresh = (await client.get(path)).json()
        assert (
            fresh["prompt_preview"]["request_digest"] == before["prompt_preview"]["request_digest"]
        )
        assert (
            fresh["prompt_preview"]["command_context"]
            != before["prompt_preview"]["command_context"]
        )
        assert fresh["generation_job"] is None and visual.submit_calls == []


@pytest.mark.parametrize(
    "point",
    ["final_authorizations", "generation_jobs", "generations", "audit_events", "prepared_prompts"],
)
@pytest.mark.asyncio
async def test_final_transaction_fault_has_no_authorization_or_attempt_and_can_retry(
    full_settings, point
):
    from httpx import ASGITransport

    from tests.support.auth import AuthClient

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()
        operation = "UPDATE" if point == "prepared_prompts" else "INSERT"
        condition = "NEW.event_type='prompt.final_authorized'" if point == "audit_events" else "1"
        with sqlite3.connect(full_settings.database_path) as db:
            db.execute(
                f"CREATE TRIGGER t02_fault BEFORE {operation} ON {point} WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT,'controlled T02 failure'); END"
            )
        async with AuthClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://testserver",
            cookies=client.cookies,
        ) as raw:
            raw.csrf_token = client.csrf_token
            response = await confirm(raw, project_id, before)
        assert response.status_code == 500, response.text
        after = (await client.get(path)).json()
        assert (
            after["generation_job"] is None and after["prompt_preview"] == before["prompt_preview"]
        )
        assert after["intent"]["status"] == "accepted" and after["usage"]["used"] == 1
        assert (await client.get(f"/api/projects/{project_id}")).json()["generations"] == []
        assert visual.submit_calls == []
        with sqlite3.connect(full_settings.database_path) as db:
            db.execute("DROP TRIGGER t02_fault")
        # Retry the exact command after storage repair. New attempt is created once.
        visual.submit_error = RuntimeError("controlled remote unknown")
        retry = await confirm(client, project_id, before)
        assert retry.status_code == 200, retry.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        assert (await client.get(path)).json()["usage"]["used"] == 1


@pytest.mark.asyncio
async def test_reference_bytes_negative_prompt_and_output_match_reviewed_preview(full_settings):
    from app.adapters.base import ProviderOutcome
    from tests.test_v2_t40_prompt_review import png_bytes

    image = png_bytes(64, 64)
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, LLMStub(), visual, responder) as (app, client):
        project_id = await prepared(client, reference_bytes=image)
        await _drain(app)
        before = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        expected = before["prompt_preview"]["request"]
        result = await confirm(client, project_id, before)
        assert result.status_code == 200, result.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        actual = visual.submit_calls[0]
        assert actual.reference_bytes == image
        assert actual.reference_mime_type == "image/png"
        assert actual.brief.reference_asset_id == expected["references"][0]["asset_id"]
        assert actual.prompt == expected["prompt"]
        assert "禁止内容（Negative prompt）:\n价格标签；促销文案" in actual.prompt
        assert (actual.count, actual.image_size, actual.response_format) == (
            1,
            "1024x1024",
            "b64_json",
        )
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert view["usage"]["used"] == 1


@pytest.mark.parametrize(
    "changed", ["query", "approval", "reference", "slots", "revision", "snapshot"]
)
@pytest.mark.asyncio
async def test_final_basic_bindings_reject_stale_command_without_image_attempt(
    full_settings, changed
):
    from tests.test_v2_t40_prompt_review import png_bytes

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client, reference_bytes=png_bytes(64, 64))
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()
        calls = len(llm.role_calls_received)
        if changed == "query":
            app.state.settings.visual_base_url = "https://visual.invalid?new=secret-route"
        elif changed == "reference":
            ref = before["prompt_preview"]["request"]["references"][0]
            (
                full_settings.resolved_data_dir() / "references" / f"{ref['asset_id']}.png"
            ).write_bytes(png_bytes(64, 64, color=(8, 8, 8)))
        elif changed == "snapshot":
            before["prompt_preview"]["request_digest"] = "f" * 64
        else:
            with sqlite3.connect(full_settings.database_path) as db:
                if changed == "approval":
                    db.execute(
                        "UPDATE approvals SET revoked_at='2026-11-03' "
                        "WHERE approval_type='generation_prompt'"
                    )
                elif changed == "slots":
                    db.execute(
                        "UPDATE generation_slots SET released_at='2026-11-03' WHERE scope='project'"
                    )
                elif changed == "revision":
                    db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (project_id,))
        rejected = await confirm(client, project_id, before)
        assert rejected.status_code == 409, rejected.text
        after = (await client.get(path)).json()
        assert after["generation_job"] is None and after["usage"]["used"] == 1
        assert len(llm.role_calls_received) == calls and visual.submit_calls == []


@pytest.mark.asyncio
async def test_same_final_command_on_independent_http_apps_returns_one_attempt(full_settings):
    import asyncio

    from httpx import ASGITransport

    from app.adapters.base import ProviderOutcome
    from app.main import create_app
    from tests.support.adapters import build_test_registry
    from tests.support.auth import AuthClient
    from tests.test_v2_t40_prompt_review import png_bytes

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
                    confirm(client, project_id, view), confirm(second, project_id, view)
                )
            assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
            assert (
                results[0].json()["generation_job"]["id"]
                == results[1].json()["generation_job"]["id"]
            )
            gate.set()
            await _drain(app)
            await _drain(other_app)
            assert len(visual.submit_calls) == 1
            assert (await client.get(f"/api/projects/{project_id}/workflow")).json()["usage"][
                "used"
            ] == 1


@pytest.mark.asyncio
async def test_final_http_origin_csrf_and_anonymous_guards_apply_before_authorization(
    full_settings,
):
    from httpx import ASGITransport, AsyncClient

    async with _environment(full_settings, LLMStub(), VisualStub(), responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()
        preview = before["prompt_preview"]
        body = {
            "expected_revision": before["project_revision"],
            "payload": {
                "snapshot_id": preview["id"],
                "snapshot_digest": preview["request_digest"],
                "command_id": "44444444-4444-4444-8444-444444444444",
                "command_context": preview["command_context"],
            },
        }
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as anon:
            assert (await anon.post(f"{path}/actions/confirm_prompt", json=body)).status_code == 401
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver", cookies=client.cookies
        ) as raw:
            for headers in (
                {"Origin": "https://evil.invalid", "X-CSRF-Token": client.csrf_token},
                {"Origin": "https://testserver"},
            ):
                result = await raw.post(
                    f"{path}/actions/confirm_prompt", headers=headers, json=body
                )
                assert result.status_code == 403, result.text
        after = (await client.get(path)).json()
        assert after["prompt_preview"] == before["prompt_preview"]
        assert after["generation_job"] is None and after["usage"]["used"] == 1

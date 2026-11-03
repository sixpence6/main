from __future__ import annotations

import asyncio
from contextlib import aclosing

import pytest

from tests.support.adapters import LLMStub, VisualStub
from tests.support.t01_prompt import prepared, responder
from tests.test_t62_background_ownership import _add_account, _login_user
from tests.test_t65_generation_cycle import _drain, _environment


@pytest.mark.asyncio
async def test_preparation_waits_without_any_image_attempt(full_settings):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        response = await client.get(f"/api/projects/{project_id}/workflow")
        assert response.status_code == 200, response.text
        view = response.json()
        assert visual.submit_calls == [], "preparation must never submit an image"
        assert view["generation_job"] is None
        assert (await client.get(f"/api/projects/{project_id}")).json()["generations"] == []
        assert view["status"] == "needs_input"
        assert view["prompt_preview"]["status"] == "awaiting_confirmation"
        assert view["usage"]["used"] == 1


@pytest.mark.asyncio
async def test_reads_and_old_actions_cannot_authorize_or_recompile(full_settings):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        initial = (await client.get(path)).json()
        calls = len(llm.role_calls_received)
        preview = initial["prompt_preview"]
        assert preview["can_confirm"] is True
        assert preview["command_context"]
        request = preview["request"]
        assert request["negative_prompt"] == "价格标签；促销文案"
        assert "禁止内容（Negative prompt）:\n价格标签；促销文案" in request["prompt"]
        assert request["references"] == []
        assert request["output"] == {"count": 1, "size": "1024x1024", "response_format": "b64_json"}
        reviews = [
            r
            for r in llm.role_calls_received
            if r.payload["task"]["role"] == "output_reviewer"
            and r.payload["review_targets"][0].get("target_kind") == "prompt_plan"
        ]
        assert len(reviews) == 1
        reviewed = reviews[0].payload["review_targets"][0]["effective_request"]
        assert reviewed["prompt"] == request["prompt"]
        assert reviewed["negative_strategy"] == "combined-text-v1"
        import sqlite3

        # Storage boundary fails the request if a GET or old command tries to
        # manufacture approval, attempt, acceptance, or another waiting fact.
        with sqlite3.connect(full_settings.database_path) as db:
            for table in (
                "approvals",
                "prepared_prompts",
                "generation_jobs",
                "generations",
                "generation_usage",
                "generation_slots",
            ):
                for operation in ("INSERT", "UPDATE"):
                    db.execute(
                        f"CREATE TRIGGER readonly_{table}_{operation} BEFORE {operation} "
                        f"ON {table} BEGIN SELECT RAISE(ABORT, 'unexpected write'); END"
                    )
        for _ in range(3):
            current = (await client.get(path)).json()
            assert current["prompt_preview"] == preview
            assert current["generation_job"] is None
            assert current["usage"]["used"] == 1
            assert [
                current["usage"][f"{scope}_active"] for scope in ("personal", "instance", "project")
            ] == [1, 1, 1]
        for name in (
            "submit_generation",
            "retry_generation",
            "recover_generation",
            "start_new_generation",
            "resume_after_restart",
            "continue_workflow",
            "compile_prompt",
        ):
            response = await client.post(
                f"{path}/actions/{name}",
                json={"expected_revision": initial["project_revision"], "payload": {}},
            )
            assert response.status_code in (404, 409), (name, response.text)
        response = await client.post(
            f"/api/projects/{project_id}/generations",
            json={
                "request_id": "22222222-2222-4222-8222-222222222222",
                "confirmed_revision": initial["project_revision"],
            },
        )
        assert response.status_code == 409, response.text
        assert (await client.get(path)).json()["prompt_preview"] == preview
        assert visual.submit_calls == [] and visual.poll_calls == [] and visual.fetch_calls == []
        assert len(llm.role_calls_received) == calls


@pytest.mark.asyncio
async def test_preparation_replay_preserves_waiting_snapshot(full_settings):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()
        card = next(x for x in before["artifacts"] if x["kind"] == "final_brief")
        calls = len(llm.role_calls_received)
        body = {
            "expected_revision": before["project_revision"],
            "payload": {
                "artifact_id": card["id"],
                "artifact_digest": card["digest"],
                "requirement_revision": before["requirement_revision"],
                "request_id": "11111111-1111-4111-8111-111111111113",
            },
        }
        for _ in range(2):
            result = await client.post(f"{path}/actions/confirm_requirements", json=body)
            assert result.status_code == 200, result.text
            await _drain(app)
        after = (await client.get(path)).json()
        assert after["status"] == "needs_input"
        assert after["prompt_preview"] == before["prompt_preview"]
        assert len(llm.role_calls_received) == calls
        assert after["intent"]["id"] == before["intent"]["id"]
        assert after["usage"]["used"] == 1 and after["generation_job"] is None
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_two_independent_restarts_keep_waiting_without_calls(full_settings, tmp_path):
    import os
    import subprocess
    import sys

    from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR
    from tests.support.process_auth import ProcessSession, process_auth_env
    from tests.test_t01_process import free_port, stop_server, wait_for_health

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        expected = (await client.get(f"/api/projects/{project_id}/workflow")).json()
    log = tmp_path / "restart-calls.log"
    log.write_text("", encoding="utf-8")
    for _ in range(2):
        port = free_port()
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("LLM_", "VISUAL_", "DATABASE_", "DATA_DIR"))
        }
        env.update(
            DATA_DIR=str(full_settings.data_dir),
            WEB_DIST_DIR=str(full_settings.web_dist_dir),
            PYTHONPATH=os.pathsep.join(
                [str(MAIN_BACKEND_DIR), str(BACKEND_DIR), os.environ.get("PYTHONPATH", "")]
            ),
            LLM_ADAPTER="stub-llm",
            LLM_BASE_URL="https://llm.invalid",
            LLM_API_KEY="sk-test-only",
            LLM_MODEL="stub-model",
            LLM_VISION_MODEL="stub-vision",
            VISUAL_ADAPTER="stub-visual",
            VISUAL_BASE_URL="https://visual.invalid",
            VISUAL_API_KEY=full_settings.visual().api_key.get_secret_value(),
            VISUAL_MODEL="stub-model",
            VISUAL_IMAGE_SIZE="1024x1024",
            VISUAL_TIMEOUT_SECONDS=str(full_settings.visual().timeout_seconds),
            T01_CALL_LOG=str(log),
        )
        env.update(process_auth_env(full_settings, port))
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.support.t01_restart_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=BACKEND_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_for_health(process, port)
            session = ProcessSession(port)
            session.login()
            for _ in range(3):
                status, view = session.request("GET", f"/api/projects/{project_id}/workflow")
                assert status == 200, view
                assert {
                    k: v for k, v in view["prompt_preview"].items() if k != "command_context"
                } == {k: v for k, v in expected["prompt_preview"].items() if k != "command_context"}
                assert (
                    view["prompt_preview"]["command_context"]
                    != expected["prompt_preview"]["command_context"]
                )
                assert view["generation_job"] is None and view["usage"]["used"] == 1
                assert [
                    view["usage"][f"{scope}_active"]
                    for scope in ("personal", "instance", "project")
                ] == [1, 1, 1]
        finally:
            stop_server(process)
    assert log.read_text(encoding="utf-8") == ""
    assert visual.submit_calls == []


@pytest.mark.parametrize("interrupted_role", ["output_reviewer", "generation_engineer"])
@pytest.mark.asyncio
async def test_s07_recovery_returns_to_same_wait_not_image_execution(
    full_settings, interrupted_role
):
    from app.adapters.base import ProviderError

    fail = True

    def interrupted(request):
        nonlocal fail
        if (
            fail
            and request.payload["task"]["stage"] == "S07"
            and request.payload["task"]["role"] == interrupted_role
        ):
            fail = False
            raise ProviderError("controlled review interruption", evidence="not_sent")
        return responder(request)

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, interrupted) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()
        assert before["prompt_preview"] is None
        assert before["generation_job"] is None
        assert all(x["payload"] is None for x in before["artifacts"] if x["kind"] == "prompt_plan")
        response = await client.post(
            f"{path}/actions/continue_workflow",
            json={"expected_revision": before["project_revision"]},
        )
        assert response.status_code == 200, response.text
        recovered = response.json()
        assert recovered["prompt_preview"] is not None
        assert recovered["status"] == "needs_input"
        assert recovered["usage"]["used"] == 1
        assert recovered["generation_job"] is None and visual.submit_calls == []


@pytest.mark.asyncio
async def test_candidate_is_invisible_until_independent_review_finishes(full_settings):
    import asyncio

    from tests.support.t01_prompt import ReviewPause

    llm, visual = ReviewPause(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await asyncio.wait_for(llm.review_started.wait(), timeout=10)
        path = f"/api/projects/{project_id}/workflow"
        view = (await client.get(path)).json()
        assert view["prompt_preview"] is None
        candidates = [x for x in view["artifacts"] if x["kind"] == "prompt_plan"]
        assert len(candidates) == 1 and candidates[0]["status"] == "candidate"
        assert candidates[0]["payload"] is None
        assert view["generation_job"] is None and visual.submit_calls == []
        llm.release_review.set()
        await _drain(app)
        approved = (await client.get(path)).json()
        assert approved["prompt_preview"] is not None
        assert approved["prompt_preview"]["request"]["positive_prompt"]
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_requirement_change_hides_the_revoked_prompt(full_settings):
    from tests.support.t01_prompt import action

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()
        assert before["prompt_preview"] is not None
        await action(client, project_id, "revise_requirements", text="将商品改为两只杯子")
        after = (await client.get(path)).json()
        assert after["prompt_preview"] is None
        assert all(x["payload"] is None for x in after["artifacts"] if x["kind"] == "prompt_plan")
        assert any(
            x["approval_type"] == "generation_prompt" and x["revoked_at"]
            for x in after["approvals"]
        )
        assert visual.submit_calls == []


@pytest.mark.parametrize(
    "point", ["prepared_prompts", "approvals", "artifact_reviews", "audit_events", "workflow_runs"]
)
@pytest.mark.asyncio
async def test_waiting_approval_and_audit_write_faults_do_not_publish_candidates(
    full_settings, point, caplog
):
    import sqlite3

    # SQLite is a confirmed system boundary: inject storage failure, observe only HTTP.
    conditions = {
        "prepared_prompts": ("INSERT", "1"),
        "approvals": ("INSERT", "NEW.approval_type='generation_prompt'"),
        "artifact_reviews": (
            "INSERT",
            "EXISTS (SELECT 1 FROM artifacts WHERE id=NEW.artifact_id AND kind='prompt_plan')",
        ),
        "audit_events": ("INSERT", "NEW.event_type='prompt.prepared'"),
        "workflow_runs": ("UPDATE", "OLD.stage='S07' AND NEW.status='needs_input'"),
    }
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        operation, condition = conditions[point]
        with sqlite3.connect(full_settings.resolved_data_dir() / "app.db") as db:
            db.execute(
                f"CREATE TRIGGER t01_fault BEFORE {operation} ON {point} WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT, 'controlled T01 fault'); END"
            )
        project_id = await prepared(client)
        await _drain(app)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert view["prompt_preview"] is None and view["generation_job"] is None
        assert not any(x["approval_type"] == "generation_prompt" for x in view["approvals"])
        assert all(x["payload"] is None for x in view["artifacts"] if x["kind"] == "prompt_plan")
        assert view["usage"]["used"] == 1
        assert [
            view["usage"][f"{scope}_active"] for scope in ("personal", "instance", "project")
        ] == [1, 1, 1]
        assert visual.submit_calls == []
        assert "磨砂玻璃咖啡杯" not in caplog.text


# Lifecycle regressions at the new preparation boundary; legacy T62 tests remain unchanged.


@pytest.mark.asyncio
async def test_logout_keeps_preparation_owner_but_never_authorizes_an_image(full_settings):
    from tests.support.t01_prompt import ReviewPause, prepared, responder
    from tests.test_t65_generation_cycle import _drain, _environment

    llm, visual = ReviewPause(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, _admin):
        owner = await _add_account(app, "owner62@example.com")
        other = await _add_account(app, "other62@example.com")
        async with aclosing(await _login_user(app, owner.email_normalized)) as client:
            project_id = await prepared(client)
            await asyncio.wait_for(llm.review_started.wait(), timeout=10)
            assert (await client.post("/api/auth/logout")).status_code == 204
        async with aclosing(await _login_user(app, other.email_normalized)) as other_client:
            assert (
                await other_client.get(f"/api/projects/{project_id}/workflow")
            ).status_code == 404
        llm.release_review.set()
        await _drain(app)
        async with aclosing(await _login_user(app, owner.email_normalized)) as client:
            view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
            request = view["prompt_preview"]["request"]
            assert request["owner_account_id"] == owner.id
            assert request["operator_account_id"] == owner.id
            assert view["generation_job"] is None
            assert view["usage"]["used"] == 1
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_disable_is_rechecked_before_freezing_preparation_and_restore_does_not_resume(
    full_settings,
):
    from tests.support.t01_prompt import ReviewPause, prepared, responder
    from tests.test_t65_generation_cycle import _drain, _environment

    llm, visual = ReviewPause(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, admin):
        owner = await _add_account(app, "disabled62@example.com")
        async with aclosing(await _login_user(app, owner.email_normalized)) as client:
            project_id = await prepared(client)
            await asyncio.wait_for(llm.review_started.wait(), timeout=10)
            result = await admin.post(f"/api/admin/accounts/{owner.id}/disable")
            assert result.status_code == 200, result.text
            llm.release_review.set()
            await _drain(app)
            assert (await client.get(f"/api/projects/{project_id}/workflow")).status_code == 401
        calls = len(llm.role_calls_received)
        result = await admin.post(f"/api/admin/accounts/{owner.id}/restore")
        assert result.status_code == 200, result.text
        async with aclosing(await _login_user(app, owner.email_normalized)) as client:
            view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
            assert view["prompt_preview"] is None and view["generation_job"] is None
            assert all(
                x["payload"] is None for x in view["artifacts"] if x["kind"] == "prompt_plan"
            )
            assert view["usage"]["used"] == 1
        assert len(llm.role_calls_received) == calls
        assert visual.submit_calls == [] and visual.poll_calls == []


@pytest.mark.asyncio
async def test_preparation_identity_is_server_owned_not_client_execution_authorization(
    full_settings,
):
    from tests.support.t01_prompt import prepared, responder
    from tests.test_t65_generation_cycle import _drain, _environment

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, _admin):
        owner = await _add_account(app, "authref62@example.com")
        async with aclosing(await _login_user(app, owner.email_normalized)) as client:
            project_id = await prepared(client)
            await _drain(app)
            view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
            forged = await client.post(
                f"/api/projects/{project_id}/workflow/actions/submit_generation",
                json={
                    "expected_revision": view["project_revision"],
                    "owner_account_id": "forged-owner",
                    "is_admin": True,
                },
            )
            assert forged.status_code == 422, forged.text
            submitted = await client.post(
                f"/api/projects/{project_id}/workflow/actions/submit_generation",
                json={
                    "expected_revision": view["project_revision"],
                    "payload": {"authorization_ref": "forged-ref"},
                },
            )
            assert submitted.status_code == 409, submitted.text
            assert view["prompt_preview"]["request"]["owner_account_id"] == owner.id
            assert view["prompt_preview"]["request"]["operator_account_id"] == owner.id
            assert view["prompt_preview"]["can_confirm"] is True
            assert view["generation_job"] is None
        assert visual.submit_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidation", ["changed_bytes", "rejected_asset", "revoked_report"])
async def test_single_reference_is_frozen_and_changed_bytes_hide_the_preview(
    full_settings, invalidation
):
    import hashlib

    from tests.test_v2_t40_prompt_review import png_bytes

    image = png_bytes()
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client, reference_bytes=image)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        view = (await client.get(path)).json()
        assert view["prompt_preview"] is not None
        refs = view["prompt_preview"]["request"]["references"]
        assert len(refs) == 1
        assert refs[0]["sha256"] == hashlib.sha256(image).hexdigest()
        assert refs[0]["content_version"] == refs[0]["sha256"]
        assert refs[0]["usage"] == "product"
        assert refs[0]["mime_type"] == "image/png" and refs[0]["byte_size"] == len(image)
        # Fault at the external file boundary: stale reference content must fail closed.
        reference = full_settings.resolved_data_dir() / "references" / f"{refs[0]['asset_id']}.png"
        if invalidation == "changed_bytes":
            reference.write_bytes(png_bytes(color=(4, 5, 6)))
        else:
            import sqlite3

            with sqlite3.connect(full_settings.database_path) as db:
                if invalidation == "rejected_asset":
                    db.execute(
                        "UPDATE assets SET review_status='rejected' WHERE id=?",
                        (refs[0]["asset_id"],),
                    )
                else:
                    db.execute(
                        "UPDATE artifacts SET status='rejected' WHERE kind='reference_report'"
                    )
        stale = (await client.get(path)).json()
        assert stale["prompt_preview"] is None
        assert all(x["payload"] is None for x in stale["artifacts"] if x["kind"] == "prompt_plan")
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_preview_http_boundary_keeps_identity_owner_origin_and_csrf_guards(full_settings):
    from httpx import ASGITransport, AsyncClient

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        view = (await client.get(path)).json()
        calls = len(llm.role_calls_received)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as anon:
            assert (await anon.get(path)).status_code == 401
        account = await _add_account(app, "t01-other@example.com")
        async with aclosing(await _login_user(app, account.email_normalized)) as other:
            assert (await other.get(path)).status_code == 404
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver", cookies=client.cookies
        ) as raw:
            for headers in ({"Origin": "https://evil.invalid"}, {"Origin": "https://testserver"}):
                result = await raw.post(
                    f"{path}/actions/submit_generation",
                    headers=headers,
                    json={"expected_revision": view["project_revision"]},
                )
                assert result.status_code == 403, result.text
        assert (await client.get(path)).json()["prompt_preview"] == view["prompt_preview"]
        assert len(llm.role_calls_received) == calls and visual.submit_calls == []


@pytest.mark.asyncio
async def test_legacy_http_submit_has_no_authority_to_create_an_image_attempt(full_settings):
    from tests.test_t06_generation import create_confirmed_project, environment, generation_payload

    async with environment(full_settings) as (client, _registry, _llm, visual):
        project = await create_confirmed_project(client)
        result = await client.post(
            f"/api/projects/{project['id']}/generations", json=generation_payload(project)
        )
        assert result.status_code == 409, result.text
        assert result.json()["error"]["code"] == "FINAL_CONFIRMATION_REQUIRED"
        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        assert detail["generations"] == []
        view = (await client.get(f"/api/projects/{project['id']}/workflow")).json()
        assert view["generation_job"] is None and view["prompt_preview"] is None
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_precontract_unsent_history_cannot_resume_without_final_authorization(full_settings):
    from tests.test_v2_t17_restart_recovery import _seed_s08_async

    # Imported historical fixture, not a forged new authorization. Only HTTP is observed.
    seed = await _seed_s08_async(full_settings, pending_job=True)
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        path = f"/api/projects/{seed.project_id}/workflow"
        initial = (await client.get(path)).json()
        assert initial["generation_job"]["id"] == seed.job_id
        assert initial["generation_job"]["submit_attempted"] is False
        assert initial["prompt_preview"] is None
        response = await client.post(
            f"{path}/actions/recover_generation",
            json={"expected_revision": initial["project_revision"]},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "FINAL_CONFIRMATION_REQUIRED"
        after = (await client.get(path)).json()
        assert after["generation_job"] == initial["generation_job"]
        assert after["prompt_preview"] is None
        assert visual.submit_calls == [] and llm.role_calls_received == []


@pytest.mark.parametrize("changed", ["revoked_at", "rules_version", "requirement_snapshot_digest"])
@pytest.mark.asyncio
async def test_revoked_or_incompatible_prompt_approval_is_not_public(full_settings, changed):
    import sqlite3

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()
        approval_id = before["prompt_preview"]["request"]["prompt_approval_id"]
        # Storage-boundary invalidation fixture, not a private workflow helper.
        with sqlite3.connect(full_settings.database_path) as db:
            db.execute(f"UPDATE approvals SET {changed}=? WHERE id=?", ("invalidated", approval_id))
        after = (await client.get(path)).json()
        assert after["prompt_preview"] is None
        assert all(x["payload"] is None for x in after["artifacts"] if x["kind"] == "prompt_plan")
        assert after["generation_job"] is None and visual.submit_calls == []

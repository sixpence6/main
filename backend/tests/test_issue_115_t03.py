"""#115 T03: invalidated final commands, login context, disabled accounts."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import aclosing

import pytest
from pydantic import SecretStr

from tests.support.adapters import LLMStub, VisualStub
from tests.support.auth import login
from tests.support.t01_prompt import action, prepared
from tests.support.t02_prompt import (
    SubmissionObservedVisual,
    final_click,
    ready_project,
    responder,
)
from tests.test_t62_background_ownership import (
    _add_account,
    _job_state,
    _login_user,
    _wait_for_submit,
)
from tests.test_t65_generation_cycle import _drain, _environment
from tests.test_v2_t40_prompt_review import png_bytes

COMMAND_ID = "22222222-2222-4222-8222-222222222114"


async def _workflow(client, project_id: str) -> dict:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


def _confirm_body(view: dict, preview: dict, *, command_id: str = COMMAND_ID, context=None) -> dict:
    return {
        "expected_revision": view["project_revision"],
        "payload": {
            "snapshot_id": preview["id"],
            "snapshot_digest": preview["request_digest"],
            "command_id": command_id,
            "command_context": preview.get("command_context", "") if context is None else context,
        },
    }


async def _confirm(client, project_id: str, view: dict, preview: dict, **kwargs):
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/confirm_prompt",
        json=_confirm_body(view, preview, **kwargs),
    )


def _counts(settings, project_id: str) -> dict[str, int]:
    with sqlite3.connect(settings.database_path) as db:
        tables = ("generations", "generation_jobs", "final_authorizations")
        return {
            table: db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (project_id,)
            ).fetchone()[0]
            for table in tables
        }


def _rewrite_request(settings, project_id: str, mutate) -> None:
    with sqlite3.connect(settings.database_path) as db:
        row = db.execute(
            "SELECT id, request_json FROM prepared_prompts "
            "WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        request = json.loads(row[1])
        mutate(request)
        db.execute(
            "UPDATE prepared_prompts SET request_json=? WHERE id=?",
            (json.dumps(request, ensure_ascii=False), row[0]),
        )


def _mutate(settings, app, project_id: str, preview: dict, case: str) -> None:
    request = preview["request"]
    with sqlite3.connect(settings.database_path) as db:
        if case == "requirement_revision":
            db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (project_id,))
        elif case == "prompt_content":
            artifact_id = db.execute(
                "SELECT artifact_id FROM prepared_prompts WHERE id=?", (preview["id"],)
            ).fetchone()[0]
            row = db.execute(
                "SELECT payload_json FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
            payload = json.loads(row[0])
            payload["positive_prompt"] = payload.get("positive_prompt", "") + " 变更"
            db.execute(
                "UPDATE artifacts SET payload_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), artifact_id),
            )
        elif case == "approval_record":
            db.execute(
                "UPDATE approvals SET revoked_at='2026-11-03T00:00:00Z', "
                "revocation_reason='test' WHERE approval_type='generation_prompt'"
            )
        elif case == "reference_file":
            asset_id = request["references"][0]["asset_id"]
            path = settings.resolved_data_dir() / "references" / f"{asset_id}.png"
            path.write_bytes(png_bytes(96, 96, color=(9, 9, 9)))
        elif case == "reference_version":
            asset_id = request["references"][0]["asset_id"]
            db.execute("UPDATE assets SET sha256=? WHERE id=?", ("0" * 64, asset_id))
        elif case == "output_size":
            app.state.settings.visual_image_size = "1536x1024"
        elif case == "output_format":
            _rewrite_request(
                settings,
                project_id,
                lambda frozen: frozen["output"].__setitem__("response_format", "url"),
            )
        elif case == "output_count":
            _rewrite_request(
                settings,
                project_id,
                lambda frozen: frozen["output"].__setitem__("count", 2),
            )
        elif case == "service_model":
            app.state.settings.visual_model = "changed-model"
        elif case == "service_base_url":
            app.state.settings.visual_base_url = "https://visual.invalid/changed-route"
        elif case == "service_timeout":
            app.state.settings.visual_timeout_seconds = 9
        elif case == "service_credential":
            app.state.settings.visual_api_key = SecretStr("sk-test-visual-changed")
        else:  # pragma: no cover - guarded by parametrization
            raise AssertionError(case)


MATRIX = [
    ("requirement_revision", "REVISION_CONFLICT", None),
    ("prompt_content", "PROMPT_STALE", None),
    ("approval_record", "PROMPT_STALE", None),
    ("reference_file", "REFERENCE_UNAVAILABLE", "参考"),
    ("reference_version", "REFERENCE_UNAVAILABLE", "参考"),
    ("output_size", "PROMPT_STALE", None),
    ("output_format", "PROMPT_STALE", None),
    ("output_count", "PROMPT_STALE", None),
    ("service_model", "PROMPT_STALE", "服务配置"),
    ("service_base_url", "PROMPT_STALE", "服务配置"),
    ("service_timeout", "PROMPT_STALE", "服务配置"),
    ("service_credential", "PROMPT_STALE", "服务配置"),
]


@pytest.mark.parametrize(("case", "expected_code", "expected_label"), MATRIX)
@pytest.mark.asyncio
async def test_invalidation_matrix_rejects_old_final_command_without_attempt(
    full_settings, case, expected_code, expected_label
):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client, reference_bytes=png_bytes(640, 640))
        await _drain(app)
        view = await _workflow(client, project_id)
        preview = view["prompt_preview"]
        assert preview is not None and preview["can_confirm"] is True
        llm_calls = len(llm.role_calls_received)

        _mutate(full_settings, app, project_id, preview, case)
        rejected = await _confirm(client, project_id, view, preview)

        assert rejected.status_code == 409, rejected.text
        body = rejected.json()["error"]
        assert body["code"] == expected_code, body
        if expected_label is not None:
            assert expected_label in body["message"], body
        after = await _workflow(client, project_id)
        assert after["prompt_preview"] is None
        assert after["generation_job"] is None
        assert after["usage"]["used"] == 1
        assert all(value == 0 for value in _counts(full_settings, project_id).values())
        assert visual.submit_calls == []
        assert len(llm.role_calls_received) == llm_calls


@pytest.mark.asyncio
async def test_title_only_rename_keeps_effective_snapshot_and_attempt(full_settings):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        preview = before["prompt_preview"]

        renamed = await client.patch(f"/api/projects/{project_id}", json={"title": "只改标题"})
        assert renamed.status_code == 200, renamed.text
        after = await _workflow(client, project_id)
        assert after["project_revision"] == before["project_revision"]
        assert after["prompt_preview"]["id"] == preview["id"]
        assert after["prompt_preview"]["request_digest"] == preview["request_digest"]

        accepted = await _confirm(client, project_id, after, after["prompt_preview"])
        assert accepted.status_code == 200, accepted.text
        await _drain(app)
        final = await _workflow(client, project_id)
        assert final["usage"]["used"] == 1
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_same_account_relogin_reads_own_context_without_authorization_or_idle_refresh(
    full_settings,
):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        old_preview = before["prompt_preview"]
        assert (await client.post("/api/auth/logout")).status_code == 204
        client.csrf_token = None
        await login(client)
        with sqlite3.connect(full_settings.database_path) as db:
            idle_before_read = db.execute(
                "SELECT idle_expires_at FROM sessions WHERE revoked_at IS NULL"
            ).fetchone()[0]

        fresh = await _workflow(client, project_id)
        fresh_preview = fresh["prompt_preview"]
        assert fresh_preview is not None and fresh_preview["can_confirm"] is True
        assert fresh_preview["request_digest"] == old_preview["request_digest"]
        assert fresh_preview["command_context"] != old_preview["command_context"]
        assert _counts(full_settings, project_id)["final_authorizations"] == 0
        with sqlite3.connect(full_settings.database_path) as db:
            idle_after_read = db.execute(
                "SELECT idle_expires_at FROM sessions WHERE revoked_at IS NULL"
            ).fetchone()[0]
        assert idle_after_read == idle_before_read

        stale = await _confirm(client, project_id, before, old_preview)
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "FINAL_CONTEXT_CHANGED"
        assert _counts(full_settings, project_id)["final_authorizations"] == 0

        accepted = await _confirm(client, project_id, fresh, fresh_preview)
        assert accepted.status_code == 200, accepted.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        assert (await _workflow(client, project_id))["usage"]["used"] == 1


LIFECYCLE = ["expired", "revoked", "epoch_changed", "account_disabled"]


@pytest.mark.parametrize("case", LIFECYCLE)
@pytest.mark.asyncio
async def test_old_login_lifecycle_payload_is_rejected_before_authorization(full_settings, case):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = await _workflow(client, project_id)
        preview = view["prompt_preview"]
        with sqlite3.connect(full_settings.database_path) as db:
            if case == "expired":
                db.execute(
                    "UPDATE sessions SET idle_expires_at='2000-01-01T00:00:00Z' "
                    "WHERE revoked_at IS NULL"
                )
            elif case == "revoked":
                db.execute(
                    "UPDATE sessions SET revoked_at='2026-11-03T00:00:00Z', "
                    "revoked_reason='test_revoked' WHERE revoked_at IS NULL"
                )
            elif case == "epoch_changed":
                db.execute("UPDATE accounts SET session_epoch=session_epoch+1")
            elif case == "account_disabled":
                db.execute(
                    "UPDATE accounts SET is_disabled=1, "
                    "disabled_at='2026-11-03T00:00:00Z'"
                )

        rejected = await _confirm(client, project_id, view, preview)

        assert rejected.status_code == 401, rejected.text
        assert rejected.json()["error"]["code"] == "AUTH_REQUIRED"
        assert _counts(full_settings, project_id)["final_authorizations"] == 0
        assert visual.submit_calls == []


@pytest.mark.parametrize("case", ["wrong_project", "wrong_run", "wrong_intent"])
@pytest.mark.asyncio
async def test_foreign_snapshot_binding_is_rejected(full_settings, case):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = await _workflow(client, project_id)
        preview = view["prompt_preview"]
        if case == "wrong_project":
            other_id = (await client.post("/api/projects", json={})).json()["id"]
            column, value = "project_id", other_id
        elif case == "wrong_run":
            column, value = "run_id", str(uuid.uuid4())
        else:
            column, value = "intent_id", str(uuid.uuid4())
        with sqlite3.connect(full_settings.database_path) as db:
            db.execute(
                f"UPDATE prepared_prompts SET {column}=? WHERE id=?", (value, preview["id"])
            )

        rejected = await _confirm(client, project_id, view, preview)

        assert rejected.status_code == 409, rejected.text
        body = rejected.json()["error"]
        if case == "wrong_intent":
            assert body["code"] == "PROMPT_STALE", body
            assert "准备意图" in body["message"], body
        assert _counts(full_settings, project_id)["final_authorizations"] == 0
        assert visual.submit_calls == []


@pytest.mark.parametrize("case", ["approval_revoked", "service_changed", "revision_changed"])
@pytest.mark.asyncio
async def test_idempotent_replay_rechecks_current_gates(full_settings, case):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = await _workflow(client, project_id)
        preview = view["prompt_preview"]
        first = await _confirm(client, project_id, view, preview)
        assert first.status_code == 200, first.text
        await _drain(app)
        assert len(visual.submit_calls) == 1

        body_view = view
        if case == "approval_revoked":
            with sqlite3.connect(full_settings.database_path) as db:
                db.execute(
                    "UPDATE approvals SET revoked_at='2026-11-03T00:00:00Z', "
                    "revocation_reason='test' WHERE approval_type='generation_prompt'"
                )
        elif case == "service_changed":
            app.state.settings.visual_model = "changed-model"
        else:
            with sqlite3.connect(full_settings.database_path) as db:
                db.execute("UPDATE projects SET revision=revision+1 WHERE id=?", (project_id,))
            body_view = await _workflow(client, project_id)

        replay = await _confirm(client, project_id, body_view, preview)

        assert replay.status_code == 409, replay.text
        assert len(visual.submit_calls) == 1
        after = await _workflow(client, project_id)
        assert after["usage"]["used"] == 1
        assert after["generation_job"]["id"] == first.json()["generation_job"]["id"]


@pytest.mark.asyncio
async def test_disabled_account_blocks_next_image_call_without_attempt(full_settings):
    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, responder) as (app, admin):
        owner = await _add_account(app, "t03-next@example.com")
        async with aclosing(await _login_user(app, owner.email_normalized)) as owner_client:
            project_id = await prepared(owner_client)
            await _drain(app)
            view = await _workflow(owner_client, project_id)
            preview = view["prompt_preview"]

            disabled = await admin.post(f"/api/admin/accounts/{owner.id}/disable")
            assert disabled.status_code == 200, disabled.text
            rejected = await _confirm(owner_client, project_id, view, preview)

        assert rejected.status_code == 401, rejected.text
        assert rejected.json()["error"]["code"] == "AUTH_REQUIRED"
        assert _counts(full_settings, project_id)["final_authorizations"] == 0
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_disabled_account_blocks_publish_and_keeps_delivery_unpublished(full_settings):
    from app.adapters.base import ProviderOutcome

    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual, responder) as (app, admin):
        owner = await _add_account(app, "t03-publish@example.com")
        async with aclosing(await _login_user(app, owner.email_normalized)) as owner_client:
            project_id = await prepared(owner_client)
            await _drain(app)
            view = await _workflow(owner_client, project_id)
            accepted = await _confirm(owner_client, project_id, view, view["prompt_preview"])
            assert accepted.status_code == 200, accepted.text
            await _drain(app)
            await action(owner_client, project_id, "prepare_delivery")
            await action(owner_client, project_id, "approve_delivery")
            ready = await _workflow(owner_client, project_id)
            assert ready["delivery"]["status"] == "approved"

            disabled = await admin.post(f"/api/admin/accounts/{owner.id}/disable")
            assert disabled.status_code == 200, disabled.text
            rejected = await owner_client.post(
                f"/api/projects/{project_id}/workflow/actions/publish_delivery",
                json={"expected_revision": ready["project_revision"]},
            )

        assert rejected.status_code == 401, rejected.text
        with sqlite3.connect(full_settings.database_path) as db:
            status = db.execute(
                "SELECT status FROM deliveries WHERE project_id=?", (project_id,)
            ).fetchone()[0]
        assert status == "approved"
        published_dir = full_settings.resolved_data_dir() / "published"
        assert not published_dir.exists() or list(published_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_late_provider_result_after_disable_does_not_write_back(full_settings):
    import asyncio

    from app.adapters.base import ProviderOutcome

    gate = asyncio.Event()
    visual = SubmissionObservedVisual(
        submit_outcome=ProviderOutcome(state="pending", remote_task_id="remote-115"),
        gate=gate,
    )
    async with _environment(full_settings, LLMStub(), visual, responder) as (app, admin):
        owner = await _add_account(app, "t03-late@example.com")
        async with aclosing(await _login_user(app, owner.email_normalized)) as owner_client:
            setup = await ready_project(app, owner_client)
            started = await final_click(owner_client, setup)
            assert started.status_code == 200, started.text
            await _wait_for_submit(visual)

            disabled = await admin.post(f"/api/admin/accounts/{owner.id}/disable")
            assert disabled.status_code == 200, disabled.text
            gate.set()
            await _drain(app)

        job, generation = await _job_state(app, setup["project_id"])
        assert job.status == "unknown", job.status
        assert job.error_code == "ACCOUNT_DISABLED", job.error_code
        assert generation.remote_task_id == "remote-115"
        assert generation.result_asset_id is None
        assert generation.error_code == "ACCOUNT_DISABLED"
        assert visual.poll_calls == []
        with sqlite3.connect(full_settings.database_path) as db:
            assert (
                db.execute(
                    "SELECT COUNT(*) FROM deliveries WHERE project_id=?",
                    (setup["project_id"],),
                ).fetchone()[0]
                == 0
            )


@pytest.mark.asyncio
async def test_published_history_readable_but_old_command_not_executable(full_settings):
    from app.adapters.base import ProviderOutcome

    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = await _workflow(client, project_id)
        preview = view["prompt_preview"]
        accepted = await _confirm(client, project_id, view, preview)
        assert accepted.status_code == 200, accepted.text
        await _drain(app)
        await action(client, project_id, "prepare_delivery")
        await action(client, project_id, "approve_delivery")
        await action(client, project_id, "publish_delivery")

        app.state.settings.visual_base_url = "https://visual.invalid/changed-route"
        after = await _workflow(client, project_id)
        assert after["prompt_preview"] is None
        prompt_plan = next(x for x in after["artifacts"] if x["kind"] == "prompt_plan")
        assert prompt_plan["payload"] is not None

        replay = await _confirm(client, project_id, view, preview)
        assert replay.status_code == 409, replay.text
        assert replay.json()["error"]["code"] == "PROMPT_STALE"
        assert len(visual.submit_calls) == 1

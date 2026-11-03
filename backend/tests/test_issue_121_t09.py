from __future__ import annotations

import hashlib
import sqlite3
import uuid

import pytest

from app.adapters.base import ProviderOutcome
from tests.support.adapters import LLMStub, VisualStub
from tests.support.t01_prompt import action
from tests.support.t02_prompt import responder as preparation_responder
from tests.test_issue_118_t06 import _revision_responder
from tests.test_issue_120_t08 import _stage
from tests.test_t65_generation_cycle import _drain, _environment
from tests.test_v2_t40_prompt_review import png_bytes


def _rows(path: str, sql: str, params: tuple = ()) -> list[dict]:
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql, params).fetchall()]


async def _workflow(client, project_id: str) -> dict:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


def _message_body(content: str, revision: int, request_id: str) -> dict:
    return {
        "content": content,
        "expected_revision": revision,
        "request_id": request_id,
    }


async def _post_message(client, project_id: str, body: dict):
    return await client.post(f"/api/projects/{project_id}/messages", json=body)


def _gate_responder(mapping: dict[str, dict]):
    base = _revision_responder("杯身加金色把手")

    def responder(request):
        task = request.payload["task"]
        if task["role"] == "conversation_router":
            text = request.payload["input_data"]["user_text"]
            spec = mapping.get(text, {"kind": "conversation"})
            return {
                "role": "conversation_router",
                "status": "ready",
                "data": {
                    "kind": spec["kind"],
                    "reference_usage": spec.get("reference_usage", "none"),
                    "evidence_ids": [],
                },
                "issues": [],
                "questions": [],
            }
        response = base(request)
        if task["role"] == "requirements_consultant" and response.get("data") is not None:
            data = response["data"]
            asset_id = request.payload["input_data"].get("reference_asset_id")
            data["reference_asset_id"] = asset_id
            data["reference_usage"] = (
                request.payload["input_data"].get("reference_usage")
                if asset_id
                else "none"
            )
            if "桌面近景" in request.payload["input_data"].get("user_text", ""):
                data["composition"] = "桌面近景构图，主体居中"
        if task["role"] == "visual_director" and response.get("data") is not None:
            source = request.payload["input_data"].get("brief_draft") or {}
            if source.get("composition"):
                response["data"]["composition"] = source["composition"]
        return response

    return responder


def _approved_final_brief(view: dict) -> dict:
    return next(
        item
        for item in view["artifacts"]
        if item["kind"] == "final_brief" and item["status"] == "approved"
    )


async def _advance_to_preview(client, project_id: str) -> dict:
    for name in ("compose_brief", "plan_creative", "plan_visual", "finalize_requirements"):
        await action(client, project_id, name)
    view = await _workflow(client, project_id)
    card = _approved_final_brief(view)
    await action(
        client,
        project_id,
        "confirm_requirements",
        payload={
            "artifact_id": card["id"],
            "artifact_digest": card["digest"],
            "requirement_revision": view["requirement_revision"],
            "request_id": str(uuid.uuid4()),
        },
    )
    return await _workflow(client, project_id)


@pytest.mark.asyncio
async def test_gate_journey_text_reference_repair_and_frozen_publish(full_settings):
    from tests.test_issue_116_t04 import _ledger

    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    image = png_bytes(color=(11, 22, 33))
    digest = hashlib.sha256(image).hexdigest()
    first_text = "请把这张图作为商品参考，做一张商品主图，文案为 WARM MORNING"
    chat_text = "这张图里的杯子是什么材质？"
    rename_text = "春天陶瓷杯主图"
    repair_text = "把杯身把手改成金色"
    revision_text = "请把构图改成桌面近景，其他保持已确认内容"
    responder = _gate_responder(
        {
            first_text: {"kind": "requirements_change", "reference_usage": "product"},
            chat_text: {"kind": "conversation"},
            repair_text: {"kind": "prompt_change"},
            revision_text: {"kind": "requirements_change"},
        }
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = (await client.post("/api/projects", json={})).json()["id"]
        staged = (await _stage(client, project_id, image)).json()
        asset_id = staged["asset"]["id"]
        sent = await _post_message(
            client,
            project_id,
            {
                **_message_body(first_text, 0, str(uuid.uuid4())),
                "attachment_asset_id": asset_id,
            },
        )
        assert sent.status_code == 200, sent.text
        await _drain(app)
        view = await _advance_to_preview(client, project_id)
        await _drain(app)
        view = await _workflow(client, project_id)
        assert view["stage"] == "S07" and view["status"] == "needs_input"
        preview = view["prompt_preview"]
        assert preview is not None and preview["can_confirm"] is True
        request = preview["request"]
        assert request["prompt"]
        assert request["negative_prompt"]
        assert request["output"] == {
            "count": 1,
            "size": "1024x1024",
            "response_format": "b64_json",
        }
        assert request["references"][0]["asset_id"] == asset_id
        assert request["references"][0]["usage"] == "product"
        assert request["references"][0]["content_version"] == digest
        first_intent = view["intent"]
        first_used = view["usage"]["used"]
        first_ledger = _ledger(full_settings.database_path)
        assert first_ledger["intents"] == 1
        assert first_ledger["usage"] == 1
        assert first_ledger["slots"] == 3
        assert first_ledger["live_slots"] == 3
        assert visual.submit_calls == []

        first_read = await _workflow(client, project_id)
        second_read = await _workflow(client, project_id)
        assert first_read["prompt_preview"]["id"] == preview["id"]
        assert second_read["prompt_preview"]["id"] == preview["id"]
        assert second_read["prompt_preview"]["request_digest"] == preview["request_digest"]
        assert visual.submit_calls == []

        chatted = await _post_message(
            client,
            project_id,
            _message_body(chat_text, view["project_revision"], str(uuid.uuid4())),
        )
        assert chatted.status_code == 200, chatted.text
        chat_body = chatted.json()
        chat_view = chat_body["workflow"]
        assert chat_body["user_message"]["turn_kind"] == "conversation"
        assert chat_view["project_revision"] == view["project_revision"]
        assert chat_view["requirement_revision"] == view["requirement_revision"]
        assert chat_view["prompt_preview"]["id"] == preview["id"]
        assert chat_view["intent"]["id"] == first_intent["id"]
        assert chat_view["usage"]["used"] == first_used
        assert _ledger(full_settings.database_path) == first_ledger
        assert visual.submit_calls == []

        renamed = await client.patch(
            f"/api/projects/{project_id}", json={"title": rename_text}
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["title"] == rename_text
        renamed_view = await _workflow(client, project_id)
        assert renamed_view["project_revision"] == view["project_revision"]
        assert renamed_view["prompt_preview"]["id"] == preview["id"]
        assert _ledger(full_settings.database_path) == first_ledger

        repaired = await _post_message(
            client,
            project_id,
            _message_body(repair_text, renamed_view["project_revision"], str(uuid.uuid4())),
        )
        assert repaired.status_code == 200, repaired.text
        repaired_view = repaired.json()["workflow"]
        repaired_preview = repaired_view["prompt_preview"]
        assert repaired_view["stage"] == "S07"
        assert repaired_view["status"] == "needs_input"
        assert repaired_preview["id"] != preview["id"]
        assert repaired_preview["request_digest"] != preview["request_digest"]
        assert "金色" in repaired_preview["request"]["prompt"]
        assert repaired_view["intent"]["id"] == first_intent["id"]
        assert repaired_view["intent"]["status"] == "accepted"
        assert repaired_view["usage"]["used"] == first_used
        assert repaired_view["project_revision"] == view["project_revision"]
        assert _rows(
            full_settings.database_path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (preview["id"],),
        ) == [{"status": "invalidated"}]
        assert _rows(
            full_settings.database_path,
            "SELECT COUNT(*) AS n FROM generation_jobs WHERE project_id=?",
            (project_id,),
        )[0]["n"] == 0
        assert visual.submit_calls == []

        revised = await _post_message(
            client,
            project_id,
            _message_body(
                revision_text, repaired_view["project_revision"], str(uuid.uuid4())
            ),
        )
        assert revised.status_code == 200, revised.text
        revised_view = revised.json()["workflow"]
        assert revised_view["stage"] == "S04" and revised_view["status"] == "active"
        assert revised_view["project_revision"] > repaired_view["project_revision"]
        assert revised_view["requirement_revision"] > repaired_view["requirement_revision"]
        assert revised_view["prompt_preview"] is None
        stale_click = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_prompt",
            json={
                "expected_revision": revised_view["project_revision"],
                "payload": {
                    "snapshot_id": preview["id"],
                    "snapshot_digest": preview["request_digest"],
                    "command_id": "88888888-8888-4888-8888-888888888121",
                    "command_context": preview["command_context"],
                },
            },
        )
        assert stale_click.status_code == 409, stale_click.text
        assert visual.submit_calls == []
        assert revised_view["intent"]["id"] == first_intent["id"]
        assert revised_view["intent"]["status"] == "abandoned"
        assert revised_view["usage"]["used"] == first_used
        revised_ledger = _ledger(full_settings.database_path)
        assert revised_ledger["intents"] == 1
        assert revised_ledger["usage"] == 1
        assert revised_ledger["live_slots"] == 0
        assert _rows(
            full_settings.database_path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (repaired_preview["id"],),
        ) == [{"status": "invalidated"}]
        assert _rows(
            full_settings.database_path,
            "SELECT COUNT(*) AS n FROM generation_slots WHERE intent_id=? "
            "AND released_at IS NULL",
            (first_intent["id"],),
        )[0]["n"] == 0
        assert visual.submit_calls == []

        view = await _workflow(client, project_id)
        for name in ("plan_creative", "plan_visual", "finalize_requirements"):
            await action(client, project_id, name)
        view = await _workflow(client, project_id)
        card = _approved_final_brief(view)
        await action(
            client,
            project_id,
            "confirm_requirements",
            payload={
                "artifact_id": card["id"],
                "artifact_digest": card["digest"],
                "requirement_revision": view["requirement_revision"],
                "request_id": str(uuid.uuid4()),
            },
        )
        await _drain(app)
        final_view = await _workflow(client, project_id)
        assert final_view["prompt_preview"] is not None
        final_preview = final_view["prompt_preview"]
        assert final_view["intent"]["id"] != first_intent["id"]
        assert final_view["intent"]["status"] == "accepted"
        assert final_view["usage"]["used"] > first_used
        final_request = final_preview["request"]
        assert final_request["references"][0]["content_version"] == digest
        assert visual.submit_calls == []

        command_id = "33333333-3333-4333-8333-333333333121"
        confirm_body = {
            "expected_revision": final_view["project_revision"],
            "payload": {
                "snapshot_id": final_preview["id"],
                "snapshot_digest": final_preview["request_digest"],
                "command_id": command_id,
                "command_context": final_preview["command_context"],
            },
        }
        confirmed = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_prompt",
            json=confirm_body,
        )
        assert confirmed.status_code == 200, confirmed.text
        job_id = confirmed.json()["generation_job"]["id"]
        replayed = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_prompt",
            json=confirm_body,
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["generation_job"]["id"] == job_id
        await _drain(app)
        assert len(visual.submit_calls) == 1
        submitted = visual.submit_calls[0]
        assert submitted.prompt == final_request["prompt"]
        assert "禁止内容（Negative prompt）" in submitted.prompt
        assert final_request["negative_prompt"] in submitted.prompt
        assert submitted.image_size == final_request["output"]["size"]
        assert submitted.count == final_request["output"]["count"]
        assert submitted.response_format == final_request["output"]["response_format"]
        assert submitted.reference_bytes == image
        assert submitted.reference_mime_type == "image/png"
        assert submitted.reference_path is not None
        after = await _workflow(client, project_id)
        assert after["generation_job"]["id"] == job_id
        assert after["usage"]["used"] == final_view["usage"]["used"]

        await action(client, project_id, "prepare_delivery")
        await action(client, project_id, "approve_delivery")
        published = await action(client, project_id, "publish_delivery")
        assert published["delivery"]["status"] == "published"
        version = published["versions"][-1]
        assert version["generation_job_id"] == job_id
        assert version["prompt"]["positive_prompt"] == final_request["positive_prompt"]
        assert version["prompt"]["negative_prompt"] == final_request["negative_prompt"]
        image_response = await client.get(f"/api/assets/{published['delivery']['asset_id']}")
        assert image_response.status_code == 200
        assert published["usage"]["project_active"] == 0


@pytest.mark.asyncio
async def test_gate_candidate_waits_for_independent_review_and_two_restarts(
    full_settings, tmp_path
):
    import asyncio
    import os
    import subprocess
    import sys

    from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR
    from tests.support.process_auth import ProcessSession, process_auth_env
    from tests.support.t01_prompt import ReviewPause, prepared
    from tests.test_t01_process import free_port, stop_server, wait_for_health

    llm, visual = ReviewPause(), VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (app, client):
        project_id = await prepared(client)
        await asyncio.wait_for(llm.review_started.wait(), timeout=10)
        during = await _workflow(client, project_id)
        assert during["prompt_preview"] is None
        candidates = [item for item in during["artifacts"] if item["kind"] == "prompt_plan"]
        assert len(candidates) == 1
        assert candidates[0]["status"] == "candidate"
        assert candidates[0]["payload"] is None
        assert during["generation_job"] is None
        assert "confirm_prompt" not in {
            item["id"] for item in during["available_actions"]
        }
        assert visual.submit_calls == []
        llm.release_review.set()
        await _drain(app)
        expected = await _workflow(client, project_id)
        assert expected["prompt_preview"] is not None
        assert expected["prompt_preview"]["request"]["positive_prompt"]
        assert visual.submit_calls == []

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
                    key: value
                    for key, value in view["prompt_preview"].items()
                    if key != "command_context"
                } == {
                    key: value
                    for key, value in expected["prompt_preview"].items()
                    if key != "command_context"
                }
                assert (
                    view["prompt_preview"]["command_context"]
                    != expected["prompt_preview"]["command_context"]
                )
                assert view["generation_job"] is None
                assert view["usage"]["used"] == 1
                assert [
                    view["usage"][f"{scope}_active"]
                    for scope in ("personal", "instance", "project")
                ] == [1, 1, 1]
        finally:
            stop_server(process)
    assert log.read_text(encoding="utf-8") == ""
    assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_gate_preparation_double_click_and_same_request_id_keep_one_acceptance(
    full_settings,
):
    import asyncio

    from tests.test_issue_116_t04 import (
        _ledger,
        _prepare_project,
        _requirements_payload,
    )

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (app, client):
        project_id = await _prepare_project(client)
        body = await _requirements_payload(client, project_id)
        url = f"/api/projects/{project_id}/workflow/actions/confirm_requirements"
        results = await asyncio.gather(
            client.post(url, json=body),
            client.post(url, json=body),
        )
        codes = sorted(result.status_code for result in results)
        assert codes[0] == 200 and codes[1] in (200, 409), [r.text for r in results]
        await _drain(app)
        ledger = _ledger(full_settings.database_path)
        assert ledger["intents"] == 1
        assert ledger["usage"] == 1
        assert ledger["slots"] == 3
        assert ledger["live_slots"] == 3
        assert visual.submit_calls == []
        view = await _workflow(client, project_id)
        assert view["prompt_preview"] is not None
        assert view["usage"]["used"] == 1

        conflicting = await _requirements_payload(client, project_id, revision_delta=1)
        rejected = await client.post(url, json=conflicting)
        assert rejected.status_code == 409, rejected.text
        assert _ledger(full_settings.database_path) == ledger
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_gate_final_dual_connection_and_lost_response_keep_one_submission(
    full_settings,
):
    import asyncio

    from httpx import ASGITransport

    from app.main import create_app
    from tests.support.adapters import build_test_registry
    from tests.support.auth import AuthClient
    from tests.support.t01_prompt import prepared
    from tests.test_issue_114_t02 import confirm

    gate = asyncio.Event()
    visual = VisualStub(
        gate=gate,
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        ),
    )
    llm = LLMStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (app, client):
        other_app = create_app(
            settings=full_settings, registry=build_test_registry(llm=llm, visual=visual)
        )
        async with other_app.router.lifespan_context(other_app):
            project_id = await prepared(client)
            await _drain(app)
            view = await _workflow(client, project_id)
            preview = view["prompt_preview"]
            assert preview is not None
            async with AuthClient(
                transport=ASGITransport(app=other_app),
                base_url="https://testserver",
                cookies=client.cookies,
                csrf_token=client.csrf_token,
            ) as second:
                results = await asyncio.gather(
                    confirm(
                        client,
                        project_id,
                        view,
                        command_id="44444444-4444-4444-8444-444444444121",
                    ),
                    confirm(
                        second,
                        project_id,
                        view,
                        command_id="44444444-4444-4444-8444-444444444121",
                    ),
                )
            assert [result.status_code for result in results] == [200, 200], [
                result.text for result in results
            ]
            job_id = results[0].json()["generation_job"]["id"]
            assert results[1].json()["generation_job"]["id"] == job_id
            gate.set()
            await _drain(app)
            await _drain(other_app)
            assert len(visual.submit_calls) == 1
            submitted = visual.submit_calls[0]
            assert submitted.prompt == preview["request"]["prompt"]
            assert submitted.image_size == preview["request"]["output"]["size"]
            assert submitted.count == preview["request"]["output"]["count"]
            current = await _workflow(client, project_id)
            assert current["generation_job"]["id"] == job_id
            assert current["usage"]["used"] == view["usage"]["used"]

            replayed = await confirm(
                client,
                project_id,
                view,
                command_id="44444444-4444-4444-8444-444444444121",
            )
            assert replayed.status_code == 200, replayed.text
            assert replayed.json()["generation_job"]["id"] == job_id
            await _drain(app)
            assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_gate_stale_login_wrong_owner_and_wrong_project_fail_before_attempt(
    full_settings,
):
    from contextlib import aclosing

    from tests.support.auth import login
    from tests.support.t01_prompt import prepared
    from tests.test_issue_114_t02 import confirm
    from tests.test_issue_116_t04 import _ledger
    from tests.test_t62_background_ownership import _add_account, _login_user

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = await _workflow(client, project_id)
        preview = before["prompt_preview"]
        before_ledger = _ledger(full_settings.database_path)
        assert before_ledger["authorizations"] == 0
        assert before_ledger["jobs"] == 0

        missing = await client.post(
            f"{path}/actions/confirm_prompt",
            json={"expected_revision": before["project_revision"]},
        )
        assert missing.status_code == 422

        other = await _add_account(app, "t121-other@example.com")
        async with aclosing(await _login_user(app, other.email_normalized)) as other_client:
            assert (await confirm(other_client, project_id, before)).status_code == 404

        unstarted = (await client.post("/api/projects", json={})).json()["id"]
        wrong_project = await client.post(
            f"/api/projects/{unstarted}/workflow/actions/confirm_prompt",
            json={
                "expected_revision": 0,
                "payload": {
                    "snapshot_id": preview["id"],
                    "snapshot_digest": preview["request_digest"],
                    "command_id": str(uuid.uuid4()),
                    "command_context": preview["command_context"],
                },
            },
        )
        assert wrong_project.status_code in {404, 409}, wrong_project.text

        assert (await client.post("/api/auth/logout")).status_code == 204
        client.csrf_token = None
        await login(client)
        assert (await confirm(client, project_id, before)).status_code == 409
        fresh = await _workflow(client, project_id)
        assert (
            fresh["prompt_preview"]["request_digest"]
            == before["prompt_preview"]["request_digest"]
        )
        assert (
            fresh["prompt_preview"]["command_context"]
            != before["prompt_preview"]["command_context"]
        )
        assert fresh["generation_job"] is None
        assert visual.submit_calls == []
        after_ledger = _ledger(full_settings.database_path)
        assert after_ledger["intents"] == before_ledger["intents"] == 1
        assert after_ledger["usage"] == before_ledger["usage"] == 1
        assert after_ledger["slots"] == before_ledger["slots"] == 3
        assert after_ledger["authorizations"] == 0
        assert after_ledger["jobs"] == 0
        assert after_ledger["generations"] == 0


@pytest.mark.parametrize(
    "point",
    [
        "final_authorizations",
        "generation_jobs",
        "generations",
        "audit_events",
        "prepared_prompts",
    ],
)
@pytest.mark.asyncio
async def test_gate_final_write_fault_rolls_back_and_retry_submits_once(
    full_settings, point
):
    from httpx import ASGITransport

    from tests.support.auth import AuthClient
    from tests.support.t01_prompt import prepared
    from tests.test_issue_114_t02 import confirm
    from tests.test_issue_116_t04 import _ledger

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = await _workflow(client, project_id)
        preview = before["prompt_preview"]
        command_id = "55555555-5555-4555-8555-555555555121"
        operation = "UPDATE" if point == "prepared_prompts" else "INSERT"
        condition = (
            "NEW.event_type='prompt.final_authorized'" if point == "audit_events" else "1"
        )
        with sqlite3.connect(full_settings.database_path) as db:
            db.execute(
                f"CREATE TRIGGER t121_fault BEFORE {operation} ON {point} "
                f"WHEN {condition} BEGIN SELECT RAISE(ABORT, 'controlled T121 failure'); END"
            )
        payload = {
            "expected_revision": before["project_revision"],
            "payload": {
                "snapshot_id": preview["id"],
                "snapshot_digest": preview["request_digest"],
                "command_id": command_id,
                "command_context": preview["command_context"],
            },
        }
        async with AuthClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://testserver",
            cookies=client.cookies,
        ) as raw:
            raw.csrf_token = client.csrf_token
            failed = await raw.post(f"{path}/actions/confirm_prompt", json=payload)
        assert failed.status_code == 500, failed.text
        after = await _workflow(client, project_id)
        ledger = _ledger(full_settings.database_path)
        assert after["generation_job"] is None
        assert after["prompt_preview"]["id"] == preview["id"]
        assert after["prompt_preview"]["request_digest"] == preview["request_digest"]
        assert after["intent"]["status"] == "accepted"
        assert after["usage"]["used"] == 1
        assert ledger["authorizations"] == 0
        assert ledger["jobs"] == 0
        assert ledger["generations"] == 0
        assert visual.submit_calls == []

        with sqlite3.connect(full_settings.database_path) as db:
            db.execute("DROP TRIGGER t121_fault")
        retried = await confirm(client, project_id, before, command_id=command_id)
        assert retried.status_code == 200, retried.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        final_view = await _workflow(client, project_id)
        assert final_view["generation_job"]["id"] == retried.json()["generation_job"]["id"]
        assert final_view["usage"]["used"] == 1


@pytest.mark.asyncio
async def test_gate_abandon_releases_slots_keeps_usage_and_never_claims_remote_cancel(
    full_settings,
):
    import json

    from tests.support.t01_prompt import prepared

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        preview = before["prompt_preview"]
        intent = before["intent"]
        command_id = "66666666-6666-4666-8666-666666666121"
        payload = {
            "snapshot_id": preview["id"],
            "snapshot_digest": preview["request_digest"],
            "command_id": command_id,
        }
        abandoned = await action(client, project_id, "abandon_generation", payload=payload)
        assert abandoned["stage"] == "S07"
        assert abandoned["status"] == "blocked"
        assert abandoned["issue"]["code"] == "PROMPT_ABANDONED"
        assert abandoned["prompt_preview"] is None
        assert abandoned["intent"]["id"] == intent["id"]
        assert abandoned["intent"]["status"] == "abandoned"
        assert abandoned["requirement_revision"] == before["requirement_revision"]
        assert abandoned["usage"]["used"] == 1
        assert visual.submit_calls == []
        path = full_settings.database_path
        assert _rows(
            path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (preview["id"],),
        ) == [{"status": "invalidated"}]
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM generation_slots "
            "WHERE intent_id=? AND released_at IS NULL",
            (intent["id"],),
        )[0]["n"] == 0
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM generation_jobs WHERE project_id=?",
            (project_id,),
        )[0]["n"] == 0
        audits = _rows(
            path,
            "SELECT correlation_id,detail_json FROM audit_events "
            "WHERE event_type='generation_prompt_abandoned'",
        )
        assert len(audits) == 1
        detail = json.loads(audits[0]["detail_json"])
        assert audits[0]["correlation_id"] == command_id
        assert detail["intent_id"] == intent["id"]
        assert detail["snapshot_id"] == preview["id"]
        assert detail["remote_cancelled"] is False
        assert detail["image_attempt_exists"] is False

        replay = await action(client, project_id, "abandon_generation", payload=payload)
        assert replay["intent"]["status"] == "abandoned"
        assert replay["usage"]["used"] == 1
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM audit_events "
            "WHERE event_type='generation_prompt_abandoned'",
        )[0]["n"] == 1
        assert visual.submit_calls == []


@pytest.mark.parametrize(
    "busy_kind,expected_code",
    [("recovery", "PROJECT_BUSY"), ("filing", "ACTIVE_GENERATION_EXISTS")],
)
@pytest.mark.asyncio
async def test_gate_busy_message_is_rejected_without_queue_or_model_call(
    full_settings, busy_kind, expected_code
):
    from datetime import UTC, datetime

    from tests.support.t01_prompt import prepared

    llm, visual = LLMStub(), VisualStub()
    async with _environment(full_settings, llm, visual, preparation_responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        path = full_settings.database_path
        now = datetime.now(UTC).isoformat()
        if busy_kind == "recovery":
            with sqlite3.connect(path) as db:
                db.execute(
                    "UPDATE workflow_runs SET recovery_claim_id=?, recovery_claimed_at=? "
                    "WHERE project_id=?",
                    (str(uuid.uuid4()), now, project_id),
                )
        else:
            run_id = _rows(
                path,
                "SELECT id FROM workflow_runs WHERE project_id=?",
                (project_id,),
            )[0]["id"]
            with sqlite3.connect(path) as db:
                db.execute(
                    "INSERT INTO generation_jobs (id, project_id, run_id, intent_id, "
                    "request_id, request_digest, confirmed_snapshot_digest, status, "
                    "submit_attempted, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', 1, ?, ?)",
                    (
                        str(uuid.uuid4()),
                        project_id,
                        run_id,
                        before["intent"]["id"],
                        str(uuid.uuid4()),
                        "a" * 64,
                        "b" * 64,
                        now,
                        now,
                    ),
                )
        messages_before = _rows(
            path,
            "SELECT COUNT(*) AS n FROM messages WHERE project_id=?",
            (project_id,),
        )[0]["n"]
        calls_before = len(llm.role_calls_received)
        response = await _post_message(
            client,
            project_id,
            _message_body(
                "请继续解释一下", before["project_revision"], str(uuid.uuid4())
            ),
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == expected_code
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM messages WHERE project_id=?",
            (project_id,),
        )[0]["n"] == messages_before
        assert len(llm.role_calls_received) == calls_before
        assert visual.submit_calls == []
        after = await _workflow(client, project_id)
        assert after["prompt_preview"]["id"] == before["prompt_preview"]["id"]
        assert after["usage"]["used"] == 1


def test_gate_unknown_submit_after_crash_is_never_resubmitted(full_settings):
    import asyncio

    from tests.support.process_auth import process_session
    from tests.test_issue_117_t05 import (
        _action,
        _boot,
        _confirm_or_crash,
        _count_log,
        _log_path,
        _prepare_project,
        _project_rows,
        _stop,
        _wait_for_exit,
        _workflow,
    )

    log = _log_path(full_settings, "t121-unknown")
    process = None
    try:
        process, port = _boot(full_settings, "crash-after-submit-mark", log)
        session = process_session(port)
        project_id, view = asyncio.run(_prepare_project(session))
        _confirm_or_crash(
            session,
            project_id,
            view,
            "77777777-7777-4777-8777-777777777121",
        )
        _wait_for_exit(process, log, "crash:after-submit-mark")
        process = None
        rows = _project_rows(full_settings, project_id)
        assert rows["jobs"][0]["submit_attempted"] == 1
        assert _count_log(log, "visual:") == 0

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, blocked = _workflow(session, project_id)
        assert status == 200, blocked
        assert blocked["status"] == "blocked"
        assert blocked["issue"]["code"] == "GENERATION_UNKNOWN"
        assert blocked["generation_job"]["status"] == "unknown"
        assert blocked["generation_job"]["submit_attempted"] is True
        status, verified = _action(
            session, project_id, "verify_generation", blocked["project_revision"]
        )
        assert status == 200, verified
        status, recovered = _action(
            session, project_id, "recover_generation", blocked["project_revision"]
        )
        assert status == 200, recovered
        assert _count_log(log, "visual:") == 0
        rows = _project_rows(full_settings, project_id)
        assert rows["jobs"][0]["status"] == "unknown"
        assert len(rows["authorizations"]) == 1
    finally:
        _stop(process)


def test_gate_0015_upgrade_preserves_history_without_inventing_attachments(settings):
    from alembic import command

    from tests.test_v3_p1_t12_legacy_compat import _alembic_config

    config = _alembic_config(settings)
    command.upgrade(config, "0014")
    now = "2026-01-01T00:00:00.000Z"
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "INSERT INTO projects ("
            "id,title,revision,brief_status,product_description,purpose,style,composition,"
            "text_content,prompt,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("project-1", "旧项目", 1, "draft", "旧商品", "", "", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO messages ("
            "id,project_id,sequence_no,role,content,project_revision,turn_kind,created_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            ("message-1", "project-1", 1, "user", "旧消息", 1, "conversation", now),
        )
    command.upgrade(config, "head")
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0015",)
        assert db.execute(
            "SELECT content,turn_kind FROM messages WHERE id='message-1'"
        ).fetchall() == [("旧消息", "conversation")]
        assert db.execute("SELECT count(*) FROM message_attachments").fetchone() == (0,)
    command.downgrade(config, "0014")
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute(
            "SELECT content FROM messages WHERE id='message-1'"
        ).fetchall() == [("旧消息",)]
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='message_attachments'"
            ).fetchall()
            == []
        )

from __future__ import annotations

import sqlite3
import uuid
from contextlib import aclosing
from datetime import UTC, datetime

import pytest

from tests.support.adapters import LLMStub, VisualStub
from tests.support.t01_prompt import action, prepared
from tests.support.t02_prompt import responder as preparation_responder
from tests.test_issue_118_t06 import _budget, _intent, _live_slots, _revision_responder
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


async def _workflow(client, project_id: str) -> dict:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


def _conversation_responder(mapping: dict[str, dict], base=None):
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
        return (base or preparation_responder)(request)

    return responder


def _message_body(content: str, revision: int, request_id: str) -> dict:
    return {
        "content": content,
        "expected_revision": revision,
        "request_id": request_id,
    }


async def _post_message(client, project_id: str, body: dict):
    return await client.post(f"/api/projects/{project_id}/messages", json=body)


@pytest.mark.asyncio
async def test_conversation_does_not_create_requirement_or_intent_and_is_idempotent(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    content = "你好，请解释一下现在的方案"
    responder = _conversation_responder({content: {"kind": "conversation"}})
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        assert before["stage"] == "S07" and before["status"] == "needs_input"
        assert before["prompt_preview"] is not None
        assert before["intent"]["status"] == "accepted"
        assert before["usage"]["used"] == 1
        preview_id = before["prompt_preview"]["id"]
        intent_id = before["intent"]["id"]
        renamed = await client.patch(
            f"/api/projects/{project_id}", json={"title": "新的展示标题"}
        )
        assert renamed.status_code == 200, renamed.text
        renamed_view = await _workflow(client, project_id)
        assert renamed_view["project_revision"] == before["project_revision"]
        assert renamed_view["requirement_revision"] == before["requirement_revision"]
        assert renamed_view["intent"]["id"] == intent_id
        assert renamed_view["prompt_preview"]["id"] == preview_id
        request_id = str(uuid.uuid4())
        path = full_settings.database_path
        message_count = _rows(
            path,
            "SELECT COUNT(*) AS n FROM messages WHERE project_id=?",
            (project_id,),
        )[0]["n"]
        classifier_calls = len(
            [
                call
                for call in llm.role_calls_received
                if call.payload["task"]["role"] == "conversation_router"
            ]
        )

        response = await _post_message(
            client,
            project_id,
            _message_body(content, before["project_revision"], request_id),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["user_message"]["turn_kind"] == "conversation"
        assert body["assistant_message"]["turn_kind"] == "conversation"
        assert body["user_message"]["request_id"] == request_id
        assert body["assistant_message"]["request_id"] == request_id
        assert "需求" in body["assistant_message"]["content"]
        after = body["workflow"]
        assert after["project_revision"] == before["project_revision"]
        assert after["requirement_revision"] == before["requirement_revision"]
        assert after["intent"]["id"] == intent_id
        assert after["intent"]["status"] == "accepted"
        assert after["prompt_preview"]["id"] == preview_id
        assert after["usage"]["used"] == 1
        assert _budget(path, project_id)["used"] == 1
        assert _live_slots(path, intent_id) == 3
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM messages WHERE project_id=?",
            (project_id,),
        )[0]["n"] == message_count + 2
        assert _rows(
            path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (preview_id,),
        ) == [{"status": "awaiting_confirmation"}]
        assert visual.submit_calls == []

        # Same stable request id and same body returns the original exchange,
        # without another classifier call or another message pair.
        replay = await _post_message(
            client,
            project_id,
            _message_body(content, before["project_revision"], request_id),
        )
        assert replay.status_code == 200, replay.text
        replayed = replay.json()
        assert replayed["user_message"]["id"] == body["user_message"]["id"]
        assert replayed["assistant_message"]["id"] == body["assistant_message"]["id"]
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM messages WHERE project_id=?",
            (project_id,),
        )[0]["n"] == message_count + 2
        assert len(
            [
                call
                for call in llm.role_calls_received
                if call.payload["task"]["role"] == "conversation_router"
            ]
        ) == classifier_calls + 1

        conflict = await _post_message(
            client,
            project_id,
            _message_body("同一个请求标识不能改内容", before["project_revision"], request_id),
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error"]["code"] == "COMMAND_ID_CONFLICT"

        for follow_up in ("先暂缓，等我确认", "好的，继续"):
            deferred = await _post_message(
                client,
                project_id,
                _message_body(follow_up, before["project_revision"], str(uuid.uuid4())),
            )
            assert deferred.status_code == 200, deferred.text
            deferred_body = deferred.json()
            assert deferred_body["workflow"]["project_revision"] == before["project_revision"]
            assert (
                deferred_body["workflow"]["requirement_revision"]
                == before["requirement_revision"]
            )
            assert deferred_body["workflow"]["intent"]["id"] == intent_id
            assert deferred_body["workflow"]["prompt_preview"]["id"] == preview_id
            assert deferred_body["workflow"]["usage"]["used"] == 1
            assert deferred_body["user_message"]["turn_kind"] == "conversation"
            assert deferred_body["assistant_message"]["turn_kind"] == "conversation"

        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM audit_events "
            "WHERE event_type='message_send' AND result='success'",
        )[0]["n"] >= 1

        # Reading the workflow never advances revisions or role calls.
        read_calls = len(llm.role_calls_received)
        read_view = await _workflow(client, project_id)
        assert read_view["project_revision"] == before["project_revision"]
        assert read_view["prompt_preview"]["id"] == preview_id
        assert len(llm.role_calls_received) == read_calls


@pytest.mark.asyncio
async def test_s07_requirements_change_settles_old_intent_and_invalidates_preview(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    content = "请把杯身改成哑光黑色，其他保持已确认内容"
    responder = _conversation_responder({content: {"kind": "requirements_change"}})
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        old_intent = before["intent"]
        old_preview = before["prompt_preview"]
        path = full_settings.database_path

        response = await _post_message(
            client,
            project_id,
            _message_body(content, before["project_revision"], str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        after = body["workflow"]
        assert after["stage"] == "S04" and after["status"] == "active"
        assert after["requirement_revision"] > before["requirement_revision"]
        assert after["project_revision"] > before["project_revision"]
        assert after["prompt_preview"] is None
        assert after["intent"]["id"] == old_intent["id"]
        assert after["intent"]["status"] == "abandoned"
        assert after["usage"]["used"] == 1
        assert body["user_message"]["turn_kind"] == "workflow"
        assert "需求版本" in body["assistant_message"]["content"]
        assert _intent(path, project_id)["status"] == "abandoned"
        assert _live_slots(path, old_intent["id"]) == 0
        assert _rows(
            path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (old_preview["id"],),
        ) == [{"status": "invalidated"}]
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
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_s07_prompt_change_rebuilds_candidate_without_new_intent_or_usage(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    content = "把杯身把手改成金色"
    responder = _conversation_responder(
        {content: {"kind": "prompt_change"}}, base=_revision_responder(content)
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        old_intent = before["intent"]
        old_preview = before["prompt_preview"]
        path = full_settings.database_path
        reviewer_calls = len(
            [
                call
                for call in llm.role_calls_received
                if call.payload["task"]["role"] == "output_reviewer"
                and call.payload["task"]["stage"] == "S07"
            ]
        )

        response = await _post_message(
            client,
            project_id,
            _message_body(content, before["project_revision"], str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        after = body["workflow"]
        assert after["stage"] == "S07" and after["status"] == "needs_input"
        preview = after["prompt_preview"]
        assert preview is not None and preview["id"] != old_preview["id"]
        assert content in preview["request"]["prompt"]
        assert after["project_revision"] == before["project_revision"]
        assert after["requirement_revision"] == before["requirement_revision"]
        assert after["intent"]["id"] == old_intent["id"]
        assert after["intent"]["request_id"] == old_intent["request_id"]
        assert after["intent"]["status"] == "accepted"
        assert after["usage"]["used"] == 1
        assert _budget(path, project_id)["used"] == 2
        assert _rows(
            path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (old_preview["id"],),
        ) == [{"status": "invalidated"}]
        assert _rows(
            path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (preview["id"],),
        ) == [{"status": "awaiting_confirmation"}]
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM generation_jobs WHERE project_id=?",
            (project_id,),
        )[0]["n"] == 0
        assert body["user_message"]["turn_kind"] == "prompt"
        assert visual.submit_calls == []
        assert len(
            [
                call
                for call in llm.role_calls_received
                if call.payload["task"]["role"] == "output_reviewer"
                and call.payload["task"]["stage"] == "S07"
            ]
        ) == reviewer_calls + 1


@pytest.mark.asyncio
async def test_reference_usage_change_from_message_settles_preview_and_revises_requirements(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    content = "参考图只参考风格，不要复制商品外观"
    responder = _conversation_responder(
        {content: {"kind": "reference_usage_change", "reference_usage": "style"}}
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client, reference_bytes=png_bytes(64, 64))
        await _drain(app)
        before = await _workflow(client, project_id)
        assert before["stage"] == "S07" and before["status"] == "needs_input"
        old_intent = before["intent"]
        old_preview = before["prompt_preview"]
        assert old_preview is not None
        path = full_settings.database_path

        response = await _post_message(
            client,
            project_id,
            _message_body(content, before["project_revision"], str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        after = body["workflow"]
        assert after["stage"] == "S03" and after["status"] == "active"
        assert after["requirement_revision"] > before["requirement_revision"]
        assert after["prompt_preview"] is None
        assert after["intent"]["id"] == old_intent["id"]
        assert after["intent"]["status"] == "abandoned"
        assert after["usage"]["used"] == 1
        assert body["user_message"]["turn_kind"] == "workflow"
        assert "参考图用途" in body["assistant_message"]["content"]
        assert _live_slots(path, old_intent["id"]) == 0
        assert _rows(
            path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (old_preview["id"],),
        ) == [{"status": "invalidated"}]
        reports = [
            item
            for item in after["artifacts"]
            if item["kind"] == "reference_report" and item["status"] == "approved"
        ]
        assert reports and reports[-1]["payload"]["reference_usage"] == "style"
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_s03_clarification_uses_same_message_and_records_requirement_revision(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub()
    requirements_calls = {"count": 0}

    def base_responder(request):
        task = request.payload["task"]
        if task["role"] == "requirements_consultant":
            requirements_calls["count"] += 1
            if requirements_calls["count"] == 1:
                return {
                    "role": "requirements_consultant",
                    "status": "needs_input",
                    "data": None,
                    "issues": [
                        {
                            "code": "MISSING_INPUT",
                            "path": "brief.product_description",
                            "detail": "请补充杯身颜色",
                            "evidence_ids": [],
                        }
                    ],
                    "questions": ["杯身需要什么颜色？"],
                }
        return preparation_responder(request)

    content = "杯身是白色"
    responder = _conversation_responder(
        {content: {"kind": "clarification"}}, base=base_responder
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project = (await client.post("/api/projects", json={})).json()
        project_id = project["id"]
        await action(client, project_id, "submit_request", text="做一张商品图，文案为 WARM MORNING")
        pending = await action(client, project_id, "compose_brief")
        assert pending["stage"] == "S03"
        assert pending["status"] == "needs_input"
        before = await _workflow(client, project_id)
        path = full_settings.database_path
        messages_before = _rows(
            path,
            "SELECT COUNT(*) AS n FROM messages WHERE project_id=?",
            (project_id,),
        )[0]["n"]

        response = await _post_message(
            client,
            project_id,
            _message_body(content, before["project_revision"], str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        after = body["workflow"]
        assert after["stage"] == "S04" and after["status"] == "active"
        assert after["requirement_revision"] > before["requirement_revision"]
        assert after["project_revision"] > before["project_revision"]
        assert body["user_message"]["turn_kind"] == "workflow"
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM messages WHERE project_id=?",
            (project_id,),
        )[0]["n"] > messages_before
        assert _rows(
            path,
            "SELECT COUNT(*) AS n FROM generation_intents WHERE project_id=?",
            (project_id,),
        )[0]["n"] == 0
        assert visual.submit_calls == []


@pytest.mark.parametrize(
    ("busy_kind", "expected_code"),
    [("recovery", "PROJECT_BUSY"), ("job", "ACTIVE_GENERATION_EXISTS")],
)
@pytest.mark.asyncio
async def test_busy_workflow_rejects_new_message_without_queueing(
    full_settings, busy_kind, expected_code
):
    llm = LLMStub()
    visual = VisualStub()
    content = "请继续解释一下"
    responder = _conversation_responder({content: {"kind": "conversation"}})
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        path = full_settings.database_path
        now = datetime.now(UTC).isoformat()
        if busy_kind == "recovery":
            _execute(
                path,
                "UPDATE workflow_runs SET recovery_claim_id=?, recovery_claimed_at=? "
                "WHERE project_id=?",
                (str(uuid.uuid4()), now, project_id),
            )
        else:
            run = _rows(
                path, "SELECT id FROM workflow_runs WHERE project_id=?", (project_id,)
            )[0]
            _execute(
                path,
                "INSERT INTO generation_jobs (id, project_id, run_id, intent_id, "
                "request_id, request_digest, confirmed_snapshot_digest, status, "
                "submit_attempted, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', 1, ?, ?)",
                (
                    str(uuid.uuid4()),
                    project_id,
                    run["id"],
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
            _message_body(content, before["project_revision"], str(uuid.uuid4())),
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


@pytest.mark.asyncio
async def test_message_entry_enforces_identity_owner_origin_csrf_and_request_id(
    full_settings,
):
    from httpx import ASGITransport, AsyncClient

    llm = LLMStub()
    visual = VisualStub()
    content = "你好"
    responder = _conversation_responder({content: {"kind": "conversation"}})
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        path = f"/api/projects/{project_id}/messages"
        body = _message_body(content, before["project_revision"], str(uuid.uuid4()))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as anon:
            anonymous = await anon.post(path, json=body)
            assert anonymous.status_code == 401, anonymous.text

        other = await _add_account(app, "t07-other@example.com")
        async with aclosing(await _login_user(app, other.email_normalized)) as other_client:
            other_view = await other_client.get(f"/api/projects/{project_id}/workflow")
            assert other_view.status_code == 404, other_view.text
            foreign = await other_client.post(path, json=body)
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
                guarded = await raw.post(path, headers=headers, json=body)
                assert guarded.status_code == 403, guarded.text

        invalid = await client.post(
            path,
            json={**body, "request_id": "not-a-uuid"},
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"

        blank = await client.post(
            path,
            json={"content": "   ", "expected_revision": before["project_revision"]},
        )
        assert blank.status_code == 422, blank.text
        assert blank.json()["error"]["code"] == "VALIDATION_ERROR"

        after = await _workflow(client, project_id)
        assert after["project_revision"] == before["project_revision"]
        assert after["prompt_preview"]["id"] == before["prompt_preview"]["id"]
        assert visual.submit_calls == []

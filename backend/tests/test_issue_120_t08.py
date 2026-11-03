from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import aclosing

import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

from app.adapters.base import ProviderOutcome
from tests.support.adapters import LLMStub, VisualStub
from tests.support.t01_prompt import prepared
from tests.support.t02_prompt import preparation_responder
from tests.test_issue_119_t07 import (
    _message_body,
    _post_message,
    _rows,
    _workflow,
)
from tests.test_t62_background_ownership import _add_account, _login_user
from tests.test_t65_generation_cycle import _drain, _environment
from tests.test_v2_t40_prompt_review import png_bytes
from tests.test_v3_p1_t12_legacy_compat import _alembic_config


def _attachment_responder(mapping: dict[str, dict]):
    """Wrap conversation classification and align the draft with message usage."""

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
        response = preparation_responder(request)
        if task["role"] == "requirements_consultant" and response.get("data") is not None:
            data = response["data"]
            asset_id = request.payload["input_data"].get("reference_asset_id")
            data["reference_asset_id"] = asset_id
            data["reference_usage"] = (
                request.payload["input_data"].get("reference_usage")
                if asset_id
                else "none"
            )
        return response

    return responder


async def _stage(client, project_id: str, image: bytes):
    return await client.post(
        f"/api/projects/{project_id}/message-attachments",
        files={"file": ("reference.png", image, "image/png")},
    )


@pytest.mark.asyncio
async def test_staging_is_separate_from_business_reference_and_message_history_keeps_snapshot(
    full_settings,
):
    llm, visual = LLMStub(), VisualStub()
    image = png_bytes()
    digest = hashlib.sha256(image).hexdigest()
    content = "请把这张图作为商品参考，做一张商品主图"
    responder = _attachment_responder(
        {content: {"kind": "requirements_change", "reference_usage": "product"}}
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project = (await client.post("/api/projects", json={})).json()
        project_id = project["id"]

        discarded = (await _stage(client, project_id, png_bytes(color=(1, 2, 3)))).json()
        discarded_id = discarded["asset"]["id"]
        dropped = await client.delete(
            f"/api/projects/{project_id}/message-attachments/{discarded_id}"
        )
        assert dropped.status_code == 200, dropped.text
        assert (await client.get(discarded["preview_url"])).status_code == 404
        detail = (await client.get(f"/api/projects/{project_id}")).json()
        assert detail["project"]["revision"] == 0
        assert detail["project"]["reference_asset"] is None
        assert detail["messages"] == []

        staged_response = await _stage(client, project_id, image)
        assert staged_response.status_code == 200, staged_response.text
        staged = staged_response.json()
        asset_id = staged["asset"]["id"]
        assert staged["content_version"] == digest
        assert staged["sha256"] == digest
        assert staged["preview_url"] == f"/api/assets/{asset_id}"

        preview = await client.get(staged["preview_url"])
        assert preview.status_code == 200, preview.text
        assert preview.content == image

        detail = (await client.get(f"/api/projects/{project_id}")).json()
        assert detail["project"]["reference_asset"] is None
        assert detail["project"]["revision"] == 0
        assert detail["messages"] == []
        rows = _rows(
            full_settings.database_path,
            "SELECT reference_asset_id FROM projects WHERE id=?",
            (project_id,),
        )
        assert rows[0]["reference_asset_id"] is None

        body = {
            **_message_body(content, 0, str(uuid.uuid4())),
            "attachment_asset_id": asset_id,
        }
        response = await _post_message(client, project_id, body)
        assert response.status_code == 200, response.text
        payload = response.json()
        user = payload["user_message"]
        assert user["attachment"]["asset_id"] == asset_id
        assert user["attachment"]["usage"] == "product"
        assert user["attachment"]["content_version"] == digest
        assert payload["workflow"]["project_revision"] > 0

        detail = (await client.get(f"/api/projects/{project_id}")).json()
        assert detail["project"]["reference_asset"]["id"] == asset_id
        attached = [m for m in detail["messages"] if m["attachment"] is not None]
        assert len(attached) == 1
        assert attached[0]["attachment"]["usage"] == "product"

        revision = (await client.get(f"/api/projects/{project_id}")).json()["project"]["revision"]
        removed = await client.delete(
            f"/api/projects/{project_id}/reference?expected_revision={revision}"
        )
        assert removed.status_code == 200, removed.text
        detail = (await client.get(f"/api/projects/{project_id}")).json()
        assert detail["project"]["reference_asset"] is None
        attached = [m for m in detail["messages"] if m["attachment"] is not None]
        assert len(attached) == 1
        assert attached[0]["id"] == user["id"]
        assert attached[0]["attachment"]["content_version"] == digest
        assert (await client.get(staged["preview_url"])).status_code == 200
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_ambiguous_attachment_waits_for_usage_then_promotes_without_inventing_revision(
    full_settings,
):
    llm, visual = LLMStub(), VisualStub()
    image = png_bytes()
    first = "这张图先看一下"
    second = "这张图作为风格参考"
    responder = _attachment_responder(
        {
            first: {"kind": "conversation"},
            second: {"kind": "reference_usage_change", "reference_usage": "style"},
        }
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project = (await client.post("/api/projects", json={})).json()
        project_id = project["id"]
        staged = (await _stage(client, project_id, image)).json()
        asset_id = staged["asset"]["id"]

        first_response = await _post_message(
            client,
            project_id,
            {
                **_message_body(first, 0, str(uuid.uuid4())),
                "attachment_asset_id": asset_id,
            },
        )
        assert first_response.status_code == 200, first_response.text
        first_payload = first_response.json()
        first_classifier = [
            call
            for call in llm.role_calls_received
            if call.payload["task"]["role"] == "conversation_router"
        ][-1]
        assert (
            first_classifier.payload["input_data"]["has_reference_attachment"] is True
        )
        assert first_payload["workflow"]["stage"] == "S01"
        assert first_payload["workflow"]["status"] == "active"
        assert first_payload["workflow"]["project_revision"] == 0
        assert first_payload["workflow"]["prompt_preview"] is None
        assert first_payload["workflow"]["intent"] is None
        assert first_payload["user_message"]["attachment"]["usage"] == "none"

        detail = (await client.get(f"/api/projects/{project_id}")).json()
        assert detail["project"]["reference_asset"] is None
        assert len(detail["messages"]) == 2
        assert all(m["turn_kind"] == "conversation" for m in detail["messages"])

        second_response = await _post_message(
            client,
            project_id,
            _message_body(second, 0, str(uuid.uuid4())),
        )
        assert second_response.status_code == 200, second_response.text
        second_payload = second_response.json()
        second_classifier = [
            call
            for call in llm.role_calls_received
            if call.payload["task"]["role"] == "conversation_router"
        ][-1]
        assert (
            second_classifier.payload["input_data"]["has_reference_attachment"] is False
        )
        assert second_payload["workflow"]["project_revision"] == 1
        assert second_payload["workflow"]["stage"] in {"S03", "S04"}
        detail = (await client.get(f"/api/projects/{project_id}")).json()
        assert detail["project"]["reference_asset"]["id"] == asset_id
        attachment = _rows(
            full_settings.database_path,
            "SELECT * FROM message_attachments WHERE asset_id=?",
            (asset_id,),
        )[0]
        assert attachment["usage"] == "style"
        assert attachment["message_id"] == first_payload["user_message"]["id"]
        assert (
            _rows(
                full_settings.database_path,
                "SELECT turn_kind FROM messages WHERE id=?",
                (first_payload["user_message"]["id"],),
            )[0]["turn_kind"]
            == "workflow"
        )
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_s07_attachment_replacement_invalidates_old_preview_and_binds_usage(
    full_settings,
):
    llm, visual = LLMStub(), VisualStub()
    image = png_bytes(color=(2, 3, 4))
    content = "把这张图作为风格参考，其他需求保持"
    responder = _attachment_responder(
        {content: {"kind": "requirements_change", "reference_usage": "style"}}
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        before = await _workflow(client, project_id)
        old_intent = before["intent"]
        old_preview = before["prompt_preview"]
        staged = (await _stage(client, project_id, image)).json()
        asset_id = staged["asset"]["id"]
        response = await _post_message(
            client,
            project_id,
            {
                **_message_body(content, before["project_revision"], str(uuid.uuid4())),
                "attachment_asset_id": asset_id,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        after = payload["workflow"]
        assert after["stage"] in {"S03", "S04"}
        assert after["project_revision"] > before["project_revision"]
        assert after["requirement_revision"] > before["requirement_revision"]
        assert after["prompt_preview"] is None
        assert after["intent"]["id"] == old_intent["id"]
        assert after["intent"]["status"] == "abandoned"
        assert _rows(
            full_settings.database_path,
            "SELECT status FROM prepared_prompts WHERE id=?",
            (old_preview["id"],),
        ) == [{"status": "invalidated"}]
        attachment = _rows(
            full_settings.database_path,
            "SELECT * FROM message_attachments WHERE asset_id=?",
            (asset_id,),
        )[0]
        assert attachment["usage"] == "style"
        assert attachment["content_version"] == staged["content_version"]
        assert payload["user_message"]["attachment"]["asset_id"] == asset_id
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_attachment_usage_reaches_frozen_prompt_reference_after_requirements_flow(
    full_settings,
):
    from tests.support.t01_prompt import action

    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    image = png_bytes(color=(7, 8, 9))
    digest = hashlib.sha256(image).hexdigest()
    content = "请把这张图作为商品参考，做一张商品主图，文案为 WARM MORNING"
    responder = _attachment_responder(
        {content: {"kind": "requirements_change", "reference_usage": "product"}}
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = (await client.post("/api/projects", json={})).json()["id"]
        staged = (await _stage(client, project_id, image)).json()
        asset_id = staged["asset"]["id"]
        response = await _post_message(
            client,
            project_id,
            {
                **_message_body(content, 0, str(uuid.uuid4())),
                "attachment_asset_id": asset_id,
            },
        )
        assert response.status_code == 200, response.text
        for name in ("compose_brief", "plan_creative", "plan_visual", "finalize_requirements"):
            await action(client, project_id, name)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        card = next(
            x
            for x in view["artifacts"]
            if x["kind"] == "final_brief" and x["status"] == "approved"
        )
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
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert view["prompt_preview"] is not None
        refs = view["prompt_preview"]["request"]["references"]
        assert len(refs) == 1
        assert refs[0]["asset_id"] == asset_id
        assert refs[0]["usage"] == "product"
        assert refs[0]["content_version"] == digest
        assert refs[0]["sha256"] == digest

        preview = view["prompt_preview"]
        confirmed = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_prompt",
            json={
                "expected_revision": view["project_revision"],
                "payload": {
                    "snapshot_id": preview["id"],
                    "snapshot_digest": preview["request_digest"],
                    "command_id": str(uuid.uuid4()),
                    "command_context": preview["command_context"],
                },
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        submitted = visual.submit_calls[0]
        assert submitted.reference_bytes == image
        assert submitted.reference_mime_type == "image/png"
        assert submitted.reference_path is not None


@pytest.mark.asyncio
async def test_attachment_entry_enforces_identity_owner_origin_csrf_and_asset_scope(
    full_settings,
):
    llm, visual = LLMStub(), VisualStub()
    content = "请把这张图作为商品参考，做一张商品主图"
    responder = _attachment_responder(
        {content: {"kind": "requirements_change", "reference_usage": "product"}}
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        project_id = (await client.post("/api/projects", json={})).json()["id"]
        image = png_bytes(color=(3, 5, 7))
        staging_path = f"/api/projects/{project_id}/message-attachments"

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as anon:
            anonymous = await anon.post(
                staging_path, files={"file": ("reference.png", image, "image/png")}
            )
            assert anonymous.status_code == 401, anonymous.text

        other = await _add_account(app, "t08-other@example.com")
        async with aclosing(await _login_user(app, other.email_normalized)) as other_client:
            foreign = await other_client.post(
                staging_path, files={"file": ("reference.png", image, "image/png")}
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
                    staging_path,
                    headers=headers,
                    files={"file": ("reference.png", image, "image/png")},
                )
                assert guarded.status_code == 403, guarded.text

        invalid = await _stage(client, project_id, b"not-an-image")
        assert invalid.status_code == 422, invalid.text

        staged = (await _stage(client, project_id, image)).json()
        asset_id = staged["asset"]["id"]
        preview_url = staged["preview_url"]
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://testserver"
        ) as anon:
            assert (await anon.get(preview_url)).status_code == 401
        async with aclosing(await _login_user(app, other.email_normalized)) as other_client:
            assert (await other_client.get(preview_url)).status_code == 404

        tampered = (await _stage(client, project_id, image)).json()
        tampered_id = tampered["asset"]["id"]
        relative_path = _rows(
            full_settings.database_path,
            "SELECT relative_path FROM assets WHERE id=?",
            (tampered_id,),
        )[0]["relative_path"]
        (full_settings.resolved_data_dir() / relative_path).write_bytes(png_bytes(color=(9, 9, 9)))
        changed = await _post_message(
            client,
            project_id,
            {
                **_message_body(content, 0, str(uuid.uuid4())),
                "attachment_asset_id": tampered_id,
            },
        )
        assert changed.status_code == 409, changed.text
        assert changed.json()["error"]["code"] == "ATTACHMENT_NOT_AVAILABLE"

        second_project = (await client.post("/api/projects", json={})).json()["id"]
        foreign_asset = (await _stage(client, second_project, image)).json()["asset"]["id"]
        cross_project = await _post_message(
            client,
            project_id,
            {
                **_message_body(content, 0, str(uuid.uuid4())),
                "attachment_asset_id": foreign_asset,
            },
        )
        assert cross_project.status_code == 404, cross_project.text
        assert cross_project.json()["error"]["code"] == "ASSET_NOT_FOUND"

        request_id = str(uuid.uuid4())
        body = {
            **_message_body(content, 0, request_id),
            "attachment_asset_id": asset_id,
        }
        accepted = await _post_message(client, project_id, body)
        assert accepted.status_code == 200, accepted.text
        accepted_body = accepted.json()
        assert accepted_body["user_message"]["attachment"]["asset_id"] == asset_id
        current_revision = accepted_body["workflow"]["project_revision"]

        replay = await _post_message(client, project_id, body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["user_message"]["id"] == accepted_body["user_message"]["id"]
        assert replay.json()["user_message"]["attachment"]["asset_id"] == asset_id

        reused = await _post_message(
            client,
            project_id,
            {
                **_message_body(content, current_revision, str(uuid.uuid4())),
                "attachment_asset_id": asset_id,
            },
        )
        assert reused.status_code == 409, reused.text
        assert reused.json()["error"]["code"] == "ATTACHMENT_ALREADY_SENT"
        discarded = await client.delete(
            f"/api/projects/{project_id}/message-attachments/{asset_id}"
        )
        assert discarded.status_code == 409, discarded.text
        assert visual.submit_calls == []


def test_0015_upgrade_preserves_existing_messages_without_inventing_attachments(settings):
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

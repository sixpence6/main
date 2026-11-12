from __future__ import annotations

import io
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport
from PIL import Image
from sqlalchemy import select

from app.auth.models import Account, AuditEvent
from app.auth.passwords import hash_password
from app.auth.service import normalize_email
from app.db import read_session, write_transaction
from app.models import Asset
from app.timeutil import format_timestamp
from app.workflow import state
from app.workflow.tables import Approval
from tests.support.auth import TEST_ORIGIN, AuthClient, ensure_test_admin, login

USER_PASSWORD = "correct horse battery staple 60"


@asynccontextmanager
async def _fresh_app(settings: Any, registry: Any):
    from app.main import create_app
    from tests.conftest import migrate

    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        await ensure_test_admin(application)
        yield application


def _client(app: Any) -> AuthClient:
    return AuthClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN)


async def _add_account(app: Any, email: str) -> Account:
    now = app.state.clock.now()
    password_hash = await app.state.hash_gate.run(hash_password, USER_PASSWORD)
    async with write_transaction(app.state.session_factory) as session:
        account = Account(
            id=str(uuid.uuid4()),
            email_normalized=normalize_email(email),
            password_hash=password_hash,
            password_set_at=format_timestamp(now),
            password_policy_version="r1-2026-10",
            is_admin=0,
            is_disabled=0,
            session_epoch=0,
            created_at=format_timestamp(now),
            updated_at=format_timestamp(now),
        )
        session.add(account)
        await session.flush()
        return account


async def _login_user(app: Any, email: str) -> AuthClient:
    client = _client(app)
    await login(client, email=email, password=USER_PASSWORD)
    return client


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 8), (40, 90, 160)).save(buffer, "PNG")
    return buffer.getvalue()


async def _upload(
    client: AuthClient, project_id: str, revision: int, *, name: str = "reference.png"
) -> Any:
    return await client.post(
        f"/api/projects/{project_id}/reference",
        files={"file": (name, _png(), "image/png")},
        data={"expected_revision": str(revision)},
    )


def _brief() -> dict[str, Any]:
    return {
        "product_description": "米色亚麻托特包",
        "purpose": "电商主图",
        "facts": [
            {
                "text": "用户描述为米色亚麻托特包",
                "basis": "user_declared",
                "evidence_ids": ["user-turn-1"],
            }
        ],
        "creative_direction": "简洁暖色，突出材质",
        "style_keywords": ["简洁", "暖色"],
        "composition": "居中构图，浅米色背景",
        "lighting": "柔和自然光",
        "texts": ["春季新品"],
        "must_include": ["托特包主体完整", "提手可见"],
        "must_preserve": ["亚麻材质纹理"],
        "forbidden": ["水印", "夸张阴影"],
        "size": "1024x1024",
        "reference_asset_id": None,
        "reference_usage": "none",
        "assumptions": ["未指定品牌"],
        "unresolved": [],
    }


async def _seed_confirmation(app: Any, client: AuthClient) -> tuple[str, Any, Any]:
    project = (await client.post("/api/projects", json={})).json()
    project_id = project["id"]
    patched = await client.patch(
        f"/api/projects/{project_id}/brief",
        json={
            "expected_revision": 0,
            "product_description": "米色亚麻托特包",
            "purpose": "电商主图",
            "style": "简洁暖色，突出材质",
            "composition": "居中构图，浅米色背景",
            "text_content": "春季新品",
        },
    )
    assert patched.status_code == 200, patched.text
    ctx = await state.load_context(app, project_id)
    await state.create_run(ctx, "S06")
    ctx = await state.reload_context(ctx)
    artifact = await state.start_artifact(ctx, "final_brief", _brief())
    await state.add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks=[{"rule_id": "T09_CARD_COMPLETE", "outcome": "pass", "note": "卡片字段完整"}],
    )
    ctx = await state.reload_context(ctx)
    current = await state.latest_artifact(ctx, "final_brief")
    assert current is not None
    return project_id, ctx, current


async def _events(app: Any, event_type: str) -> list[AuditEvent]:
    async with read_session(app.state.session_factory) as session:
        return list(
            (
                await session.scalars(
                    select(AuditEvent)
                    .where(AuditEvent.event_type == event_type)
                    .order_by(AuditEvent.occurred_at, AuditEvent.id)
                )
            ).all()
        )


@pytest.mark.asyncio
async def test_reference_upload_records_operator_and_blocks_other_accounts(settings, registry):
    async with _fresh_app(settings, registry) as app:
        admin = _client(app)
        try:
            await login(admin)
            account_a = await _add_account(app, "a60@example.com")
            await _add_account(app, "b60@example.com")
            user_a = await _login_user(app, "a60@example.com")
            user_b = await _login_user(app, "b60@example.com")
            try:
                project = (await user_a.post("/api/projects", json={})).json()
                uploaded = await _upload(
                    user_a, project["id"], 0, name="../../outside.png"
                )
                assert uploaded.status_code == 200, uploaded.text
                asset_id = uploaded.json()["reference_asset"]["id"]

                async with read_session(app.state.session_factory) as session:
                    asset = await session.get(Asset, asset_id)
                    assert asset is not None
                    assert asset.relative_path == f"references/{asset_id}.png"
                events = await _events(app, "reference_uploaded")
                assert len(events) == 1
                event = events[0]
                assert event.actor_account_id == account_a.id
                assert event.actor_kind == "account"
                assert event.target_kind == "project"
                assert event.target_id == project["id"]
                assert event.result == "success"
                assert json.loads(event.detail_json or "{}") == {
                    "mime_type": "image/png",
                    "width": 10,
                    "height": 8,
                    "bytes": len(_png()),
                }

                denied = await _upload(user_b, project["id"], 1)
                assert denied.status_code == 404, denied.text
                assert len(await _events(app, "reference_uploaded")) == 1

                removed = await user_a.delete(
                    f"/api/projects/{project['id']}/reference",
                    params={"expected_revision": uploaded.json()["revision"]},
                )
                assert removed.status_code == 200, removed.text
                removed_events = await _events(app, "reference_removed")
                assert len(removed_events) == 1
                assert removed_events[0].actor_account_id == account_a.id
                assert removed_events[0].target_id == project["id"]
                assert json.loads(removed_events[0].detail_json or "{}") == {
                    "had_reference": True
                }
            finally:
                await user_a.aclose()
                await user_b.aclose()
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_foreign_artifact_id_is_rejected_and_self_reported_roles_are_ignored(
    settings, registry
):
    async with _fresh_app(settings, registry) as app:
        admin = _client(app)
        try:
            await login(admin)
            account_a = await _add_account(app, "a60-confirm@example.com")
            account_b = await _add_account(app, "b60-confirm@example.com")
            user_a = await _login_user(app, "a60-confirm@example.com")
            user_b = await _login_user(app, "b60-confirm@example.com")
            try:
                project_a, ctx_a, artifact_a = await _seed_confirmation(app, user_a)
                project_b, ctx_b, artifact_b = await _seed_confirmation(app, user_a)
                assert project_a != project_b
                assert artifact_a.id != artifact_b.id

                foreign = await user_a.post(
                    f"/api/projects/{project_a}/workflow/actions/confirm_requirements",
                    json={
                        "expected_revision": ctx_a.project.revision,
                        "payload": {
                            "artifact_id": artifact_b.id,
                            "artifact_digest": artifact_b.digest,
                            "requirement_revision": ctx_a.run.requirement_revision,
                            "owner_account_id": account_b.id,
                            "approved_by": "attacker",
                            "confirmed": True,
                            "is_admin": True,
                        },
                    },
                )
                assert foreign.status_code == 409, foreign.text
                assert foreign.json()["error"]["code"] in {
                    "CARD_DIGEST_MISMATCH",
                    "REQUIREMENTS_CHANGED",
                }
                async with read_session(app.state.session_factory) as session:
                    approvals = list(
                        (
                            await session.scalars(
                                select(Approval).where(Approval.project_id == project_a)
                            )
                        ).all()
                    )
                    assert approvals == []

                valid = await user_a.post(
                    f"/api/projects/{project_a}/workflow/actions/confirm_requirements",
                    json={
                        "expected_revision": ctx_a.project.revision,
                        "payload": {
                            "artifact_id": artifact_a.id,
                            "artifact_digest": artifact_a.digest,
                            "requirement_revision": ctx_a.run.requirement_revision,
                            "owner_account_id": account_b.id,
                            "approved_by": "attacker",
                            "confirmed": True,
                            "is_admin": True,
                        },
                    },
                )
                assert valid.status_code == 200, valid.text
                async with read_session(app.state.session_factory) as session:
                    approvals = list(
                        (
                            await session.scalars(
                                select(Approval).where(Approval.project_id == project_a)
                            )
                        ).all()
                    )
                    assert len(approvals) == 1
                    assert approvals[0].approved_by == "user"
                    assert approvals[0].project_id == project_a
                events = await _events(app, "workflow_action")
                successes = [event for event in events if event.result == "success"]
                failures = [event for event in events if event.result != "success"]
                assert len(successes) == 1
                assert len(failures) == 1
                event = successes[0]
                assert event.actor_account_id == account_a.id
                assert event.target_kind == "project"
                assert event.target_id == project_a
                detail = json.loads(event.detail_json or "{}")
                assert detail["action_id"] == "confirm_requirements"
                assert detail["expected_revision"] == ctx_a.project.revision
                assert detail["run_id"] == ctx_a.run.id
                assert detail["stage"] == "S06"

                denied = await user_b.post(
                    f"/api/projects/{project_a}/workflow/actions/confirm_requirements",
                    json={
                        "expected_revision": ctx_a.project.revision,
                        "payload": {
                            "artifact_id": artifact_a.id,
                            "artifact_digest": artifact_a.digest,
                            "requirement_revision": ctx_a.run.requirement_revision,
                        },
                    },
                )
                assert denied.status_code == 404, denied.text
                assert len(await _events(app, "workflow_action")) == 2
            finally:
                await user_a.aclose()
                await user_b.aclose()
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_csrf_and_legacy_write_gate_remain_enforced(settings, registry):
    async with _fresh_app(settings, registry) as app:
        admin = _client(app)
        try:
            await login(admin)
            await _add_account(app, "a60-gate@example.com")
            user_a = await _login_user(app, "a60-gate@example.com")
            try:
                project = (await user_a.post("/api/projects", json={})).json()
                saved_csrf = user_a.csrf_token
                user_a.csrf_token = None
                blocked_upload = await _upload(user_a, project["id"], 0)
                assert blocked_upload.status_code == 403, blocked_upload.text
                assert blocked_upload.json()["error"]["code"] == "CSRF_INVALID"
                user_a.csrf_token = saved_csrf

                patched = await user_a.patch(
                    f"/api/projects/{project['id']}/brief",
                    json={
                        "expected_revision": 0,
                        "product_description": "米色亚麻托特包",
                        "purpose": "电商主图",
                    },
                )
                assert patched.status_code == 200, patched.text
                ctx = await state.load_context(app, project["id"])
                await state.create_run(ctx, "S06")
                blocked_legacy = await user_a.patch(
                    f"/api/projects/{project['id']}/brief",
                    json={
                        "expected_revision": 1,
                        "product_description": "不应写入",
                    },
                )
                assert blocked_legacy.status_code == 409, blocked_legacy.text
                assert blocked_legacy.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"
            finally:
                await user_a.aclose()
        finally:
            await admin.aclose()

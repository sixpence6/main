from __future__ import annotations

import io
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport
from PIL import Image
from sqlalchemy import select

from app.auth.models import AuthSession
from app.auth.passwords import hash_password
from app.auth.principal import Principal
from app.auth.service import normalize_email
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.models import Generation, Message, Project
from app.services.projects import get_project_detail, list_projects
from app.timeutil import format_timestamp
from tests.support.auth import (
    TEST_ORIGIN,
    AuthClient,
    ensure_test_admin,
    login,
)

USER_PASSWORD = "correct horse battery staple 59"


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


async def _add_account(app: Any, email: str) -> Any:
    now = app.state.clock.now()
    password_hash = await app.state.hash_gate.run(hash_password, USER_PASSWORD)
    async with write_transaction(app.state.session_factory) as session:
        from app.auth.models import Account

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


async def _seed_subobjects(app: Any, project_id: str) -> tuple[str, str, bytes]:
    now = app.state.clock.now()
    async with write_transaction(app.state.session_factory) as session:
        message = Message(
            id=str(uuid.uuid4()),
            project_id=project_id,
            sequence_no=1,
            role="user",
            content="A 的私有对话",
            project_revision=0,
            brief_snapshot_json=None,
            operation_id=None,
            created_at=format_timestamp(now),
        )
        generation = Generation(
            id=str(uuid.uuid4()),
            project_id=project_id,
            version_no=1,
            request_id=str(uuid.uuid4()),
            request_canonical="{}",
            request_digest="0" * 64,
            confirmed_revision=0,
            status="succeeded",
            stage="done",
            brief_snapshot_json="{}",
            prompt="",
            image_size=None,
            reference_asset_id=None,
            adapter_id="stub",
            model="stub",
            service_fingerprint="stub",
            remote_task_id=None,
            result_locator_json=None,
            result_asset_id=None,
            error_code=None,
            error_message=None,
            submit_attempted=1,
            workflow_job_id=None,
            created_at=format_timestamp(now),
            started_at=format_timestamp(now),
            completed_at=format_timestamp(now),
        )
        session.add(message)
        session.add(generation)
        await session.flush()
        return message.id, generation.id


async def _upload(client: AuthClient, project_id: str, revision: int) -> Any:
    return await client.post(
        f"/api/projects/{project_id}/reference",
        files={"file": ("reference.png", _png(), "image/png")},
        data={"expected_revision": str(revision)},
    )


@pytest.mark.asyncio
async def test_owner_is_persisted_and_list_filtering_happens_before_pagination(settings, registry):
    async with _fresh_app(settings, registry) as app:
        admin = _client(app)
        try:
            await login(admin)
            account_a = await _add_account(app, "a@example.com")
            account_b = await _add_account(app, "b@example.com")
            user_a = await _login_user(app, "a@example.com")
            user_b = await _login_user(app, "b@example.com")
            try:
                project_a = (await user_a.post("/api/projects", json={})).json()
                project_b = (await user_b.post("/api/projects", json={})).json()
                async with write_transaction(app.state.session_factory) as session:
                    row_a = await session.get(Project, project_a["id"])
                    row_b = await session.get(Project, project_b["id"])
                    assert row_a is not None and row_b is not None
                    assert row_a.owner_account_id == account_a.id
                    assert row_b.owner_account_id == account_b.id
                    row_a.updated_at = "2026-01-01T00:00:00.000Z"
                    row_b.updated_at = "2026-02-01T00:00:00.000Z"

                list_a = await user_a.get("/api/projects", params={"limit": 1})
                assert list_a.status_code == 200, list_a.text
                assert [item["id"] for item in list_a.json()["items"]] == [project_a["id"]]
                list_b = await user_b.get("/api/projects", params={"limit": 1})
                assert list_b.status_code == 200, list_b.text
                assert [item["id"] for item in list_b.json()["items"]] == [project_b["id"]]

                list_admin = await admin.get("/api/projects")
                assert list_admin.status_code == 200
                assert list_admin.json()["items"] == []

                other = await user_a.get(f"/api/projects/{project_b['id']}")
                missing = await user_a.get(f"/api/projects/{uuid.uuid4()}")
                assert other.status_code == 404
                assert missing.status_code == 404
                assert other.json() == missing.json()
                assert (await admin.get(f"/api/projects/{project_a['id']}")).status_code == 404
                assert (await user_a.get(f"/api/projects/{project_a['id']}")).status_code == 200
            finally:
                await user_a.aclose()
                await user_b.aclose()
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_subobject_ids_do_not_cross_account_boundaries(settings, registry):
    async with _fresh_app(settings, registry) as app:
        admin = _client(app)
        try:
            await login(admin)
            await _add_account(app, "a-sub@example.com")
            await _add_account(app, "b-sub@example.com")
            user_a = await _login_user(app, "a-sub@example.com")
            user_b = await _login_user(app, "b-sub@example.com")
            try:
                project_a = (await user_a.post("/api/projects", json={})).json()
                upload = await _upload(user_a, project_a["id"], 0)
                assert upload.status_code == 200, upload.text
                asset_id = upload.json()["reference_asset"]["id"]
                message_id, generation_id = await _seed_subobjects(app, project_a["id"])
                assert message_id and generation_id
                denied_upload = await _upload(user_b, project_a["id"], 0)
                assert denied_upload.status_code == 404, denied_upload.text
                denied_remove = await user_b.delete(
                    f"/api/projects/{project_a['id']}/reference", params={"expected_revision": 0}
                )
                assert denied_remove.status_code == 404, denied_remove.text

                detail = await user_a.get(f"/api/projects/{project_a['id']}")
                assert detail.status_code == 200
                assert [item["id"] for item in detail.json()["messages"]] == [message_id]

                checks = [
                    ("get", f"/api/projects/{project_a['id']}", None),
                    ("get", f"/api/projects/{project_a['id']}/workflow", None),
                    (
                        "patch",
                        f"/api/projects/{project_a['id']}/brief",
                        {"expected_revision": 0, "product_description": "x"},
                    ),
                    (
                        "post",
                        f"/api/projects/{project_a['id']}/messages",
                        {"content": "x", "expected_revision": 0},
                    ),
                    (
                        "post",
                        f"/api/projects/{project_a['id']}/workflow/actions/submit_request",
                        {"expected_revision": 0, "text": "x"},
                    ),
                    (
                        "post",
                        f"/api/projects/{project_a['id']}/generations",
                        {"request_id": str(uuid.uuid4()), "confirmed_revision": 0},
                    ),
                    ("get", f"/api/assets/{asset_id}", None),
                    ("get", f"/api/generations/{generation_id}", None),
                    ("post", f"/api/generations/{generation_id}/retrieve-result", {}),
                ]
                for method, path, payload in checks:
                    if payload is None:
                        response = await getattr(user_b, method)(path)
                    else:
                        response = await getattr(user_b, method)(path, json=payload)
                    assert response.status_code == 404, (path, response.status_code, response.text)
                assert (await user_b.get(f"/api/assets/{asset_id}")).status_code == 404
                assert (await admin.get(f"/api/assets/{asset_id}")).status_code == 404
                assert (await admin.get(f"/api/generations/{generation_id}")).status_code == 404

                own_asset = await user_a.get(f"/api/assets/{asset_id}")
                assert own_asset.status_code == 200
                assert own_asset.content == _png()
                assert (await user_a.get(f"/api/generations/{generation_id}")).status_code == 200
            finally:
                await user_a.aclose()
                await user_b.aclose()
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_forged_owner_role_fields_and_stale_principal_cannot_authorize(settings, registry):
    async with _fresh_app(settings, registry) as app:
        admin = _client(app)
        try:
            await login(admin)
            account_a = await _add_account(app, "a-forged@example.com")
            account_b = await _add_account(app, "b-forged@example.com")
            user_a = await _login_user(app, "a-forged@example.com")
            try:
                forged_owner = await user_a.post(
                    "/api/projects", json={"owner_account_id": account_b.id}
                )
                assert forged_owner.status_code == 422
                forged_role = await user_a.post("/api/projects", json={"is_admin": True})
                assert forged_role.status_code == 422

                project = (await user_a.post("/api/projects", json={})).json()
                forged_message = await user_a.post(
                    f"/api/projects/{project['id']}/messages",
                    json={
                        "content": "x",
                        "expected_revision": 0,
                        "owner_account_id": account_b.id,
                    },
                )
                assert forged_message.status_code == 422

                async with read_session(app.state.session_factory) as session:
                    session_row = await session.scalar(
                        select(AuthSession).where(AuthSession.account_id == account_a.id)
                    )
                    assert session_row is not None
                    stale = Principal(
                        account_id=account_a.id,
                        email="a-forged@example.com",
                        is_admin=True,
                        session_id=session_row.id,
                        account_epoch=session_row.account_epoch,
                    )
                assert (
                    await admin.post(f"/api/admin/accounts/{account_a.id}/disable")
                ).status_code == 200
                with pytest.raises(ApiError) as error:
                    await list_projects(
                        app.state.session_factory, limit=20, cursor=None, principal=stale
                    )
                assert error.value.status_code == 401
                assert error.value.code == "AUTH_REQUIRED"
            finally:
                await user_a.aclose()
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_unowned_legacy_project_is_not_served_externally(settings, registry):
    async with _fresh_app(settings, registry) as app:
        admin = _client(app)
        try:
            await login(admin)
            await _add_account(app, "a-legacy@example.com")
            user_a = await _login_user(app, "a-legacy@example.com")
            now = app.state.clock.now()
            async with write_transaction(app.state.session_factory) as session:
                legacy = Project(
                    id=str(uuid.uuid4()),
                    owner_account_id=None,
                    title="未归属旧项目",
                    revision=0,
                    created_at=format_timestamp(now),
                    updated_at=format_timestamp(now),
                )
                session.add(legacy)
                await session.flush()
                legacy_id = legacy.id
            try:
                assert (await user_a.get(f"/api/projects/{legacy_id}")).status_code == 404
                assert (await admin.get(f"/api/projects/{legacy_id}")).status_code == 404
                assert (await user_a.get("/api/projects")).json()["items"] == []
                detail = await get_project_detail(app.state.session_factory, legacy_id)
                assert detail.project.id == legacy_id
            finally:
                await user_a.aclose()
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_anonymous_business_requests_are_denied(settings, registry):
    async with _fresh_app(settings, registry) as app:
        anonymous = _client(app)
        try:
            for path in (
                "/api/projects",
                f"/api/projects/{uuid.uuid4()}",
                f"/api/assets/{uuid.uuid4()}",
            ):
                response = await anonymous.get(path)
                assert response.status_code == 401
                assert response.json()["error"]["code"] == "AUTH_REQUIRED"
        finally:
            await anonymous.aclose()

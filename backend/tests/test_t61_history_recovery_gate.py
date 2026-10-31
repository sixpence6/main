from __future__ import annotations

import io
import json
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport
from PIL import Image
from sqlalchemy import select

from app.auth.models import Account
from app.auth.passwords import hash_password
from app.auth.service import normalize_email
from app.db import write_transaction
from app.main import create_app
from app.models import Generation, Project
from app.timeutil import format_timestamp
from app.workflow.tables import Delivery
from tests.conftest import migrate
from tests.support.adapters import LLMStub
from tests.support.auth import (
    TEST_ORIGIN,
    AuthClient,
    ensure_test_admin,
    login,
)
from tests.test_v2_t13_delivery import (
    _approve,
    _delivery_env,
    _prepare,
    _publish,
    _role_responder,
    _seed_ready_state,
)
from tests.test_v2_t20_retention import _seed_candidate

USER_PASSWORD = "correct horse battery staple 61"

PROTECTED_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/api/auth/session"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/password/change"),
    ("GET", "/api/auth/sessions"),
    ("DELETE", "/api/auth/sessions/{session_id}"),
    ("POST", "/api/auth/sessions/revoke-others"),
    ("GET", "/api/auth/security-events"),
    ("GET", "/api/admin/accounts"),
    ("POST", "/api/admin/accounts"),
    ("POST", "/api/admin/accounts/{account_id}/disable"),
    ("POST", "/api/admin/accounts/{account_id}/restore"),
    ("POST", "/api/admin/accounts/{account_id}/links"),
    ("GET", "/api/admin/security-events"),
    ("GET", "/api/assets/{asset_id}"),
    ("GET", "/api/generations/{generation_id}"),
    ("POST", "/api/generations/{generation_id}/retrieve-result"),
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("GET", "/api/projects/{project_id}"),
    ("PATCH", "/api/projects/{project_id}/brief"),
    ("POST", "/api/projects/{project_id}/confirm"),
    ("POST", "/api/projects/{project_id}/generations"),
    ("POST", "/api/projects/{project_id}/messages"),
    ("DELETE", "/api/projects/{project_id}/reference"),
    ("POST", "/api/projects/{project_id}/reference"),
    ("GET", "/api/projects/{project_id}/workflow"),
    ("POST", "/api/projects/{project_id}/workflow/actions/{action_id}"),
)


@asynccontextmanager
async def _fresh_app(settings: Any, registry: Any):
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


def _png(color: tuple[int, int, int] = (20, 80, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 8), color).save(buffer, format="PNG")
    return buffer.getvalue()


async def _upload(client: AuthClient, project_id: str, revision: int) -> Any:
    return await client.post(
        f"/api/projects/{project_id}/reference",
        files={"file": ("reference.png", _png(), "image/png")},
        data={"expected_revision": str(revision)},
    )


async def _seed_generation(app: Any, project_id: str, asset_id: str) -> str:
    now = format_timestamp(app.state.clock.now())
    async with write_transaction(app.state.session_factory) as session:
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
            remote_task_id="remote-t61",
            result_locator_json=json.dumps(
                {"locator": "https://provider.invalid/secret-image"},
                ensure_ascii=False,
            ),
            result_asset_id=asset_id,
            error_code=None,
            error_message=None,
            submit_attempted=0,
            workflow_job_id=None,
            created_at=now,
            started_at=now,
            completed_at=now,
        )
        session.add(generation)
        await session.flush()
        return generation.id


def _route_path(path: str) -> str:
    return path.format(
        session_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        asset_id=uuid.uuid4(),
        generation_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        action_id="confirm_requirements",
    )


@pytest.mark.asyncio
async def test_every_private_route_requires_authentication(settings, registry):
    async with _fresh_app(settings, registry) as app:
        async with _client(app) as client:
            for method, template in PROTECTED_ROUTES:
                path = _route_path(template)
                kwargs: dict[str, Any] = {}
                if method not in {"GET", "DELETE"}:
                    kwargs["json"] = {}
                response = await client.request(method, path, **kwargs)
                assert response.status_code == 401, (method, path, response.text)
                assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_cross_account_ids_never_expose_history_or_asset_metadata(settings, registry):
    async with _fresh_app(settings, registry) as app:
        admin = _client(app)
        try:
            await login(admin)
            await _add_account(app, "a-t61@example.com")
            await _add_account(app, "b-t61@example.com")
            user_a = await _login_user(app, "a-t61@example.com")
            user_b = await _login_user(app, "b-t61@example.com")
            try:
                project_a = (await user_a.post("/api/projects", json={})).json()
                project_b = (await user_b.post("/api/projects", json={})).json()
                upload_a = await _upload(user_a, project_a["id"], 0)
                upload_b = await _upload(user_b, project_b["id"], 0)
                assert upload_a.status_code == 200, upload_a.text
                assert upload_b.status_code == 200, upload_b.text
                asset_a = upload_a.json()["reference_asset"]["id"]
                asset_b = upload_b.json()["reference_asset"]["id"]

                for method, path in (
                    ("get", f"/api/projects/{project_a['id']}"),
                    ("get", f"/api/projects/{project_a['id']}/workflow"),
                    ("get", f"/api/assets/{asset_a}"),
                    ("get", f"/api/assets/{asset_a}?download=true"),
                ):
                    response = await getattr(user_b, method)(path)
                    assert response.status_code == 404, (path, response.text)
                action = await user_b.post(
                    f"/api/projects/{project_a['id']}/workflow/actions/confirm_requirements",
                    json={"expected_revision": 0, "payload": {}},
                )
                assert action.status_code == 404, action.text
                assert (await admin.get(f"/api/projects/{project_a['id']}")).status_code == 404
                assert (await admin.get(f"/api/assets/{asset_a}")).status_code == 404

                # Corrupt the stored cross-project associations: metadata must not
                # turn into a history or preview oracle even when a row now points
                # at another project's asset.
                async with write_transaction(app.state.session_factory) as session:
                    row_a = await session.get(Project, project_a["id"])
                    assert row_a is not None
                    row_a.reference_asset_id = asset_b
                generation_id = await _seed_generation(app, project_a["id"], asset_b)

                detail = await user_a.get(f"/api/projects/{project_a['id']}")
                assert detail.status_code == 200, detail.text
                payload = detail.json()
                assert payload["project"]["reference_asset"] is None
                generation = next(
                    item for item in payload["generations"] if item["id"] == generation_id
                )
                assert generation["result_asset"] is None
                assert generation["result_asset_id"] is None
                assert asset_b not in detail.text
                assert "provider.invalid" not in detail.text
                assert "result_locator" not in detail.text

                assert (await user_a.get(f"/api/assets/{asset_a}")).status_code == 200
                assert (await user_a.get(f"/api/assets/{asset_b}")).status_code == 404
                assert (await user_b.get(f"/api/assets/{asset_b}")).status_code == 200
            finally:
                await user_a.aclose()
                await user_b.aclose()
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_published_bytes_are_exact_and_unapproved_states_stay_closed(full_settings):
    stub = LLMStub()

    async def seed(settings: Any, session: Any):
        ids = await _seed_ready_state(settings, session)
        stub.role_responder = _role_responder(ids)
        return ids

    async with _delivery_env(full_settings, stub, seed) as env:
        before = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert before.status_code == 409, before.text
        assert env.ids.image_bytes not in before.content

        await _prepare(env)
        prepared = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert prepared.status_code == 409, prepared.text
        assert env.ids.image_bytes not in prepared.content

        await _approve(env)
        approved = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert approved.status_code == 409, approved.text
        assert env.ids.image_bytes not in approved.content
        download = await env.client.get(f"/api/assets/{env.ids.asset_id}?download=true")
        assert download.status_code == 409, download.text
        assert env.ids.image_bytes not in download.content

        await _publish(env)
        published = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert published.status_code == 200, published.text
        assert published.content == env.ids.image_bytes
        downloaded = await env.client.get(f"/api/assets/{env.ids.asset_id}?download=true")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == env.ids.image_bytes
        assert "attachment" in downloaded.headers["content-disposition"]

        published_files = list((full_settings.resolved_data_dir() / "published").glob("*"))
        assert len(published_files) == 1
        original = published_files[0].read_bytes()
        assert original == env.ids.image_bytes
        published_files[0].unlink()
        missing = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert missing.status_code == 404, missing.text
        assert env.ids.image_bytes not in missing.content
        published_files[0].write_bytes(original)

        async with env.app.state.session_factory() as session:
            delivery = await session.scalar(select(Delivery))
            assert delivery is not None
            delivery.status = "revoked"
            await session.commit()
        revoked = await env.client.get(f"/api/assets/{env.ids.asset_id}")
        assert revoked.status_code == 409, revoked.text
        assert env.ids.image_bytes not in revoked.content

        expired = await _seed_candidate(env.app, completed_delta=timedelta(hours=25))
        expired_response = await env.client.get(f"/api/assets/{expired.asset_id}")
        assert expired_response.status_code == 409, expired_response.text
        assert expired.data not in expired_response.content

        quarantined = await _seed_candidate(env.app, completed_delta=timedelta(0))
        quarantined_response = await env.client.get(f"/api/assets/{quarantined.asset_id}")
        assert quarantined_response.status_code == 409, quarantined_response.text
        assert quarantined.data not in quarantined_response.content


@pytest.mark.asyncio
async def test_reference_digest_and_static_or_locator_paths_fail_closed(settings, registry):
    async with _fresh_app(settings, registry) as app:
        async with _client(app) as client:
            await login(client)
            project = (await client.post("/api/projects", json={})).json()
            upload = await _upload(client, project["id"], 0)
            assert upload.status_code == 200, upload.text
            asset_id = upload.json()["reference_asset"]["id"]
            original = _png()
            target = settings.resolved_data_dir() / "references" / f"{asset_id}.png"
            assert target.read_bytes() == original

            target.write_bytes(_png((200, 20, 20)))
            tampered = await client.get(f"/api/assets/{asset_id}")
            assert tampered.status_code == 409, tampered.text
            assert original not in tampered.content

            target.write_bytes(original)
            restored = await client.get(f"/api/assets/{asset_id}")
            assert restored.status_code == 200, restored.text
            assert restored.content == original

            # The public build is served with the same no-referrer/nosniff
            # boundary, but private data paths never become a public alias.
            (settings.resolved_web_dist_dir() / "assets").mkdir(parents=True, exist_ok=True)
            (settings.resolved_web_dist_dir() / "index.html").write_text(
                "<html>ok</html>", encoding="utf-8"
            )
            (settings.resolved_web_dist_dir() / "assets" / "app.js").write_text(
                "console.log('ok')", encoding="utf-8"
            )
            for path in (
                "/references/leak.png",
                "/generated/leak.png",
                "/published/leak.png",
                "/data/references/leak.png",
                "/static/leak.png",
            ):
                response = await client.get(path)
                assert response.status_code == 404, (path, response.text)
                assert original not in response.content
            locator = await client.get("/api/assets/https%3A%2F%2Fprovider.invalid%2Fsecret-image")
            assert locator.status_code == 404, locator.text

            static = await client.get("/assets/app.js")
            assert static.status_code == 200
            assert static.text == "console.log('ok')"
            assert static.headers["referrer-policy"] == "no-referrer"
            assert static.headers["x-content-type-options"] == "nosniff"

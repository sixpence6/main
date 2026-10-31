from __future__ import annotations

import asyncio
import sqlite3
import uuid
from typing import Any

import pytest
from httpx import ASGITransport
from pydantic import SecretStr

from app.auth.clock import Clock, FrozenClock
from app.auth.models import Account
from app.auth.passwords import HashGate
from app.auth.service import AlreadyInitialized, init_admin
from app.db import build_session_factory, create_engine, write_transaction
from app.errors import ApiError
from app.maintenance.claim import CLAIM_CONFIRMATION, ClaimResult, claim_legacy
from app.models import Project
from tests.conftest import migrate
from tests.support.auth import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_ORIGIN,
    AuthClient,
    ensure_test_admin,
    login,
)
from tests.test_t55_auth_foundation import FROZEN, _login_attempt
from tests.test_t56_account_links import _create, _issue, _link_client, _submit, _token
from tests.test_t61_history_recovery_gate import (
    PROTECTED_ROUTES,
    _add_account,
    _fresh_app,
    _login_user,
    _png,
    _upload,
)
from tests.test_t63_legacy_claim import _backup, _seed_source

PUBLIC_OPERATIONS = {
    ("GET", "/api/health"),
    ("GET", "/api/config/status"),
    ("GET", "/api/auth/csrf"),
    ("POST", "/api/auth/login"),
    ("GET", "/api/auth/link"),
    ("POST", "/api/auth/link"),
}
EXPECTED_OPERATIONS = PUBLIC_OPERATIONS | set(PROTECTED_ROUTES)
USER_PASSWORD = "correct horse battery staple 61"



def _new_client(app: Any) -> AuthClient:
    return AuthClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN)


async def _csrf(client: AuthClient) -> str:
    response = await client.get("/api/auth/csrf")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


@pytest.mark.asyncio
async def test_registered_route_inventory_is_exact_and_default_deny_closes_unknown_surface(
    settings, registry
):
    async with _fresh_app(settings, registry) as app:
        spec = app.openapi()
        operations = {
            (method.upper(), path)
            for path, node in spec["paths"].items()
            for method in node
            if method in {"get", "post", "patch", "delete", "put"}
        }
        assert len(operations) == 37
        assert operations == EXPECTED_OPERATIONS

        async with _new_client(app) as anon:
            for method, template in PROTECTED_ROUTES:
                path = template.format(
                    session_id=uuid.uuid4(),
                    account_id=uuid.uuid4(),
                    asset_id=uuid.uuid4(),
                    generation_id=uuid.uuid4(),
                    project_id=uuid.uuid4(),
                    action_id="confirm_requirements",
                )
                kwargs: dict[str, Any] = {}
                if method not in {"GET", "DELETE"}:
                    kwargs["json"] = {}
                response = await anon.request(method, path, **kwargs)
                assert response.status_code == 401, (method, path, response.text)
                assert response.json()["error"]["code"] == "AUTH_REQUIRED"

            assert (await anon.get("/api/health")).status_code == 200
            assert (await anon.get("/api/config/status")).status_code == 200
            assert (await anon.get("/api/auth/csrf")).status_code == 200
            assert (await anon.get("/api/auth/link")).status_code == 400
            assert (await anon.post("/api/auth/link", json={})).status_code == 403
            assert (await anon.post("/api/auth/login", json={})).status_code == 403
            unknown = await anon.get("/api/not-a-route")
            assert unknown.status_code == 401
            assert unknown.json()["error"]["code"] == "AUTH_REQUIRED"

        async with _new_client(app) as admin:
            await login(admin)
            unknown = await admin.get("/api/not-a-route")
            assert unknown.status_code == 404
            assert unknown.json()["error"]["code"] == "ROUTE_NOT_FOUND"


@pytest.mark.asyncio
async def test_permission_state_matrix_keeps_admin_out_of_business_data(settings, registry):
    async with _fresh_app(settings, registry) as app:
        admin = _new_client(app)
        await login(admin)
        account_a = await _add_account(app, "gate-a@example.com")
        account_b = await _add_account(app, "gate-b@example.com")
        user_a = await _login_user(app, "gate-a@example.com")
        user_b = await _login_user(app, "gate-b@example.com")
        try:
            project_a = (await user_a.post("/api/projects", json={})).json()
            project_b = (await user_b.post("/api/projects", json={})).json()
            assert (await user_a.get(f"/api/projects/{project_a['id']}")).status_code == 200
            assert (await user_a.get(f"/api/projects/{project_b['id']}")).status_code == 404
            assert (await user_b.get(f"/api/projects/{project_a['id']}")).status_code == 404
            assert (await admin.get(f"/api/projects/{project_a['id']}")).status_code == 404
            assert (await admin.get(f"/api/projects/{project_b['id']}")).status_code == 404
            denied_workflow = await user_b.get(f"/api/projects/{project_a['id']}/workflow")
            assert denied_workflow.status_code == 404
            assert (await admin.get(f"/api/projects/{project_a['id']}/workflow")).status_code == 404
            assert (await user_b.patch(
                f"/api/projects/{project_a['id']}/brief",
                json={"expected_revision": 0, "product_description": "cross"},
            )).status_code == 404
            assert (await user_b.post(
                f"/api/projects/{project_a['id']}/messages",
                json={"content": "cross", "expected_revision": 0},
            )).status_code == 404
            assert (await user_b.post(
                f"/api/projects/{project_a['id']}/workflow/actions/confirm_requirements",
                json={"expected_revision": 0, "payload": {}},
            )).status_code == 404

            upload = await _upload(user_a, project_a["id"], 0)
            assert upload.status_code == 200, upload.text
            asset_id = upload.json()["reference_asset"]["id"]
            assert (await user_a.get(f"/api/assets/{asset_id}")).content == _png()
            assert (await user_b.get(f"/api/assets/{asset_id}")).status_code == 404
            assert (await admin.get(f"/api/assets/{asset_id}")).status_code == 404

            # A blocked file (tampered digest) stays closed even for its owner.
            target = settings.resolved_data_dir() / "references" / f"{asset_id}.png"
            original = target.read_bytes()
            target.write_bytes(_png((200, 0, 0)))
            blocked = await user_a.get(f"/api/assets/{asset_id}")
            assert blocked.status_code == 409, blocked.text
            assert original not in blocked.content
            target.write_bytes(original)
            assert (await user_a.get(f"/api/assets/{asset_id}")).content == original
            assert (await user_b.get(f"/api/assets/{asset_id}")).status_code == 404

            # An ownerless legacy project is not servable by anybody, including admin.
            now = app.state.clock.now()
            unclaimed_id = str(uuid.uuid4())
            from app.timeutil import format_timestamp

            async with write_transaction(app.state.session_factory) as session:
                session.add(
                    Project(
                        id=unclaimed_id,
                        title="unclaimed gate project",
                        created_at=format_timestamp(now),
                        updated_at=format_timestamp(now),
                    )
                )
            assert (await user_a.get(f"/api/projects/{unclaimed_id}")).status_code == 404
            assert (await user_b.get(f"/api/projects/{unclaimed_id}")).status_code == 404
            assert (await admin.get(f"/api/projects/{unclaimed_id}")).status_code == 404
            listed = await admin.get("/api/projects")
            assert listed.status_code == 200
            assert unclaimed_id not in {item["id"] for item in listed.json()["items"]}

            forged = await user_a.post(
                "/api/projects", json={"owner_account_id": account_a.id, "is_admin": True}
            )
            assert forged.status_code == 422, forged.text

            pending = await admin.post(
                "/api/admin/accounts", json={"email": "gate-pending@example.com"}
            )
            assert pending.status_code == 201, pending.text
            pending_client = _new_client(app)
            preauth = await _csrf(pending_client)
            pending_login = await pending_client.post(
                "/api/auth/login",
                json={"email": "gate-pending@example.com", "password": USER_PASSWORD},
                headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": preauth},
            )
            assert pending_login.status_code == 401
            assert pending_login.json()["error"]["code"] == "INVALID_CREDENTIALS"
            await pending_client.aclose()

            disabled = await admin.post(f"/api/admin/accounts/{account_b.id}/disable")
            assert disabled.status_code == 200, disabled.text
            assert (await user_b.get("/api/projects")).status_code == 401
            assert (await user_a.get("/api/projects")).status_code == 200
            assert (await user_a.get(f"/api/projects/{project_a['id']}")).status_code == 200
        finally:
            await user_a.aclose()
            await user_b.aclose()
            await admin.aclose()


@pytest.mark.asyncio
async def test_dual_sessions_csrf_origin_and_admin_role_boundary(settings, registry):
    async with _fresh_app(settings, registry) as app:
        await _add_account(app, "gate-session@example.com")
        first = await _login_user(app, "gate-session@example.com")
        second = await _login_user(app, "gate-session@example.com")
        try:
            listed = await first.get("/api/auth/sessions")
            assert listed.status_code == 200, listed.text
            sessions = listed.json()["items"]
            assert len(sessions) == 2
            assert sum(1 for item in sessions if item["current"]) == 1
            other = next(item for item in sessions if not item["current"])
            revoke = await first.delete(f"/api/auth/sessions/{other['id']}")
            assert revoke.status_code == 204, revoke.text
            assert (await second.get("/api/projects")).status_code == 401
            assert (await first.get("/api/projects")).status_code == 200

            saved = first.csrf_token
            first.csrf_token = None
            missing = await first.post("/api/projects", json={})
            assert missing.status_code == 403
            assert missing.json()["error"]["code"] == "CSRF_INVALID"
            first.csrf_token = saved

            first.origin = "https://evil.example"
            wrong_origin = await first.post("/api/projects", json={})
            assert wrong_origin.status_code == 403
            assert wrong_origin.json()["error"]["code"] == "ORIGIN_INVALID"
            first.origin = TEST_ORIGIN

            forbidden = await first.post("/api/admin/accounts", json={"email": "nope@example.com"})
            assert forbidden.status_code == 403, forbidden.text
            assert forbidden.json()["error"]["code"] == "FORBIDDEN"
            admin = _new_client(app)
            await login(admin)
            events = await admin.get("/api/admin/security-events")
            assert events.status_code == 200, events.text
            assert any(item["event_type"] == "permission_denied" for item in events.json()["items"])
            await admin.aclose()

            logout = await first.post("/api/auth/logout")
            assert logout.status_code == 204, logout.text
            assert (await first.get("/api/projects")).status_code == 401
            assert (await second.get("/api/projects")).status_code == 401
        finally:
            await first.aclose()
            await second.aclose()


@pytest.mark.asyncio
async def test_rate_hash_budgets_audit_rollback_and_database_outage_fail_closed(
    settings, registry, monkeypatch
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        anonymous = _new_client(app)
        try:
            for _ in range(5):
                response = await _login_attempt(
                    anonymous, email=TEST_ADMIN_EMAIL, password="wrong password 12345"
                )
                assert response.status_code == 401
            limited = await _login_attempt(anonymous, email=TEST_ADMIN_EMAIL)
            assert limited.status_code == 429
            assert limited.json()["error"]["code"] == "RATE_LIMITED"
            assert int(limited.headers["retry-after"]) > 0
        finally:
            await anonymous.aclose()
        app.state.clock.advance(901)

        # The password hashing gate has independent running and waiting budgets.
        gate = HashGate(running=2, queued=8)
        release = asyncio.Event()

        async def worker() -> None:
            async with gate.slot():
                await release.wait()

        tasks = [asyncio.create_task(worker()) for _ in range(10)]
        for _ in range(50):
            await asyncio.sleep(0.002)
            if gate._running == 2 and gate._waiting == 8:
                break
        assert gate._running == 2
        assert gate._waiting == 8
        with pytest.raises(ApiError) as busy:
            async with gate.slot():
                pass
        assert busy.value.code == "AUTH_BUSY"
        release.set()
        await asyncio.gather(*tasks)

        admin = _new_client(app)
        try:
            await login(admin)
            account = await _add_account(app, "gate-rollback@example.com")
            user = await _login_user(app, "gate-rollback@example.com")

            def boom(*args: Any, **kwargs: Any) -> None:
                raise RuntimeError("audit unavailable")

            monkeypatch.setattr("app.auth.account_admin.write_audit", boom)
            with pytest.raises(RuntimeError):
                await admin.post(f"/api/admin/accounts/{account.id}/disable")
            connection = sqlite3.connect(settings.database_path)
            try:
                disabled = connection.execute(
                    "SELECT is_disabled FROM accounts WHERE id = ?", (account.id,)
                ).fetchone()[0]
                sessions = connection.execute(
                    "SELECT COUNT(*) FROM sessions WHERE account_id = ? AND revoked_at IS NULL",
                    (account.id,),
                ).fetchone()[0]
            finally:
                connection.close()
            assert disabled == 0
            assert sessions == 1
            assert (await user.get("/api/auth/session")).status_code == 200

            async def broken_load(*args: Any, **kwargs: Any):
                raise RuntimeError("database unavailable")

            monkeypatch.setattr("app.auth.middleware.load_session", broken_load)
            assert (await user.get("/api/auth/session")).status_code == 503
            assert (await user.get("/api/health")).status_code == 200
        finally:
            await admin.aclose()
            await user.aclose()


@pytest.mark.asyncio
async def test_secret_markers_do_not_reach_responses_database_or_filenames(
    settings, registry, caplog
):
    password_marker = "gate-password-marker-64-7f3c1a"
    model_key = "sk-gate-model-marker-64-9b2e"
    secret_settings = settings.model_copy(
        update={"llm_api_key": SecretStr(model_key), "visual_api_key": SecretStr(model_key)}
    )
    caplog.set_level("INFO")
    async with _fresh_app(secret_settings, registry) as app:
        admin = _new_client(app)
        try:
            await login(admin)
            account = await _create(admin, "gate-secret@example.com")
            issued = await _issue(admin, account["id"])
            link_token = _token(issued["link"])
            public = await _link_client(app)
            submitted = await _submit(public, link_token, "setup", password_marker)
            assert submitted.status_code == 200, submitted.text
            await public.aclose()

            user = _new_client(app)
            try:
                await login(user, "gate-secret@example.com", password_marker)
                project = (await admin.post("/api/projects", json={})).json()
                uploaded = await admin.post(
                    f"/api/projects/{project['id']}/reference",
                    files={"file": (f"{password_marker}.png", _png(), "image/png")},
                    data={"expected_revision": "0"},
                )
                assert uploaded.status_code == 200, uploaded.text

                body_markers = {
                    password_marker,
                    model_key,
                    link_token,
                    admin.cookies.get("__Host-session"),
                    user.cookies.get("__Host-session"),
                }
                stored_markers = {
                    *body_markers,
                    admin.csrf_token,
                    user.csrf_token,
                }
                assert all(stored_markers)
                for response in (
                    await user.get("/api/auth/session"),
                    await user.get("/api/auth/sessions"),
                    await user.get("/api/auth/security-events"),
                    await user.get("/api/projects"),
                    await admin.get(f"/api/projects/{project['id']}"),
                    await admin.get("/api/auth/session"),
                    await admin.get("/api/auth/sessions"),
                    await admin.get("/api/auth/security-events"),
                    await admin.get("/api/admin/accounts"),
                    await admin.get("/api/admin/security-events"),
                    await admin.get("/api/config/status"),
                ):
                    assert response.status_code == 200, response.text
                    for marker in body_markers:
                        assert marker not in response.text

                assert all(marker not in caplog.text for marker in stored_markers)
                for path in settings.data_dir.rglob("*"):
                    assert all(marker not in path.name for marker in stored_markers)
                    if path.is_file():
                        raw = path.read_bytes()
                        for marker in stored_markers:
                            assert marker.encode() not in raw
            finally:
                await user.aclose()
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_concurrent_initialization_link_and_claim_use_independent_connections(
    settings, registry, tmp_path
):
    # init_admin must serialize across separate engines, not only task-local sessions.
    migrate(settings)
    engines = [create_engine(settings) for _ in range(4)]
    factories = [build_session_factory(engine) for engine in engines]
    try:
        # Open each pool once so WAL activation is not part of the business race.
        for engine in engines:
            async with engine.connect() as connection:
                await connection.exec_driver_sql("SELECT 1")

        results = await asyncio.gather(
            *(
                init_admin(
                    factory,
                    email=TEST_ADMIN_EMAIL,
                    password=TEST_ADMIN_PASSWORD,
                    clock=Clock(),
                    hash_gate=HashGate(),
                )
                for factory in factories
            ),
            return_exceptions=True,
        )
    finally:
        for engine in engines:
            await engine.dispose()
    successes = [item for item in results if isinstance(item, Account)]
    failures = [item for item in results if isinstance(item, AlreadyInitialized)]
    assert len(successes) == 1, results
    assert len(failures) == 3, results
    connection = sqlite3.connect(settings.database_path)
    try:
        accounts = connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        bootstraps = connection.execute("SELECT COUNT(*) FROM instance_bootstrap").fetchone()[0]
    finally:
        connection.close()
    assert accounts == 1
    assert bootstraps == 1

    app = __import__("app.main", fromlist=["create_app"]).create_app(
        settings=settings, registry=registry
    )
    async with app.router.lifespan_context(app):
        await ensure_test_admin(app)
        admin = _new_client(app)
        await login(admin)
        created = await admin.post("/api/admin/accounts", json={"email": "gate-race@example.com"})
        assert created.status_code == 201, created.text
        issued = await admin.post(
            f"/api/admin/accounts/{created.json()['account']['id']}/links",
            json={"purpose": "setup"},
        )
        assert issued.status_code == 201, issued.text
        token = issued.json()["link"].split("#token=", 1)[1]
        first = _new_client(app)
        second = _new_client(app)

        async def submit(client: AuthClient, password: str):
            csrf = await _csrf(client)
            return await client.post(
                "/api/auth/link",
                json={"token": token, "purpose": "setup", "password": password},
                headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": csrf},
            )

        responses = await asyncio.gather(
            submit(first, "first gate password 64"),
            submit(second, "second gate password 64"),
        )
        assert sorted(response.status_code for response in responses) == [200, 400]
        await first.aclose()
        await second.aclose()
        await admin.aclose()

    # claim_legacy opens its own engine for each call; concurrent calls must not duplicate facts.
    # Use a fresh legacy source: the race database above has already lived through the
    # full migration chain and must not be downgraded under a running app fixture.
    legacy_settings = settings.model_copy(
        update={
            "data_dir": tmp_path / "legacy-data",
            "web_dist_dir": tmp_path / "legacy-web",
        }
    )
    seeded = await _seed_source(legacy_settings)
    backup = await _backup(legacy_settings, tmp_path / "gate-backups")
    project_id = seeded["first"]["project_id"]

    async def claim_once():
        return await claim_legacy(
            source=legacy_settings.data_dir,
            backup=backup.backup_dir,
            admin_email="admin@example.com",
            project_ids=[project_id],
            confirmation=CLAIM_CONFIRMATION,
            service_stopped=True,
            candidate_roots=[legacy_settings.data_dir],
        )

    claim_results = await asyncio.gather(claim_once(), claim_once(), return_exceptions=True)
    claims = [item for item in claim_results if isinstance(item, ClaimResult)]
    assert claims, claim_results
    assert any(item.idempotent is False for item in claims)
    connection = sqlite3.connect(legacy_settings.database_path)
    try:
        claim_count = connection.execute("SELECT COUNT(*) FROM legacy_claims").fetchone()[0]
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type = ?",
            ("legacy_projects_claimed",),
        ).fetchone()[0]
        owner = connection.execute(
            "SELECT owner_account_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    assert claim_count == 1
    assert audit_count == 1
    assert owner is not None

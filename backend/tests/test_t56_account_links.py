from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport
from pydantic import ValidationError
from sqlalchemy import func, select

from app.auth.audit import write_audit
from app.auth.clock import FrozenClock
from app.auth.models import Account, AccountLink, AuditEvent, AuthSession
from app.config import Settings
from app.db import read_session, write_transaction
from app.timeutil import format_timestamp
from tests.support.auth import (
    TEST_ORIGIN,
    AuthClient,
    authenticated_client,
    login,
)

FROZEN = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
USER_PASSWORD = "a very good user password 42"


def _client(app: Any, *, raise_app_exceptions: bool = True) -> AuthClient:
    return AuthClient(
        transport=ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
        base_url=TEST_ORIGIN,
    )


async def _create(admin: AuthClient, email: str) -> dict[str, Any]:
    response = await admin.post("/api/admin/accounts", json={"email": email})
    assert response.status_code == 201, response.text
    return response.json()["account"]


async def _issue(admin: AuthClient, account_id: str, purpose: str = "setup") -> dict[str, Any]:
    response = await admin.post(
        f"/api/admin/accounts/{account_id}/links", json={"purpose": purpose}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _token(link: str) -> str:
    assert "#token=" in link, link
    token = link.split("#token=", 1)[1]
    assert token
    return token


async def _link_client(app: Any, *, raise_app_exceptions: bool = True) -> AuthClient:
    client = _client(app, raise_app_exceptions=raise_app_exceptions)
    response = await client.get("/api/auth/csrf")
    assert response.status_code == 200, response.text
    client.csrf_token = response.json()["csrf_token"]
    return client


async def _submit(
    client: AuthClient, token: str, purpose: str, password: str
):
    return await client.post(
        "/api/auth/link",
        json={"token": token, "password": password, "purpose": purpose},
    )


async def _login_response(client: AuthClient, email: str, password: str):
    response = await client.get("/api/auth/csrf")
    assert response.status_code == 200, response.text
    token = response.json()["csrf_token"]
    client.csrf_token = None
    return await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": token},
    )


async def _setup_user(app: Any, email: str, password: str = USER_PASSWORD) -> dict[str, Any]:
    async with authenticated_client(app) as admin:
        account = await _create(admin, email)
        issued = await _issue(admin, account["id"])
    public = await _link_client(app)
    response = await _submit(public, _token(issued["link"]), "setup", password)
    assert response.status_code == 200, response.text
    return account


async def _account(app: Any, account_id: str) -> Account | None:
    async with read_session(app.state.session_factory) as session:
        return await session.get(Account, account_id)


async def _count(app: Any, model: Any) -> int:
    async with read_session(app.state.session_factory) as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _disable(app: Any, account_id: str) -> None:
    now = format_timestamp(app.state.clock.now())
    async with write_transaction(app.state.session_factory) as session:
        account = await session.get(Account, account_id)
        assert account is not None
        account.is_disabled = 1
        account.disabled_at = now
        account.updated_at = now


def test_app_public_url_validation() -> None:
    assert Settings(_env_file=None, app_public_url=" HTTPS://Example.COM/ ").app_public_url == "https://example.com"
    for value in (
        "https://user:pass@example.com",
        "https://example.com/path",
        "https://example.com?x=1",
        "https://example.com#fragment",
        "ftp://example.com",
    ):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, app_public_url=value)
    assert Settings(_env_file=None, app_public_url=None).app_public_url is None


@pytest.mark.asyncio
async def test_admin_creates_pending_account_and_unique_email(app: Any) -> None:
    async with authenticated_client(app) as admin:
        account = await _create(admin, "  User@Example.COM ")
        assert account["email"] == "user@example.com"
        assert account["status"] == "pending_setup"
        assert account["is_admin"] is False
        assert account["last_login_at"] is None

        duplicate = await admin.post("/api/admin/accounts", json={"email": "user@example.com"})
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "ACCOUNT_EXISTS"

        listing = await admin.get("/api/admin/accounts")
        assert listing.status_code == 200
        assert any(item["id"] == account["id"] for item in listing.json()["items"])

        injection = await admin.post(
            "/api/admin/accounts", json={"email": "other@example.com", "is_admin": True}
        )
        assert injection.status_code == 422


@pytest.mark.asyncio
async def test_ordinary_user_cannot_manage_accounts(app: Any) -> None:
    await _setup_user(app, "ordinary@example.com")
    client = _client(app)
    await login(client, "ordinary@example.com", USER_PASSWORD)

    listing = await client.get("/api/admin/accounts")
    assert listing.status_code == 403
    assert listing.json()["error"]["code"] == "FORBIDDEN"
    created = await client.post("/api/admin/accounts", json={"email": "other@example.com"})
    assert created.status_code == 403


@pytest.mark.asyncio
async def test_issuance_uses_configured_url_digest_only_and_supersedes_old(app: Any) -> None:
    async with authenticated_client(app) as admin:
        account = await _create(admin, "link@example.com")
        first = await _issue(admin, account["id"])
        assert first["link"].startswith("https://testserver/set-password#token=")
        assert "?" not in first["link"].split("#", 1)[0]
        first_token = _token(first["link"])

        async with read_session(app.state.session_factory) as session:
            rows = list((await session.scalars(select(AccountLink))).all())
            assert len(rows) == 1
            assert rows[0].token_digest != first_token
            assert len(rows[0].token_digest) == 64
            assert rows[0].used_at is None
            audits = list((await session.scalars(select(AuditEvent))).all())
            assert all(first_token not in (event.detail_json or "") for event in audits)

        status = await _link_client(app)
        response = await status.get("/api/auth/link", headers={"X-Link-Token": first_token})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["purpose"] == "setup"
        assert body["account_email"] == "l***@example.com"
        assert body["expires_at"] == first["expires_at"]

        second = await _issue(admin, account["id"])
        second_token = _token(second["link"])
        assert second_token != first_token
        old_status = await status.get("/api/auth/link", headers={"X-Link-Token": first_token})
        assert old_status.status_code == 400
        new_status = await status.get("/api/auth/link", headers={"X-Link-Token": second_token})
        assert new_status.status_code == 200


@pytest.mark.asyncio
async def test_missing_trusted_app_url_fails_closed(app: Any) -> None:
    async with authenticated_client(app) as admin:
        account = await _create(admin, "no-url@example.com")
        app.state.settings.app_public_url = None
        response = await admin.post(
            f"/api/admin/accounts/{account['id']}/links", json={"purpose": "setup"}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "APP_PUBLIC_URL_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_link_submission_is_atomic_no_session_and_consumes_once(app: Any) -> None:
    async with authenticated_client(app) as admin:
        account = await _create(admin, "consume@example.com")
        issued = await _issue(admin, account["id"])
    token = _token(issued["link"])

    public = await _link_client(app)
    response = await _submit(public, token, "setup", USER_PASSWORD)
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}
    assert "__Host-session" not in response.headers.get("set-cookie", "")

    stored = await _account(app, account["id"])
    assert stored is not None
    assert stored.status == "active"
    assert stored.password_hash
    assert stored.password_hash != USER_PASSWORD
    async with read_session(app.state.session_factory) as session:
        user_sessions = await session.scalar(
            select(func.count())
            .select_from(AuthSession)
            .where(AuthSession.account_id == account["id"])
        )
        assert user_sessions == 0

    async with read_session(app.state.session_factory) as session:
        link = await session.scalar(
            select(AccountLink).where(AccountLink.account_id == account["id"])
        )
        assert link is not None
        assert link.used_at is not None

    repeat = await _submit(public, token, "setup", "another very good password 43")
    assert repeat.status_code == 400
    assert repeat.json()["error"]["code"] == "LINK_INVALID"

    user = _client(app)
    await login(user, "consume@example.com", USER_PASSWORD)


@pytest.mark.asyncio
async def test_reset_issuance_keeps_old_password_and_consumption_revokes_sessions(app: Any) -> None:
    async with authenticated_client(app) as admin:
        account = await _create(admin, "reset@example.com")
        setup = await _issue(admin, account["id"])
    public = await _link_client(app)
    assert (await _submit(public, _token(setup["link"]), "setup", USER_PASSWORD)).status_code == 200

    user = _client(app)
    await login(user, "reset@example.com", USER_PASSWORD)

    async with authenticated_client(app) as admin:
        reset = await _issue(admin, account["id"], "reset")
    # Issuing a reset link must not change the old password.
    old_login = _client(app)
    await login(old_login, "reset@example.com", USER_PASSWORD)
    assert (await _account(app, account["id"])).password_hash is not None

    reset_token = _token(reset["link"])
    status_client = await _link_client(app)
    status = await status_client.get("/api/auth/link", headers={"X-Link-Token": reset_token})
    assert status.status_code == 200
    assert (await _account(app, account["id"])).password_hash is not None

    reset_public = await _link_client(app)
    response = await _submit(reset_public, reset_token, "reset", "a brand new password 44")
    assert response.status_code == 200, response.text

    assert (await user.get("/api/auth/session")).status_code == 401
    old = _client(app)
    old_response = await _login_response(old, "reset@example.com", USER_PASSWORD)
    assert old_response.status_code == 401
    new = _client(app)
    await login(new, "reset@example.com", "a brand new password 44")


@pytest.mark.asyncio
async def test_expired_wrong_purpose_disabled_and_wrong_token_are_rejected(app: Any) -> None:
    app.state.clock = FrozenClock(app.state.clock.now())
    async with authenticated_client(app) as admin:
        expired_account = await _create(admin, "expired@example.com")
        expired = await _issue(admin, expired_account["id"])
        disabled_account = await _create(admin, "disabled@example.com")
        disabled = await _issue(admin, disabled_account["id"])
        wrong_purpose_account = await _create(admin, "purpose@example.com")
        wrong_purpose = await _issue(admin, wrong_purpose_account["id"])
    await _disable(app, disabled_account["id"])

    app.state.clock.advance(30 * 60 + 1)
    public = await _link_client(app)
    expired_response = await _submit(public, _token(expired["link"]), "setup", USER_PASSWORD)
    assert expired_response.status_code == 400
    disabled_response = await _submit(public, _token(disabled["link"]), "setup", USER_PASSWORD)
    assert disabled_response.status_code == 400
    purpose_response = await _submit(
        public, _token(wrong_purpose["link"]), "reset", USER_PASSWORD
    )
    assert purpose_response.status_code == 400
    wrong_token = await _submit(public, "not-a-real-token", "setup", USER_PASSWORD)
    assert wrong_token.status_code == 400
    assert (await _account(app, expired_account["id"])).password_hash is None


@pytest.mark.asyncio
async def test_concurrent_consumption_has_exactly_one_winner(app: Any) -> None:
    async with authenticated_client(app) as admin:
        account = await _create(admin, "race@example.com")
        issued = await _issue(admin, account["id"])
    token = _token(issued["link"])
    first = await _link_client(app)
    second = await _link_client(app)
    password_a = "first concurrent password 45"
    password_b = "second concurrent password 46"
    responses = await asyncio.gather(
        _submit(first, token, "setup", password_a),
        _submit(second, token, "setup", password_b),
    )
    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 400], [response.text for response in responses]
    stored = await _account(app, account["id"])
    assert stored is not None and stored.password_hash
    async with read_session(app.state.session_factory) as session:
        used = await session.scalar(
            select(func.count()).select_from(AccountLink).where(AccountLink.used_at.is_not(None))
        )
        assert used == 1
    winner = _client(app)
    accepted_password = password_a if responses[0].status_code == 200 else password_b
    await login(winner, "race@example.com", accepted_password)


@pytest.mark.asyncio
async def test_link_rate_limits_target(app: Any) -> None:
    async with authenticated_client(app) as admin:
        account = await _create(admin, "limits@example.com")
        for _ in range(3):
            await _issue(admin, account["id"])
        limited = await admin.post(
            f"/api/admin/accounts/{account['id']}/links", json={"purpose": "setup"}
        )
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert "Retry-After" in limited.headers


@pytest.mark.asyncio
async def test_link_rate_limits_admin(app: Any) -> None:
    async with authenticated_client(app) as admin:
        for index in range(3):
            account = await _create(admin, f"admin-limit-{index}@example.com")
            for _ in range(3):
                await _issue(admin, account["id"])
        final_account = await _create(admin, "admin-limit-final@example.com")
        await _issue(admin, final_account["id"])
        over = await admin.post(
            f"/api/admin/accounts/{final_account['id']}/links", json={"purpose": "setup"}
        )
        assert over.status_code == 429
        assert over.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_link_verification_credential_budget(app: Any) -> None:
    async with authenticated_client(app) as admin:
        account = await _create(admin, "verify-limit@example.com")
        issued = await _issue(admin, account["id"])
    public = await _link_client(app)
    for _ in range(5):
        response = await _submit(public, _token(issued["link"]), "setup", "short")
        assert response.status_code == 422, response.text
    limited = await _submit(public, _token(issued["link"]), "setup", USER_PASSWORD)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_link_verification_source_budget(app: Any) -> None:
    public = await _link_client(app)
    for index in range(30):
        response = await _submit(public, f"unknown-token-{index}", "setup", USER_PASSWORD)
        assert response.status_code == 400, response.text
    limited = await _submit(public, "unknown-token-final", "setup", USER_PASSWORD)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_account_link_and_password(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit store unavailable")

    async with authenticated_client(app) as admin:
        before = await _count(app, Account)
        monkeypatch.setattr("app.auth.links.write_audit", boom)
        broken = _client(app, raise_app_exceptions=False)
        await login(broken, "admin@example.com", "correct horse battery staple 42")
        created = await broken.post("/api/admin/accounts", json={"email": "rollback@example.com"})
        assert created.status_code == 500
        assert await _count(app, Account) == before

        monkeypatch.setattr("app.auth.links.write_audit", write_audit)
        account = await _create(admin, "rollback-link@example.com")
        monkeypatch.setattr("app.auth.links.write_audit", boom)
        issued = await broken.post(
            f"/api/admin/accounts/{account['id']}/links", json={"purpose": "setup"}
        )
        assert issued.status_code == 500
        assert await _count(app, AccountLink) == 0

        monkeypatch.setattr("app.auth.links.write_audit", write_audit)
        link = await _issue(admin, account["id"])
        public = await _link_client(app, raise_app_exceptions=False)
        monkeypatch.setattr("app.auth.links.write_audit", boom)
        submitted = await public.post(
            "/api/auth/link",
            json={"token": _token(link["link"]), "password": USER_PASSWORD, "purpose": "setup"},
        )
        assert submitted.status_code == 500
        stored = await _account(app, account["id"])
        assert stored is not None and stored.password_hash is None
        async with read_session(app.state.session_factory) as session:
            row = await session.scalar(
                select(AccountLink).where(AccountLink.account_id == account["id"])
            )
            assert row is not None and row.used_at is None


@pytest.mark.asyncio
async def test_public_link_routes_and_private_headers(app: Any) -> None:
    anonymous = _client(app)
    projects = await anonymous.get("/api/projects")
    assert projects.status_code == 401
    assert projects.json()["error"]["code"] == "AUTH_REQUIRED"

    status = await anonymous.get("/api/auth/link", headers={"X-Link-Token": "bogus"})
    assert status.status_code == 400
    assert status.headers["cache-control"] == "no-store"
    assert status.headers["referrer-policy"] == "no-referrer"
    assert status.headers["x-frame-options"] == "DENY"

    no_csrf = await anonymous.post(
        "/api/auth/link", json={"token": "bogus", "password": USER_PASSWORD, "purpose": "setup"}
    )
    assert no_csrf.status_code == 403

    with_csrf = await _link_client(app)
    invalid = await _submit(with_csrf, "bogus", "setup", USER_PASSWORD)
    assert invalid.status_code == 400
    assert "__Host-session" not in invalid.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_migration_creates_account_links_table(app: Any) -> None:
    async with read_session(app.state.session_factory) as session:
        result = await session.execute(select(func.count()).select_from(AccountLink))
        assert int(result.scalar_one()) == 0

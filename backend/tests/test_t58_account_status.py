from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.auth.clock import FrozenClock
from app.auth.models import Account, AccountLink, AuditEvent
from app.auth.passwords import hash_password
from app.auth.service import normalize_email
from app.db import read_session, write_transaction
from app.timeutil import format_timestamp
from tests.support.auth import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_ORIGIN,
    AuthClient,
    ensure_test_admin,
    login,
)

FROZEN = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
USER_PASSWORD = "correct horse battery staple 58"


@asynccontextmanager
async def _fresh_app(settings: Any, registry: Any):
    from tests.conftest import migrate

    migrate(settings)
    from app.main import create_app

    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        await ensure_test_admin(application)
        yield application


def _new_client(app: Any) -> AuthClient:
    return AuthClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN)


async def _try_login(client: AuthClient, email: str, password: str):
    csrf = await client.get("/api/auth/csrf")
    assert csrf.status_code == 200, csrf.text
    token = csrf.json()["csrf_token"]
    response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": token},
    )
    if response.status_code == 200:
        client.csrf_token = response.json()["csrf_token"]
    return response


async def _add_account(app: Any, email: str, password: str) -> Account:
    now = app.state.clock.now()
    password_hash = await app.state.hash_gate.run(hash_password, password)
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


async def _add_pending(app: Any, email: str) -> Account:
    now = app.state.clock.now()
    async with write_transaction(app.state.session_factory) as session:
        account = Account(
            id=str(uuid.uuid4()),
            email_normalized=normalize_email(email),
            password_hash=None,
            password_set_at=None,
            password_policy_version=None,
            is_admin=0,
            is_disabled=0,
            session_epoch=0,
            created_at=format_timestamp(now),
            updated_at=format_timestamp(now),
        )
        session.add(account)
        await session.flush()
        return account


async def _account(app: Any, account_id: str) -> Account | None:
    async with read_session(app.state.session_factory) as session:
        return await session.get(Account, account_id)


async def _account_links(app: Any, account_id: str) -> list[AccountLink]:
    async with read_session(app.state.session_factory) as session:
        return list(
            await session.scalars(select(AccountLink).where(AccountLink.account_id == account_id))
        )


async def _audit(app: Any, event_type: str) -> list[AuditEvent]:
    async with read_session(app.state.session_factory) as session:
        return list(
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == event_type)
                .order_by(AuditEvent.occurred_at)
            )
        )


@pytest.mark.asyncio
async def test_disable_revokes_sessions_and_links_and_blocks_next_request(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin = _new_client(app)
        user = _new_client(app)
        try:
            await login(admin)
            account = await _add_account(app, "disabled@example.com", USER_PASSWORD)
            issue = await admin.post(
                f"/api/admin/accounts/{account.id}/links", json={"purpose": "reset"}
            )
            assert issue.status_code == 201, issue.text
            link_token = issue.json()["link"].split("#token=", 1)[1]
            assert (
                await _try_login(user, "disabled@example.com", USER_PASSWORD)
            ).status_code == 200

            response = await admin.post(f"/api/admin/accounts/{account.id}/disable")
            assert response.status_code == 200, response.text
            body = response.json()["account"]
            assert body["status"] == "disabled"
            assert body["is_admin"] is False
            assert (await user.get("/api/auth/sessions")).status_code == 401
            assert (
                await _try_login(_new_client(app), "disabled@example.com", USER_PASSWORD)
            ).status_code == 401
            assert (
                await _try_login(_new_client(app), "disabled@example.com", "wrong")
            ).status_code == 401
            inspect = await _new_client(app).get(
                "/api/auth/link", headers={"X-Link-Token": link_token}
            )
            assert inspect.status_code == 400
            links = await _account_links(app, account.id)
            assert len(links) == 1
            assert links[0].revoked_reason == "account_disabled"
            events = await _audit(app, "account_disabled")
            assert len(events) == 1
            assert events[0].before_json is not None
            assert "disabled" in events[0].after_json
            assert USER_PASSWORD not in (events[0].detail_json or "")
        finally:
            await admin.aclose()
            await user.aclose()


@pytest.mark.asyncio
async def test_restore_keeps_old_sessions_and_one_time_links_dead(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin = _new_client(app)
        user = _new_client(app)
        try:
            await login(admin)
            account = await _add_account(app, "restored@example.com", USER_PASSWORD)
            issue = await admin.post(
                f"/api/admin/accounts/{account.id}/links", json={"purpose": "reset"}
            )
            assert issue.status_code == 201
            link_token = issue.json()["link"].split("#token=", 1)[1]
            assert (
                await _try_login(user, "restored@example.com", USER_PASSWORD)
            ).status_code == 200
            before_session = await user.get("/api/auth/sessions")
            assert before_session.status_code == 200

            assert (
                await admin.post(f"/api/admin/accounts/{account.id}/disable")
            ).status_code == 200
            response = await admin.post(f"/api/admin/accounts/{account.id}/restore")
            assert response.status_code == 200, response.text
            assert response.json()["account"]["status"] == "active"
            assert (await user.get("/api/auth/sessions")).status_code == 401
            inspect = await _new_client(app).get(
                "/api/auth/link", headers={"X-Link-Token": link_token}
            )
            assert inspect.status_code == 400
            assert (
                await _try_login(_new_client(app), "restored@example.com", USER_PASSWORD)
            ).status_code == 200
            events = await _audit(app, "account_restored")
            assert len(events) == 1
            assert "active" in (events[0].after_json or "")
        finally:
            await admin.aclose()
            await user.aclose()


@pytest.mark.asyncio
async def test_restore_of_pending_account_does_not_skip_setup(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin = _new_client(app)
        try:
            await login(admin)
            account = await _add_pending(app, "pending-disabled@example.com")
            issue = await admin.post(
                f"/api/admin/accounts/{account.id}/links", json={"purpose": "setup"}
            )
            assert issue.status_code == 201
            link_token = issue.json()["link"].split("#token=", 1)[1]
            assert (
                await admin.post(f"/api/admin/accounts/{account.id}/disable")
            ).status_code == 200
            response = await admin.post(f"/api/admin/accounts/{account.id}/restore")
            assert response.status_code == 200
            assert response.json()["account"]["status"] == "pending_setup"
            assert (
                await _new_client(app).get("/api/auth/link", headers={"X-Link-Token": link_token})
            ).status_code == 400
            assert (
                await _try_login(_new_client(app), "pending-disabled@example.com", "anything")
            ).status_code == 401
            links = await _account_links(app, account.id)
            assert links[0].revoked_reason in {"account_disabled", "account_restored"}
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_only_admin_can_change_status_and_admin_targets_are_not_addressable(
    settings, registry
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin = _new_client(app)
        ordinary = _new_client(app)
        try:
            await login(admin)
            victim = await _add_account(app, "victim@example.com", USER_PASSWORD)
            assert (
                await _try_login(ordinary, "victim@example.com", USER_PASSWORD)
            ).status_code == 200
            denied = await ordinary.post(f"/api/admin/accounts/{victim.id}/disable")
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "FORBIDDEN"
            assert (await _account(app, victim.id)).is_disabled == 0
            missing = await admin.post("/api/admin/accounts/does-not-exist/disable")
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
            admin_id = (await _account_by_email(app, TEST_ADMIN_EMAIL)).id
            hidden = await admin.post(f"/api/admin/accounts/{admin_id}/disable")
            assert hidden.status_code == 404
            assert hidden.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
        finally:
            await admin.aclose()
            await ordinary.aclose()


async def _account_by_email(app: Any, email: str) -> Account:
    async with read_session(app.state.session_factory) as session:
        account = await session.scalar(
            select(Account).where(Account.email_normalized == normalize_email(email))
        )
        assert account is not None
        return account


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_disable_and_keeps_session_valid(
    settings, registry, monkeypatch: pytest.MonkeyPatch
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin = _new_client(app)
        user = _new_client(app)
        try:
            await login(admin)
            account = await _add_account(app, "rollback@example.com", USER_PASSWORD)
            assert (
                await _try_login(user, "rollback@example.com", USER_PASSWORD)
            ).status_code == 200

            def boom(*args: Any, **kwargs: Any) -> None:
                raise RuntimeError("audit unavailable")

            monkeypatch.setattr("app.auth.account_admin.write_audit", boom)
            with pytest.raises(RuntimeError):
                await admin.post(f"/api/admin/accounts/{account.id}/disable")
            stored = await _account(app, account.id)
            assert stored is not None
            assert stored.is_disabled == 0
            assert (await user.get("/api/auth/sessions")).status_code == 200
        finally:
            await admin.aclose()
            await user.aclose()


@pytest.mark.asyncio
async def test_personal_security_events_are_self_only_and_have_safe_shape(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin = _new_client(app)
        user = _new_client(app)
        try:
            await login(admin)
            account = await _add_account(app, "events@example.com", USER_PASSWORD)
            issue = await admin.post(
                f"/api/admin/accounts/{account.id}/links", json={"purpose": "reset"}
            )
            link_token = issue.json()["link"].split("#token=", 1)[1]
            assert (
                await admin.post(f"/api/admin/accounts/{account.id}/disable")
            ).status_code == 200
            assert (
                await admin.post(f"/api/admin/accounts/{account.id}/restore")
            ).status_code == 200
            assert (await _try_login(user, "events@example.com", USER_PASSWORD)).status_code == 200
            response = await user.get("/api/auth/security-events")
            assert response.status_code == 200, response.text
            items = response.json()["items"]
            assert items
            assert {item["event_type"] for item in items} >= {
                "account_link_issued",
                "account_disabled",
                "account_restored",
                "login_success",
            }
            assert all(
                set(item)
                == {
                    "id",
                    "occurred_at",
                    "event_type",
                    "result",
                    "reason_code",
                    "actor_kind",
                    "detail",
                }
                for item in items
            )
            text = response.text
            assert link_token not in text
            assert USER_PASSWORD not in text
            assert TEST_ADMIN_PASSWORD not in text
            assert TEST_ADMIN_EMAIL not in text
        finally:
            await admin.aclose()
            await user.aclose()


@pytest.mark.asyncio
async def test_admin_security_events_expose_before_after_and_mask_unknown_accounts(
    settings, registry
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin = _new_client(app)
        try:
            await login(admin)
            account = await _add_account(app, "known@example.com", USER_PASSWORD)
            assert (
                await admin.post(f"/api/admin/accounts/{account.id}/disable")
            ).status_code == 200
            unknown_id = str(uuid.uuid4())
            async with write_transaction(app.state.session_factory) as session:
                session.add(
                    AuditEvent(
                        id=str(uuid.uuid4()),
                        occurred_at=format_timestamp(app.state.clock.now()),
                        event_type="permission_denied",
                        actor_kind="anonymous",
                        target_kind="account",
                        target_id=unknown_id,
                        result="denied",
                        reason_code="ADMIN_REQUIRED",
                        correlation_id=str(uuid.uuid4()),
                        detail_json='{"password":"secret","token":"tok","purpose":"setup"}',
                    )
                )
            response = await admin.get("/api/admin/security-events")
            assert response.status_code == 200, response.text
            items = response.json()["items"]
            disabled = next(item for item in items if item["event_type"] == "account_disabled")
            assert disabled["actor_label"] == TEST_ADMIN_EMAIL
            assert disabled["target_label"] == "known@example.com"
            assert disabled["before"]["status"] == "active"
            assert disabled["after"]["status"] == "disabled"
            unknown = next(item for item in items if item["event_type"] == "permission_denied")
            assert unknown_id not in response.text
            assert unknown["target_label"].startswith("未知账号（")
            assert unknown["target_ref"] != unknown_id
            assert unknown["detail"] == {"purpose": "setup"}
            assert unknown["before"] is None
            assert unknown["after"] is None
            assert "secret" not in response.text
            assert "tok" not in response.text
        finally:
            await admin.aclose()


@pytest.mark.asyncio
async def test_ordinary_user_cannot_read_admin_security_events(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        user = _new_client(app)
        try:
            await _add_account(app, "ordinary-events@example.com", USER_PASSWORD)
            assert (
                await _try_login(user, "ordinary-events@example.com", USER_PASSWORD)
            ).status_code == 200
            response = await user.get("/api/admin/security-events")
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "FORBIDDEN"
        finally:
            await user.aclose()


@pytest.mark.asyncio
async def test_client_role_fields_do_not_authorize_admin_calls(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin = _new_client(app)
        user = _new_client(app)
        try:
            await login(admin)
            account = await _add_account(app, "role-fields@example.com", USER_PASSWORD)
            assert (
                await _try_login(user, "role-fields@example.com", USER_PASSWORD)
            ).status_code == 200

            denied_create = await user.post(
                "/api/admin/accounts",
                json={"email": "sneaky@example.com", "is_admin": True, "role": "admin"},
            )
            assert denied_create.status_code == 422
            async with read_session(app.state.session_factory) as session:
                created = await session.scalar(
                    select(Account).where(
                        Account.email_normalized == normalize_email("sneaky@example.com")
                    )
                )
            assert created is None

            denied_read = await user.get(
                "/api/admin/security-events", params={"is_admin": "true", "role": "admin"}
            )
            assert denied_read.status_code == 403
            assert denied_read.json()["error"]["code"] == "FORBIDDEN"

            events = await _audit(app, "permission_denied")
            assert len(events) == 1
            assert events[0].actor_account_id == account.id
            assert events[0].target_account_id == account.id
            assert events[0].result == "denied"
            personal = await user.get("/api/auth/security-events")
            assert personal.status_code == 200
            assert any(
                item["event_type"] == "permission_denied" for item in personal.json()["items"]
            )
        finally:
            await admin.aclose()
            await user.aclose()

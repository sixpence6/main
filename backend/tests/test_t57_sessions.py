from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport
from sqlalchemy import func, select

from app.auth.clock import FrozenClock
from app.auth.models import Account, AccountLink, AuditEvent, AuthSession
from app.auth.passwords import hash_password
from app.auth.personal import RecoveryNotFound, list_active_sessions, recover_admin
from app.auth.service import normalize_email
from app.auth.tokens import token_digest
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

FROZEN = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
NEW_PASSWORD = "new correct horse battery staple 57"


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


async def _count(app: Any, model: Any) -> int:
    async with read_session(app.state.session_factory) as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _audit(app: Any, event_type: str) -> list[AuditEvent]:
    async with read_session(app.state.session_factory) as session:
        return list(
            await session.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == event_type)
                .order_by(AuditEvent.occurred_at)
            )
        )


async def _sessions(app: Any) -> list[AuthSession]:
    async with read_session(app.state.session_factory) as session:
        return list(await session.scalars(select(AuthSession)))


@pytest.mark.asyncio
async def test_password_change_verifies_current_and_revokes_all_sessions(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        first = _new_client(app)
        second = _new_client(app)
        try:
            await login(first)
            await login(second)
            response = await first.post(
                "/api/auth/password/change",
                json={
                    "current_password": "definitely wrong",
                    "new_password": NEW_PASSWORD,
                },
            )
            assert response.status_code == 400, response.text
            assert response.json()["error"]["code"] == "CURRENT_PASSWORD_INVALID"
            assert (await first.get("/api/auth/sessions")).status_code == 200
            assert (
                await _try_login(_new_client(app), TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
            ).status_code == 200

            response = await first.post(
                "/api/auth/password/change",
                json={
                    "current_password": TEST_ADMIN_PASSWORD,
                    "new_password": NEW_PASSWORD,
                },
            )
            assert response.status_code == 204, response.text
            assert (await first.get("/api/auth/sessions")).status_code == 401
            assert (await second.get("/api/auth/sessions")).status_code == 401
            assert (
                await _try_login(_new_client(app), TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
            ).status_code == 401
            assert (
                await _try_login(_new_client(app), TEST_ADMIN_EMAIL, NEW_PASSWORD)
            ).status_code == 200
            events = await _audit(app, "password_changed")
            assert len(events) == 1
            assert events[0].result == "success"
            assert NEW_PASSWORD not in (events[0].detail_json or "")
            assert TEST_ADMIN_PASSWORD not in (events[0].detail_json or "")
        finally:
            await first.aclose()
            await second.aclose()


@pytest.mark.asyncio
async def test_password_change_rejects_policy_failure_without_touching_old_password(
    settings, registry
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        client = _new_client(app)
        try:
            await login(client)
            response = await client.post(
                "/api/auth/password/change",
                json={"current_password": TEST_ADMIN_PASSWORD, "new_password": "short"},
            )
            assert response.status_code == 422, response.text
            assert response.json()["error"]["code"] == "PASSWORD_TOO_SHORT"
            assert (
                await _try_login(_new_client(app), TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
            ).status_code == 200
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_password_change_audit_failure_rolls_back_hash_epoch_and_sessions(
    settings, registry, monkeypatch: pytest.MonkeyPatch
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        client = _new_client(app)
        try:
            await login(client)
            async with read_session(app.state.session_factory) as session:
                before = await session.scalar(
                    select(Account).where(Account.email_normalized == TEST_ADMIN_EMAIL)
                )
                assert before is not None
                before_hash = before.password_hash
                before_epoch = before.session_epoch

            def broken_audit(*args: Any, **kwargs: Any):
                raise RuntimeError("audit unavailable")

            monkeypatch.setattr("app.auth.personal.write_audit", broken_audit)
            with pytest.raises(RuntimeError):
                await client.post(
                    "/api/auth/password/change",
                    json={
                        "current_password": TEST_ADMIN_PASSWORD,
                        "new_password": NEW_PASSWORD,
                    },
                )
            async with read_session(app.state.session_factory) as session:
                after = await session.scalar(
                    select(Account).where(Account.email_normalized == TEST_ADMIN_EMAIL)
                )
                assert after is not None
                assert after.password_hash == before_hash
                assert after.session_epoch == before_epoch
            sessions = await _sessions(app)
            assert all(record.revoked_at is None for record in sessions)
            assert (
                await _try_login(_new_client(app), TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
            ).status_code == 200
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_session_list_returns_only_own_active_sessions_without_secrets(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin_one = _new_client(app)
        admin_two = _new_client(app)
        other = _new_client(app)
        try:
            await login(admin_one)
            await login(admin_two)
            await _add_account(app, "user@example.com", "user correct horse battery 57")
            await login(other, email="user@example.com", password="user correct horse battery 57")

            response = await admin_one.get("/api/auth/sessions")
            assert response.status_code == 200, response.text
            body = response.json()["items"]
            assert len(body) == 2
            assert sum(1 for item in body if item["current"]) == 1
            expected = {
                "id",
                "current",
                "created_at",
                "last_activity_at",
                "idle_expires_at",
                "absolute_expires_at",
                "client_info",
            }
            assert all(set(item) == expected for item in body)
            raw = str(response.json())
            for secret in ("token", "csrf", "digest", "source_ref"):
                assert secret not in raw.lower()
            user_sessions = (await other.get("/api/auth/sessions")).json()["items"]
            assert len(user_sessions) == 1
            assert user_sessions[0]["current"] is True
        finally:
            await admin_one.aclose()
            await admin_two.aclose()
            await other.aclose()


@pytest.mark.asyncio
async def test_revoke_one_session_is_immediate_and_unknown_cross_account_ids_are_redacted(
    settings, registry
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        admin_one = _new_client(app)
        admin_two = _new_client(app)
        other = _new_client(app)
        try:
            await login(admin_one)
            await login(admin_two)
            await _add_account(app, "other@example.com", "other correct horse battery 57")
            await login(other, email="other@example.com", password="other correct horse battery 57")

            sessions = (await admin_one.get("/api/auth/sessions")).json()["items"]
            current = next(item for item in sessions if item["current"])
            target = next(item for item in sessions if not item["current"])
            assert target["id"]

            assert (await admin_two.get("/api/auth/sessions")).status_code == 200
            response = await admin_one.delete(f"/api/auth/sessions/{target['id']}")
            assert response.status_code == 204, response.text
            assert (await admin_two.get("/api/auth/sessions")).status_code == 401
            assert (await admin_one.get("/api/auth/sessions")).status_code == 200

            other_sessions = (await other.get("/api/auth/sessions")).json()["items"]
            assert (
                await admin_one.delete(f"/api/auth/sessions/{other_sessions[0]['id']}")
            ).status_code == 404
            missing = await admin_one.delete(f"/api/auth/sessions/{uuid.uuid4()}")
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "SESSION_NOT_FOUND"
            assert current["id"]
        finally:
            await admin_one.aclose()
            await admin_two.aclose()
            await other.aclose()


@pytest.mark.asyncio
async def test_revoke_current_clears_cookies_and_next_request_is_unauthorized(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        client = _new_client(app)
        try:
            await login(client)
            current = next(
                item
                for item in (await client.get("/api/auth/sessions")).json()["items"]
                if item["current"]
            )
            response = await client.delete(f"/api/auth/sessions/{current['id']}")
            assert response.status_code == 204
            assert (await client.get("/api/auth/sessions")).status_code == 401
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_revoke_other_sessions_keeps_current_and_does_not_touch_other_accounts(
    settings, registry
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        first = _new_client(app)
        second = _new_client(app)
        third = _new_client(app)
        external = _new_client(app)
        try:
            await login(first)
            await login(second)
            await login(third)
            await _add_account(app, "external@example.com", "external correct horse battery 57")
            await login(
                external, email="external@example.com", password="external correct horse battery 57"
            )
            response = await first.post("/api/auth/sessions/revoke-others")
            assert response.status_code == 204, response.text
            assert (await first.get("/api/auth/sessions")).status_code == 200
            assert (await second.get("/api/auth/sessions")).status_code == 401
            assert (await third.get("/api/auth/sessions")).status_code == 401
            assert (await external.get("/api/auth/sessions")).status_code == 200
            events = await _audit(app, "sessions_revoked")
            assert any((event.detail_json or "").find("others") >= 0 for event in events)
        finally:
            await first.aclose()
            await second.aclose()
            await third.aclose()
            await external.aclose()


@pytest.mark.asyncio
async def test_session_timeouts_are_reflected_by_active_session_projection(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        client = _new_client(app)
        try:
            await login(client)
            async with read_session(app.state.session_factory) as session:
                account = await session.scalar(
                    select(Account).where(Account.email_normalized == TEST_ADMIN_EMAIL)
                )
                assert account is not None
            assert (
                len(
                    await list_active_sessions(
                        app.state.session_factory, account_id=account.id, now=FROZEN
                    )
                )
                == 1
            )
            assert not await list_active_sessions(
                app.state.session_factory,
                account_id=account.id,
                now=FROZEN + timedelta(seconds=1801),
            )
            assert not await list_active_sessions(
                app.state.session_factory,
                account_id=account.id,
                now=FROZEN + timedelta(hours=12, seconds=1),
            )
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_local_recover_admin_resets_password_revokes_sessions_and_links_and_audits(
    settings, registry
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        first = _new_client(app)
        second = _new_client(app)
        try:
            await login(first)
            await login(second)
            async with write_transaction(app.state.session_factory) as session:
                admin = await session.scalar(
                    select(Account).where(Account.email_normalized == TEST_ADMIN_EMAIL)
                )
                assert admin is not None
                link = AccountLink(
                    id=str(uuid.uuid4()),
                    account_id=admin.id,
                    purpose="reset",
                    token_digest=token_digest("unused-recovery-link"),
                    created_at=format_timestamp(FROZEN),
                    expires_at=format_timestamp(FROZEN + timedelta(minutes=30)),
                    created_by=admin.id,
                )
                session.add(link)
            await recover_admin(
                app.state.session_factory,
                email=TEST_ADMIN_EMAIL,
                password=NEW_PASSWORD,
                clock=app.state.clock,
                hash_gate=app.state.hash_gate,
            )
            assert (await first.get("/api/auth/sessions")).status_code == 401
            assert (await second.get("/api/auth/sessions")).status_code == 401
            assert (
                await _try_login(_new_client(app), TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD)
            ).status_code == 401
            assert (
                await _try_login(_new_client(app), TEST_ADMIN_EMAIL, NEW_PASSWORD)
            ).status_code == 200
            async with read_session(app.state.session_factory) as session:
                record = await session.scalar(select(AccountLink))
                assert record is not None and record.revoked_at is not None
                assert record.revoked_reason == "admin_recovery"
            events = await _audit(app, "admin_recovered")
            assert len(events) == 1
            assert events[0].actor_kind == "local_maintainer"
            assert "token" not in (events[0].detail_json or "").lower()
            assert NEW_PASSWORD not in (events[0].detail_json or "")
        finally:
            await first.aclose()
            await second.aclose()


@pytest.mark.asyncio
async def test_recover_admin_rejects_non_admin_target_and_audit_failure_rolls_back(
    settings, registry, monkeypatch
):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        client = _new_client(app)
        try:
            await login(client)
            async with read_session(app.state.session_factory) as session:
                admin = await session.scalar(
                    select(Account).where(Account.email_normalized == TEST_ADMIN_EMAIL)
                )
                assert admin is not None
                admin_id = admin.id
                old_hash = admin.password_hash
                old_epoch = admin.session_epoch

            with pytest.raises(RecoveryNotFound):
                await recover_admin(
                    app.state.session_factory,
                    email="ordinary@example.com",
                    password=NEW_PASSWORD,
                    clock=app.state.clock,
                    hash_gate=app.state.hash_gate,
                )

            def broken_audit(*args: Any, **kwargs: Any):
                raise RuntimeError("audit unavailable")

            monkeypatch.setattr("app.auth.personal.write_audit", broken_audit)
            with pytest.raises(RuntimeError):
                await recover_admin(
                    app.state.session_factory,
                    email=TEST_ADMIN_EMAIL,
                    password=NEW_PASSWORD,
                    clock=app.state.clock,
                    hash_gate=app.state.hash_gate,
                )
            async with read_session(app.state.session_factory) as session:
                current = await session.get(Account, admin_id)
                assert current is not None
                assert current.password_hash == old_hash
                assert current.session_epoch == old_epoch
            assert (await client.get("/api/auth/sessions")).status_code == 200
        finally:
            await client.aclose()


def test_recovery_has_no_network_route_and_cli_has_no_password_option() -> None:
    from app.auth import cli

    help_text = cli.build_parser().format_help()
    assert "recover-admin" in help_text
    assert "--password" not in help_text
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["recover-admin", "--password", "secret"])
    assert cli.main(["recover-admin"]) == 2


@pytest.mark.asyncio
async def test_recovery_path_is_not_public_in_openapi(client):
    spec = (await client.get("/api/openapi.json")).json()
    assert not any("recover" in path for path in spec["paths"])

from __future__ import annotations

import asyncio
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.auth.audit import write_audit
from app.auth.clock import FrozenClock
from app.auth.models import Account, AuditEvent, AuthSession, InstanceBootstrap, RateLimitFact
from app.auth.passwords import (
    ARGON2_MEMORY_COST_KIB,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    AuthUnavailable,
    HashGate,
    PasswordPolicyError,
    hash_password,
    password_needs_rehash,
    validate_password,
    verify_password,
)
from app.auth.rate_limit import LimitRule, RateLimited, reserve_attempts
from app.auth.service import AlreadyInitialized, InvalidEmail, init_admin, login, normalize_email
from app.auth.sessions import (
    PREAUTH_CSRF_COOKIE,
    SESSION_COOKIE,
    revoke_all_for_account,
)
from app.auth.tokens import source_ref
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.main import create_app
from app.timeutil import format_timestamp
from tests.conftest import migrate
from tests.support.auth import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_ORIGIN,
    AuthClient,
)

FROZEN = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


@asynccontextmanager
async def _fresh_app(settings: Any, registry: Any):
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        yield application


def _new_client(app: Any) -> AuthClient:
    return AuthClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN)


async def _csrf(client: AuthClient) -> str:
    response = await client.get("/api/auth/csrf")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


async def _login_attempt(
    client: AuthClient,
    email: str = TEST_ADMIN_EMAIL,
    password: str = TEST_ADMIN_PASSWORD,
):
    token = await _csrf(client)
    client.csrf_token = None
    return await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": token},
    )


async def _login(client: AuthClient, **kwargs: Any):
    response = await _login_attempt(client, **kwargs)
    assert response.status_code == 200, response.text
    client.csrf_token = response.json()["csrf_token"]
    return response


async def _add_account(
    app: Any,
    email: str,
    password: str | None = None,
    *,
    password_hash: str | None = None,
    disabled: bool = False,
) -> Account:
    now = app.state.clock.now()
    if password_hash is None and password is not None:
        password_hash = await app.state.hash_gate.run(hash_password, password)
    async with write_transaction(app.state.session_factory) as session:
        account = Account(
            id=str(uuid.uuid4()),
            email_normalized=normalize_email(email),
            password_hash=password_hash,
            password_set_at=format_timestamp(now) if password_hash else None,
            password_policy_version="r1-2026-10" if password_hash else None,
            is_admin=0,
            is_disabled=1 if disabled else 0,
            disabled_at=format_timestamp(now) if disabled else None,
            session_epoch=0,
            created_at=format_timestamp(now),
            updated_at=format_timestamp(now),
        )
        session.add(account)
        await session.flush()
        return account


async def _scalar(app: Any, statement: Any) -> Any:
    async with read_session(app.state.session_factory) as session:
        return await session.scalar(statement)


async def _count(app: Any, model: Any) -> int:
    return int(await _scalar(app, select(func.count()).select_from(model)) or 0)


def test_init_service_and_cli_have_no_password_argument() -> None:
    from app.auth import cli

    help_text = cli.build_parser().format_help()
    assert "--password" not in help_text
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["init-admin", "--password", "secret"])


def test_cli_rejects_non_tty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.auth import cli

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    assert cli.main(["init-admin"]) == 2


def test_password_policy_boundaries_and_unicode_spaces() -> None:
    assert len("a" * 15) == 15
    validate_password("N0t-a-weak-pass")
    validate_password("Zq7!" * 32)
    with pytest.raises(PasswordPolicyError) as short:
        validate_password("a" * 14)
    assert short.value.code == "PASSWORD_TOO_SHORT"
    with pytest.raises(PasswordPolicyError) as long:
        validate_password("a" * 129)
    assert long.value.code == "PASSWORD_TOO_LONG"

    password = "  pässwörd with spaces 密码  "
    validate_password(password)
    encoded = hash_password(password)
    params = f"m={ARGON2_MEMORY_COST_KIB},t={ARGON2_TIME_COST},p={ARGON2_PARALLELISM}"
    assert params in encoded
    assert re.fullmatch(r"\$argon2id\$v=19\$[^$]+\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+", encoded)
    assert verify_password(encoded, password) is True
    assert verify_password(encoded, password.strip()) is False
    assert hash_password(password) != encoded


def test_weak_password_denylist_is_casefold_and_fail_closed() -> None:
    with pytest.raises(PasswordPolicyError) as exc:
        validate_password("PASSWORDPASSWORD")
    assert exc.value.code == "WEAK_PASSWORD"


def test_password_needs_rehash_handles_invalid_hash() -> None:
    assert password_needs_rehash("not-an-argon2-hash") is True


@pytest.mark.asyncio
async def test_first_admin_concurrent_init_exactly_one_and_no_default_password(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)

        async def attempt(index: int):
            return await init_admin(
                app.state.session_factory,
                email=f" Admin{index}@Example.COM ",
                password=TEST_ADMIN_PASSWORD,
                clock=app.state.clock,
                hash_gate=app.state.hash_gate,
            )

        results = await asyncio.gather(*(attempt(i) for i in range(4)), return_exceptions=True)
        successes = [item for item in results if isinstance(item, Account)]
        failures = [item for item in results if isinstance(item, AlreadyInitialized)]
        assert len(successes) == 1
        assert len(failures) == 3
        assert await _count(app, Account) == 1
        assert await _count(app, InstanceBootstrap) == 1

        async with read_session(app.state.session_factory) as session:
            account = await session.scalar(select(Account))
            assert account is not None
            assert account.email_normalized.startswith("admin")
            assert account.email_normalized.endswith("@example.com")
            assert account.password_hash
            assert account.password_hash.startswith("$argon2id$")
            assert verify_password(account.password_hash, "password") is False
            assert account.password_set_at is not None

        with pytest.raises(AlreadyInitialized):
            await init_admin(
                app.state.session_factory,
                email="second@example.com",
                password=TEST_ADMIN_PASSWORD,
                clock=app.state.clock,
                hash_gate=app.state.hash_gate,
            )


@pytest.mark.asyncio
async def test_init_audit_failure_rolls_back_account(settings, registry, monkeypatch):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)

        def broken_audit(*args: Any, **kwargs: Any):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr("app.auth.service.write_audit", broken_audit)
        with pytest.raises(RuntimeError):
            await init_admin(
                app.state.session_factory,
                email="audit@example.com",
                password=TEST_ADMIN_PASSWORD,
                clock=app.state.clock,
                hash_gate=app.state.hash_gate,
            )
        assert await _count(app, Account) == 0
        assert await _count(app, InstanceBootstrap) == 0


@pytest.mark.asyncio
async def test_init_fails_closed_when_argon2_is_unavailable(settings, registry, monkeypatch):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        monkeypatch.setattr("app.auth.passwords.ARGON2_AVAILABLE", False)
        monkeypatch.setattr("app.auth.passwords._hasher", None)
        with pytest.raises(ApiError) as exc:
            await init_admin(
                app.state.session_factory,
                email="unavailable@example.com",
                password=TEST_ADMIN_PASSWORD,
                clock=app.state.clock,
                hash_gate=app.state.hash_gate,
            )
        assert exc.value.status_code == 503
        assert await _count(app, Account) == 0


@pytest.mark.asyncio
async def test_init_fails_closed_when_weak_list_is_unavailable(settings, registry, monkeypatch):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)

        def unavailable():
            raise AuthUnavailable("weak password list unavailable")

        monkeypatch.setattr("app.auth.passwords.weak_passwords", unavailable)
        with pytest.raises(ApiError) as exc:
            await init_admin(
                app.state.session_factory,
                email="unavailable-list@example.com",
                password=TEST_ADMIN_PASSWORD,
                clock=app.state.clock,
                hash_gate=app.state.hash_gate,
            )
        assert exc.value.status_code == 503
        assert exc.value.code == "AUTH_UNAVAILABLE"
        assert await _count(app, Account) == 0


def test_email_normalization_is_shared_and_rejects_invalid() -> None:
    assert normalize_email("  Admin@Example.COM ") == "admin@example.com"
    for value in ["", "  ", "a@b", "not-an-email", "@example.com"]:
        with pytest.raises(InvalidEmail):
            normalize_email(value)


@pytest.mark.asyncio
async def test_login_failures_are_generic_for_unknown_pending_disabled_and_wrong(app, monkeypatch):
    app.state.clock = FrozenClock(FROZEN)
    await _add_account(app, "pending@example.com", None)
    await _add_account(app, "disabled@example.com", TEST_ADMIN_PASSWORD, disabled=True)

    import app.auth.service as service

    dummy_calls: list[str] = []
    original_dummy = service.verify_dummy_password

    def dummy_spy(password: str) -> bool:
        dummy_calls.append(password)
        return original_dummy(password)

    monkeypatch.setattr(service, "verify_dummy_password", dummy_spy)

    async with _new_client(app) as client:
        cases = [
            ("unknown@example.com", "some wrong password 123"),
            ("pending@example.com", "some wrong password 123"),
            ("disabled@example.com", TEST_ADMIN_PASSWORD),
            (TEST_ADMIN_EMAIL, "some wrong password 123"),
        ]
        bodies = []
        for email, password in cases:
            response = await _login_attempt(client, email=email, password=password)
            assert response.status_code == 401, response.text
            bodies.append(response.json())
        assert all(body == bodies[0] for body in bodies)
        assert len(dummy_calls) == 2


@pytest.mark.asyncio
async def test_login_rate_limit_account_dimension_and_recovery(app):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as client:
        for _ in range(5):
            response = await _login_attempt(
                client, email=TEST_ADMIN_EMAIL, password="wrong password 12345"
            )
            assert response.status_code == 401
        limited = await _login_attempt(client, email=TEST_ADMIN_EMAIL)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"
        assert int(limited.headers["retry-after"]) > 0

        app.state.clock.advance(901)
        recovered = await _login_attempt(client, email=TEST_ADMIN_EMAIL)
        assert recovered.status_code == 200


@pytest.mark.asyncio
async def test_login_source_limit_is_independent(app):
    app.state.clock = FrozenClock(FROZEN)
    await _add_account(app, "source@example.com", TEST_ADMIN_PASSWORD)
    source_host = "198.51.100.20"
    key = source_ref(source_host)
    rules = [LimitRule("login_source", key, 30, 900)]
    for _ in range(30):
        await reserve_attempts(app.state.session_factory, rules, app.state.clock.now())

    with pytest.raises(ApiError) as exc:
        await login(
            app.state.session_factory,
            email="source@example.com",
            password=TEST_ADMIN_PASSWORD,
            clock=app.state.clock,
            hash_gate=app.state.hash_gate,
            client_host=source_host,
            user_agent="test",
        )
    assert exc.value.status_code == 429
    assert exc.value.code == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_rate_limit_window_is_half_open(app):
    app.state.clock = FrozenClock(FROZEN)
    rules = [LimitRule("login_account", source_ref("window@example.com"), 2, 60)]
    await reserve_attempts(app.state.session_factory, rules, app.state.clock.now())
    await reserve_attempts(app.state.session_factory, rules, app.state.clock.now())
    app.state.clock.advance(59)
    with pytest.raises(RateLimited):
        await reserve_attempts(app.state.session_factory, rules, app.state.clock.now())
    app.state.clock.advance(1)
    ids = await reserve_attempts(app.state.session_factory, rules, app.state.clock.now())
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_login_success_rotates_session_cookie_and_audit_has_no_secret(app):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as first:
        first_response = await _login(first)
        first_token = first_response.json()["csrf_token"]
        old_cookie = first.cookies.get(SESSION_COOKIE)
        assert old_cookie
        async with _new_client(app) as second:
            await _login(second)
            new_cookie = second.cookies.get(SESSION_COOKIE)
            assert new_cookie and new_cookie != old_cookie
            assert first.cookies.get(SESSION_COOKIE) != new_cookie

            # The first session remains independently usable until logout.
            assert (await first.get("/api/auth/session")).status_code == 200

    events = []
    async with read_session(app.state.session_factory) as session:
        events = list(await session.scalars(select(AuditEvent).order_by(AuditEvent.occurred_at)))
    assert any(event.event_type == "login_success" for event in events)
    for event in events:
        payload = " ".join(
            value
            for value in (event.detail_json, event.before_json, event.after_json)
            if value
        )
        assert TEST_ADMIN_PASSWORD not in payload
        assert first_token not in payload


@pytest.mark.asyncio
async def test_session_cookie_flags_and_server_side_digest(app):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as client:
        response = await _login(client)
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(value for value in cookies if value.startswith(SESSION_COOKIE + "="))
        lowered = session_cookie.lower()
        assert "secure" in lowered
        assert "httponly" in lowered
        assert "samesite=strict" in lowered
        assert "path=/" in lowered
        assert "domain=" not in lowered
        csrf_cookie = next(
            value for value in cookies if value.startswith(PREAUTH_CSRF_COOKIE + "=")
        )
        assert "httponly" not in csrf_cookie.lower()
        async with read_session(app.state.session_factory) as db:
            record = await db.scalar(select(AuthSession))
            assert record is not None
            raw = client.cookies.get(SESSION_COOKIE)
            assert raw and raw not in record.token_digest
            assert len(record.token_digest) == 64


@pytest.mark.asyncio
async def test_forged_session_cookie_is_rejected(app, anonymous_client):
    forged = await anonymous_client.get(
        "/api/projects", cookies={SESSION_COOKIE: "forged-session-token"}
    )
    assert forged.status_code == 401
    assert forged.json()["error"]["code"] == "AUTH_REQUIRED"

    session = await anonymous_client.get(
        "/api/auth/session", cookies={SESSION_COOKIE: "forged-session-token"}
    )
    assert session.status_code == 401
    assert session.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_anonymous_unsafe_business_request_gets_401_before_origin(app):
    app.state.clock = FrozenClock(FROZEN)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN) as raw:
        response = await raw.post("/api/projects", json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_get_csrf_does_not_clobber_session_csrf_and_session_still_works(app):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as client:
        response = await _login(client)
        token = response.json()["csrf_token"]
        async with read_session(app.state.session_factory) as db:
            before = await db.scalar(select(AuthSession))
            assert before is not None
            before_digest = before.csrf_digest
        fetched = await client.get("/api/auth/csrf")
        assert fetched.status_code == 200
        assert fetched.json()["csrf_token"] == token
        async with read_session(app.state.session_factory) as db:
            after = await db.scalar(select(AuthSession))
            assert after is not None
            assert after.csrf_digest == before_digest
        assert (await client.get("/api/auth/session")).status_code == 200


@pytest.mark.asyncio
async def test_csrf_origin_host_and_no_store(app, anonymous_client):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as client:
        await _login(client)
        assert (await client.post("/api/projects", json={})).status_code == 201

        client.csrf_token = None
        missing = await client.post("/api/projects", json={})
        assert missing.status_code == 403
        assert missing.json()["error"]["code"] == "CSRF_INVALID"

        client.csrf_token = "wrong-token"
        wrong = await client.post("/api/projects", json={})
        assert wrong.status_code == 403
        assert wrong.json()["error"]["code"] == "CSRF_INVALID"

        client.csrf_token = None
        client.origin = "https://evil.example"
        bad_origin = await client.post("/api/projects", json={})
        client.origin = TEST_ORIGIN
        assert bad_origin.status_code == 403
        assert bad_origin.json()["error"]["code"] == "ORIGIN_INVALID"

        bad_host = await anonymous_client.get("/api/health", headers={"Host": "evil.example"})
        assert bad_host.status_code == 403
        assert bad_host.json()["error"]["code"] == "HOST_INVALID"

        ok = await client.get("/api/projects")
        assert ok.status_code == 200
        assert ok.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_authenticated_csrf_fetch_then_mutation_uses_same_token(app):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as client:
        response = await _login(client)
        token = response.json()["csrf_token"]
        assert (await client.get("/api/auth/csrf")).json()["csrf_token"] == token
        created = await client.post("/api/projects", json={})
        assert created.status_code == 201


@pytest.mark.asyncio
async def test_session_idle_absolute_and_polling_does_not_renew(app):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as client:
        await _login(client)
        app.state.clock.advance(29 * 60)
        assert (await client.get("/api/auth/session")).status_code == 200
        app.state.clock.advance(61)
        assert (await client.get("/api/auth/session")).status_code == 401

    app.state.clock = FrozenClock(FROZEN + timedelta(hours=1))
    async with _new_client(app) as client:
        await _login(client)
        async with write_transaction(app.state.session_factory) as db:
            record = await db.scalar(select(AuthSession).order_by(AuthSession.created_at.desc()))
            assert record is not None
            record.idle_expires_at = record.absolute_expires_at
        app.state.clock.advance(12 * 60 * 60 + 1)
        assert (await client.get("/api/auth/session")).status_code == 401

    app.state.clock = FrozenClock(FROZEN + timedelta(hours=2))
    async with _new_client(app) as client:
        await _login(client)
        app.state.clock.advance(20 * 60)
        polling = await client.get(
            "/api/projects",
            headers={"X-Polling": "1", "X-Auth-Activity": "user"},
        )
        assert polling.status_code == 200
        app.state.clock.advance(11 * 60)
        assert (await client.get("/api/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_active_mutation_renews_idle_but_plain_get_does_not(app):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as client:
        await _login(client)
        app.state.clock.advance(20 * 60)
        created = await client.post("/api/projects", json={})
        assert created.status_code == 201
        app.state.clock.advance(20 * 60)
        assert (await client.get("/api/auth/session")).status_code == 200

    app.state.clock = FrozenClock(FROZEN + timedelta(hours=1))
    async with _new_client(app) as client:
        await _login(client)
        app.state.clock.advance(20 * 60)
        assert (await client.get("/api/projects")).status_code == 200
        app.state.clock.advance(11 * 60)
        assert (await client.get("/api/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_only_current_session_and_revoke_all_helper(app):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as first, _new_client(app) as second:
        await _login(first)
        await _login(second)
        response = await first.post("/api/auth/logout")
        assert response.status_code == 204
        assert (await first.get("/api/auth/session")).status_code == 401
        assert (await second.get("/api/auth/session")).status_code == 200

        async with read_session(app.state.session_factory) as db:
            account = await db.scalar(select(Account))
            assert account is not None
        await revoke_all_for_account(
            app.state.session_factory, account.id, app.state.clock.now(), "password_change"
        )
        assert (await second.get("/api/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_session_endpoint_returns_503_on_db_failure(app, monkeypatch):
    app.state.clock = FrozenClock(FROZEN)
    async with _new_client(app) as client:
        await _login(client)

        async def broken_load(*args: Any, **kwargs: Any):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr("app.auth.middleware.load_session", broken_load)
        assert (await client.get("/api/auth/session")).status_code == 503
        assert (await client.get("/api/health")).status_code == 200


@pytest.mark.asyncio
async def test_auth_json_body_limit(anonymous_client):
    payload = b'{"email":"a@example.com","password":"' + b"x" * 20_000 + b'"}'
    response = await anonymous_client.post(
        "/api/auth/login",
        content=payload,
        headers={"Content-Type": "application/json", "Origin": TEST_ORIGIN},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "AUTH_BODY_TOO_LARGE"


@pytest.mark.asyncio
async def test_default_deny_matrix_and_public_allowlist(app):
    import re

    from fastapi.routing import APIRoute
    from starlette.routing import Route

    public = {
        "/api/health",
        "/api/config/status",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/session",
        "/api/auth/csrf",
    }
    specs: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, (APIRoute, Route)):
            continue
        path = route.path
        methods = getattr(route, "methods", None) or set()
        if not path.startswith("/api/") or path in public or path.startswith("/api/auth/"):
            continue
        for method in sorted(methods & {"GET", "POST", "PATCH", "PUT", "DELETE"}):
            concrete = re.sub(r"\{[^}]+\}", "00000000-0000-4000-8000-000000000000", path)
            specs.append((method, concrete))
    assert specs

    from tests.support.auth import AnonymousClient

    app.state.clock = FrozenClock(FROZEN)
    async with AnonymousClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN) as anon:
        for method, path in specs:
            response = await anon.request(method, path)
            assert response.status_code == 401, (method, path, response.text)
            assert response.json()["error"]["code"] == "AUTH_REQUIRED"

    async with _new_client(app) as client:
        await _login(client)
        for method, path in specs:
            response = await client.request(method, path)
            assert response.status_code not in (401, 403), (method, path, response.text)
            assert response.status_code < 500, (method, path, response.text)

    async with AnonymousClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN) as anon:
        assert (await anon.get("/api/health")).status_code == 200
        assert (await anon.get("/api/config/status")).status_code == 200
        assert (await anon.get("/api/auth/csrf")).status_code == 200
        assert (await anon.get("/api/auth/session")).status_code == 401
        assert (await anon.get("/api/unknown")).status_code == 401
        assert (await anon.post("/api/auth/init", json={})).status_code == 401


@pytest.mark.asyncio
async def test_hash_gate_two_running_eight_queued_and_ninth_waiting_rejected():
    gate = HashGate(running=2, queued=8)
    release = asyncio.Event()
    entered = 0

    async def worker() -> None:
        nonlocal entered
        async with gate.slot():
            entered += 1
            await release.wait()

    tasks = [asyncio.create_task(worker()) for _ in range(10)]
    for _ in range(50):
        await asyncio.sleep(0.002)
        if gate._running == 2 and gate._waiting == 8:
            break
    assert gate._running == 2
    assert gate._waiting == 8
    with pytest.raises(ApiError) as exc:
        async with gate.slot():
            pass
    assert exc.value.code == "AUTH_BUSY"
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_auth_busy_from_hash_gate_releases_rate_limit_facts(app):
    app.state.clock = FrozenClock(FROZEN)
    saturated = HashGate(running=1, queued=0)
    async with saturated.slot():
        with pytest.raises(ApiError) as exc:
            await login(
                app.state.session_factory,
                email=TEST_ADMIN_EMAIL,
                password="wrong password 12345",
                clock=app.state.clock,
                hash_gate=saturated,
                client_host="198.51.100.30",
                user_agent="test",
            )
        assert exc.value.code == "AUTH_BUSY"
    async with read_session(app.state.session_factory) as db:
        facts = await db.scalar(select(func.count()).select_from(RateLimitFact))
        assert int(facts or 0) == 0


@pytest.mark.asyncio
async def test_password_needs_rehash_on_login_upgrades_hash(app):
    app.state.clock = FrozenClock(FROZEN)
    old_hasher = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    old_hash = old_hasher.hash(TEST_ADMIN_PASSWORD)
    account = await _add_account(
        app, "rehash@example.com", password_hash=old_hash
    )
    async with _new_client(app) as client:
        response = await _login(client, email="rehash@example.com")
        assert response.status_code == 200
    async with read_session(app.state.session_factory) as db:
        current = await db.get(Account, account.id)
        assert current is not None
        assert current.password_hash != old_hash
        assert current.password_hash is not None
        assert current.password_hash.startswith("$argon2id$")


@pytest.mark.asyncio
async def test_audit_json_truncation_stays_valid_json(settings, registry):
    async with _fresh_app(settings, registry) as app:
        app.state.clock = FrozenClock(FROZEN)
        async with write_transaction(app.state.session_factory) as db:
            write_audit(
                db,
                event_type="test",
                result="success",
                now=app.state.clock.now(),
                detail={"blob": "x" * 5000},
            )
        async with read_session(app.state.session_factory) as db:
            event = await db.scalar(select(AuditEvent))
            assert event is not None
            parsed = json.loads(event.detail_json or "{}")
            assert parsed["truncated"] is True
            assert parsed["original_length"] > 4096

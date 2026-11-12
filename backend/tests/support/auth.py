from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager, suppress
from typing import Any

from httpx import ASGITransport, AsyncClient, Headers
from sqlalchemy import select

from app.auth.models import Account
from app.auth.service import AlreadyInitialized, init_admin

TEST_ADMIN_EMAIL = "admin@example.com"
TEST_ADMIN_PASSWORD = "correct horse battery staple 42"
TEST_ORIGIN = "https://testserver"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class AuthClient(AsyncClient):
    """HTTP client that behaves like the browser: origin + session CSRF headers."""

    def __init__(
        self,
        *args: Any,
        origin: str = TEST_ORIGIN,
        csrf_token: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.origin = origin
        self.csrf_token = csrf_token

    async def request(self, method: str, url: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        method = method.upper()
        headers = Headers(kwargs.get("headers"))
        if method not in SAFE_METHODS:
            headers["Origin"] = self.origin
            if self.csrf_token:
                headers["X-CSRF-Token"] = self.csrf_token
        kwargs["headers"] = headers
        return await super().request(method, url, **kwargs)


class AnonymousClient(AuthClient):
    """Anonymous browser-like client: origin headers but no session or CSRF token."""


async def ensure_test_admin(app: Any) -> None:
    try:
        await init_admin(
            app.state.session_factory,
            email=TEST_ADMIN_EMAIL,
            password=TEST_ADMIN_PASSWORD,
            clock=app.state.clock,
            hash_gate=app.state.hash_gate,
        )
    except AlreadyInitialized:
        return


async def admin_account_id_for_seed(session: Any) -> str:
    """Return the account id that owns fixtures seeded before an HTTP request."""
    account = await session.scalar(
        select(Account).where(Account.email_normalized == TEST_ADMIN_EMAIL)
    )
    if account is None:  # pragma: no cover - fixture ordering bug
        raise RuntimeError("the test admin must exist before seeding owned projects")
    return account.id


async def login(
    client: AuthClient,
    email: str = TEST_ADMIN_EMAIL,
    password: str = TEST_ADMIN_PASSWORD,
    origin: str = TEST_ORIGIN,
) -> dict[str, Any]:
    client.origin = origin
    csrf_response = await client.get("/api/auth/csrf")
    assert csrf_response.status_code == 200, csrf_response.text
    preauth = csrf_response.json()["csrf_token"]
    response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": origin, "X-CSRF-Token": preauth},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    client.csrf_token = body["csrf_token"]
    return body


async def open_authenticated_client(app: Any, base_url: str = "https://testserver") -> AuthClient:
    transport = ASGITransport(app=app)
    client = AuthClient(transport=transport, base_url=base_url)
    await ensure_test_admin(app)
    await login(client)
    return client


def assign_projects_to_test_admin_sync(settings: Any, project_ids: list[str]) -> None:
    """Bind legacy raw-SQL fixtures to the seeded test admin after migrations."""

    unique_ids = list(dict.fromkeys(project_ids))
    if not unique_ids:
        return
    connection = sqlite3.connect(settings.database_path)
    try:
        row = connection.execute(
            "SELECT id FROM accounts WHERE email_normalized = ?", (TEST_ADMIN_EMAIL,)
        ).fetchone()
        if row is None:  # pragma: no cover - fixture ordering bug
            raise RuntimeError("the test admin must exist before binding project owners")
        connection.executemany(
            "UPDATE projects SET owner_account_id = ? WHERE id = ?",
            [(row[0], project_id) for project_id in unique_ids],
        )
        connection.commit()
    finally:
        connection.close()


async def bootstrap_admin(settings: Any) -> None:
    """Create the test admin through the real service without nesting event loops."""

    from app.auth.clock import Clock
    from app.auth.passwords import HashGate
    from app.db import build_session_factory, create_engine
    from app.startup import prepare_database

    engine = create_engine(settings)
    try:
        await prepare_database(engine)
        factory = build_session_factory(engine)
        with suppress(AlreadyInitialized):
            await init_admin(
                factory,
                email=TEST_ADMIN_EMAIL,
                password=TEST_ADMIN_PASSWORD,
                clock=Clock(),
                hash_gate=HashGate(),
            )
    finally:
        await engine.dispose()


async def bind_projects_to_test_admin(settings: Any, project_ids: list[str]) -> None:
    await bootstrap_admin(settings)
    assign_projects_to_test_admin_sync(settings, project_ids)


def bootstrap_admin_sync(settings: Any) -> None:
    """Used by real-process tests before starting uvicorn."""

    from app.auth.clock import Clock
    from app.auth.passwords import HashGate
    from app.db import build_session_factory, create_engine
    from app.startup import prepare_database

    async def _run() -> None:
        engine = create_engine(settings)
        try:
            await prepare_database(engine)
            factory = build_session_factory(engine)
            with suppress(AlreadyInitialized):
                await init_admin(
                    factory,
                    email=TEST_ADMIN_EMAIL,
                    password=TEST_ADMIN_PASSWORD,
                    clock=Clock(),
                    hash_gate=HashGate(),
                )
        finally:
            await engine.dispose()

    asyncio.run(_run())


@asynccontextmanager
async def authenticated_client(app: Any):
    await ensure_test_admin(app)
    transport = ASGITransport(app=app)
    async with AuthClient(transport=transport, base_url="https://testserver") as client:
        await login(client)
        yield client

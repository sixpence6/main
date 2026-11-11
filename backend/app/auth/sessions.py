from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import Account, AuthSession
from app.auth.tokens import new_token, token_digest
from app.db import read_session, write_transaction
from app.timeutil import format_timestamp

SESSION_COOKIE = "__Host-session"
PREAUTH_CSRF_COOKIE = "__Host-csrf"
# Browsers drop any cookie whose name starts with "__Host-" unless it carries the
# Secure attribute on a secure connection, so a loopback HTTP deployment cannot
# use the prefixed names. These fall back to explicitly project-scoped names
# (ports share one cookie jar on 127.0.0.1, so the prefix must not be generic).
INSECURE_SESSION_COOKIE = "ecom-session"
INSECURE_PREAUTH_CSRF_COOKIE = "ecom-csrf"


def cookie_names(secure: bool) -> tuple[str, str]:
    """Return (session_cookie, csrf_cookie) for the active cookie security mode."""
    if secure:
        return SESSION_COOKIE, PREAUTH_CSRF_COOKIE
    return INSECURE_SESSION_COOKIE, INSECURE_PREAUTH_CSRF_COOKIE
SESSION_IDLE_SECONDS = 30 * 60
SESSION_ABSOLUTE_SECONDS = 12 * 60 * 60
SESSION_IDLE = timedelta(seconds=SESSION_IDLE_SECONDS)
SESSION_ABSOLUTE = timedelta(seconds=SESSION_ABSOLUTE_SECONDS)


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class SessionContext:
    session: AuthSession
    account: Account


def add_session(
    session: AsyncSession,
    account: Account,
    now: datetime,
    source_ref: str | None,
    client_info: str | None,
) -> tuple[AuthSession, str, str]:
    raw_token = new_token()
    csrf_token = new_token()
    record = AuthSession(
        id=str(uuid.uuid4()),
        account_id=account.id,
        token_digest=token_digest(raw_token),
        csrf_digest=token_digest(csrf_token),
        account_epoch=account.session_epoch,
        created_at=format_timestamp(now),
        last_activity_at=format_timestamp(now),
        idle_expires_at=format_timestamp(now + SESSION_IDLE),
        absolute_expires_at=format_timestamp(now + SESSION_ABSOLUTE),
        source_ref=source_ref,
        client_info=(client_info or "")[:200] or None,
    )
    session.add(record)
    return record, raw_token, csrf_token


async def load_session(
    factory: async_sessionmaker[AsyncSession], raw_token: str, now: datetime
) -> SessionContext | None:
    digest = token_digest(raw_token)
    async with read_session(factory) as session:
        record = await session.scalar(select(AuthSession).where(AuthSession.token_digest == digest))
        if record is None or record.revoked_at is not None:
            return None
        if parse_timestamp(record.absolute_expires_at) <= now:
            return None
        if parse_timestamp(record.idle_expires_at) <= now:
            return None
        account = await session.get(Account, record.account_id)
        if account is None or not account.can_login:
            return None
        if record.account_epoch != account.session_epoch:
            return None
        return SessionContext(session=record, account=account)


async def touch_session(
    factory: async_sessionmaker[AsyncSession], session_id: str, now: datetime
) -> None:
    async with write_transaction(factory) as session:
        record = await session.get(AuthSession, session_id)
        if record is None or record.revoked_at is not None:
            return
        absolute = parse_timestamp(record.absolute_expires_at)
        idle = min(now + SESSION_IDLE, absolute)
        record.last_activity_at = format_timestamp(now)
        record.idle_expires_at = format_timestamp(idle)


async def rotate_csrf(
    factory: async_sessionmaker[AsyncSession], session_id: str, now: datetime
) -> str:
    csrf_token = new_token()
    async with write_transaction(factory) as session:
        record = await session.get(AuthSession, session_id)
        if record is None or record.revoked_at is not None:
            from app.errors import ApiError

            raise ApiError(401, "AUTH_REQUIRED", "请先登录")
        record.csrf_digest = token_digest(csrf_token)
        record.last_activity_at = format_timestamp(now)
        absolute = parse_timestamp(record.absolute_expires_at)
        record.idle_expires_at = format_timestamp(min(now + SESSION_IDLE, absolute))
    return csrf_token


async def revoke_current(
    factory: async_sessionmaker[AsyncSession],
    session_id: str,
    now: datetime,
    reason: str = "logout",
) -> None:
    async with write_transaction(factory) as session:
        record = await session.get(AuthSession, session_id)
        if record is None or record.revoked_at is not None:
            return
        record.revoked_at = format_timestamp(now)
        record.revoked_reason = reason


async def revoke_all_for_account(
    factory: async_sessionmaker[AsyncSession],
    account_id: str,
    now: datetime,
    reason: str,
) -> None:
    async with write_transaction(factory) as session:
        account = await session.get(Account, account_id)
        if account is None:
            return
        account.session_epoch += 1
        account.updated_at = format_timestamp(now)
        await session.execute(
            update(AuthSession)
            .where(AuthSession.account_id == account_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=format_timestamp(now), revoked_reason=reason)
        )


async def list_sessions(
    factory: async_sessionmaker[AsyncSession], account_id: str
) -> list[AuthSession]:
    async with read_session(factory) as session:
        result = await session.scalars(
            select(AuthSession)
            .where(AuthSession.account_id == account_id)
            .order_by(AuthSession.created_at.desc())
        )
        return list(result)

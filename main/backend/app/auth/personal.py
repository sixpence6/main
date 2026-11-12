from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.audit import audit_subject, write_audit
from app.auth.clock import Clock
from app.auth.models import Account, AccountLink, AuthSession
from app.auth.passwords import (
    WEAK_PASSWORD_LIST_VERSION,
    AuthUnavailable,
    HashGate,
    PasswordPolicyError,
    hash_password,
    validate_password,
    verify_password,
)
from app.auth.sessions import parse_timestamp
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.timeutil import format_timestamp


class RecoveryNotFound(Exception):
    """Raised when the local recovery command cannot identify the sole administrator."""


def _dependency_error(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        if exc.code == "AUTH_BUSY":
            return ApiError(503, "AUTH_BUSY", "认证服务繁忙，请稍后重试")
        raise exc
    return ApiError(503, "AUTH_UNAVAILABLE", "认证服务当前不可用")


def _policy_error(exc: PasswordPolicyError) -> ApiError:
    return ApiError(422, exc.code, exc.message)


def _current_password_error() -> ApiError:
    return ApiError(400, "CURRENT_PASSWORD_INVALID", "当前密码不正确")


def _active_session(record: AuthSession | None, account: Account, now: datetime) -> bool:
    if record is None or record.revoked_at is not None:
        return False
    if record.account_epoch != account.session_epoch:
        return False
    if parse_timestamp(record.absolute_expires_at) <= now:
        return False
    return parse_timestamp(record.idle_expires_at) > now


async def change_password(
    factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    account_id: str,
    current_password: str,
    new_password: str,
    clock: Clock,
    hash_gate: HashGate,
    source_ref_value: str | None = None,
) -> None:
    """Verify the current secret, then atomically replace it and revoke every session."""
    now = clock.now()
    try:
        validate_password(new_password)
    except PasswordPolicyError as exc:
        raise _policy_error(exc) from exc
    except AuthUnavailable as exc:
        raise _dependency_error(exc) from exc

    async with read_session(factory) as session:
        account = await session.get(Account, account_id)
        current_session = await session.get(AuthSession, session_id)
    if (
        account is None
        or not account.can_login
        or not _active_session(current_session, account, now)
    ):
        raise ApiError(401, "AUTH_REQUIRED", "请先登录")
    if not account.password_hash:
        raise _current_password_error()
    previous_hash = account.password_hash

    try:
        matches = await hash_gate.run(verify_password, previous_hash, current_password)
    except (AuthUnavailable, ApiError) as exc:
        raise _dependency_error(exc) from exc
    if not matches:
        raise _current_password_error()

    try:
        new_hash = await hash_gate.run(hash_password, new_password)
    except (AuthUnavailable, ApiError) as exc:
        raise _dependency_error(exc) from exc

    async with write_transaction(factory) as session:
        current = await session.get(Account, account_id)
        current_session = await session.get(AuthSession, session_id)
        if (
            current is None
            or not current.can_login
            or not _active_session(current_session, current, now)
        ):
            raise ApiError(401, "AUTH_REQUIRED", "请先登录")
        if current.password_hash != previous_hash:
            # Another concurrent change already won; never overwrite it with stale proof.
            raise ApiError(409, "PASSWORD_CHANGED", "密码已更新，请重新登录")
        current.password_hash = new_hash
        current.password_set_at = format_timestamp(now)
        current.password_policy_version = WEAK_PASSWORD_LIST_VERSION
        current.session_epoch += 1
        current.updated_at = format_timestamp(now)
        await session.execute(
            update(AuthSession)
            .where(AuthSession.account_id == account_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=format_timestamp(now), revoked_reason="password_change")
        )
        await session.execute(
            update(AccountLink)
            .where(
                AccountLink.account_id == account_id,
                AccountLink.used_at.is_(None),
                AccountLink.revoked_at.is_(None),
            )
            .values(revoked_at=format_timestamp(now), revoked_reason="password_change")
        )
        write_audit(
            session,
            event_type="password_changed",
            result="success",
            now=now,
            actor_kind="account",
            actor_account_id=account_id,
            target_account_id=account_id,
            target_kind="account",
            target_id=account_id,
            subject_ref=audit_subject(current.email_normalized),
            source_ref=source_ref_value,
            detail={"sessions_revoked": "all"},
        )


async def list_active_sessions(
    factory: async_sessionmaker[AsyncSession], *, account_id: str, now: datetime
) -> list[AuthSession]:
    async with read_session(factory) as session:
        account = await session.get(Account, account_id)
        if account is None:
            return []
        result = await session.scalars(
            select(AuthSession)
            .where(AuthSession.account_id == account_id)
            .order_by(AuthSession.created_at.desc(), AuthSession.id)
        )
        return [record for record in result if _active_session(record, account, now)]


async def revoke_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    actor_account_id: str,
    actor_session_id: str,
    target_session_id: str,
    now: datetime,
    source_ref_value: str | None = None,
) -> bool:
    """Revoke one of the caller's active sessions; never reveal other accounts' rows."""
    async with write_transaction(factory) as session:
        account = await session.get(Account, actor_account_id)
        target = await session.get(AuthSession, target_session_id)
        if account is None or target is None or target.account_id != actor_account_id:
            raise ApiError(404, "SESSION_NOT_FOUND", "会话不存在或已失效")
        if not _active_session(target, account, now):
            raise ApiError(404, "SESSION_NOT_FOUND", "会话不存在或已失效")
        target.revoked_at = format_timestamp(now)
        target.revoked_reason = "user_revoked"
        is_current = target.id == actor_session_id
        write_audit(
            session,
            event_type="session_revoked",
            result="success",
            now=now,
            actor_kind="account",
            actor_account_id=actor_account_id,
            target_account_id=actor_account_id,
            target_kind="session",
            target_id=target.id,
            subject_ref=audit_subject(account.email_normalized),
            source_ref=source_ref_value,
            detail={"current_session": is_current},
        )
        return is_current


async def revoke_other_sessions(
    factory: async_sessionmaker[AsyncSession],
    *,
    account_id: str,
    current_session_id: str,
    now: datetime,
    source_ref_value: str | None = None,
) -> int:
    """Revoke every active session except the caller's current one."""
    async with write_transaction(factory) as session:
        account = await session.get(Account, account_id)
        if account is None:
            raise ApiError(401, "AUTH_REQUIRED", "请先登录")
        result = await session.scalars(
            select(AuthSession).where(
                AuthSession.account_id == account_id, AuthSession.revoked_at.is_(None)
            )
        )
        revoked = 0
        for record in result:
            if record.id == current_session_id or not _active_session(record, account, now):
                continue
            record.revoked_at = format_timestamp(now)
            record.revoked_reason = "user_revoked_others"
            revoked += 1
        write_audit(
            session,
            event_type="sessions_revoked",
            result="success",
            now=now,
            actor_kind="account",
            actor_account_id=account_id,
            target_account_id=account_id,
            target_kind="account",
            target_id=account_id,
            subject_ref=audit_subject(account.email_normalized),
            source_ref=source_ref_value,
            detail={"revoked_count": revoked, "scope": "others"},
        )
        return revoked


async def recover_admin(
    factory: async_sessionmaker[AsyncSession],
    *,
    email: str | None,
    password: str,
    clock: Clock,
    hash_gate: HashGate,
    method: str = "local_cli",
) -> Account:
    """Reset the sole administrator password from a local maintenance context only."""
    from app.auth.service import InvalidEmail, normalize_email

    now = clock.now()
    normalized: str | None = None
    if email is not None:
        try:
            normalized = normalize_email(email)
        except InvalidEmail as exc:
            raise RecoveryNotFound("administrator not found") from exc
    try:
        validate_password(password)
    except PasswordPolicyError as exc:
        raise _policy_error(exc) from exc
    except AuthUnavailable as exc:
        raise _dependency_error(exc) from exc

    async with read_session(factory) as session:
        if normalized is None:
            admins = list(
                await session.scalars(select(Account).where(Account.is_admin == 1))
            )
            if len(admins) != 1:
                raise RecoveryNotFound("administrator not found")
            admin = admins[0]
        else:
            admin = await session.scalar(
                select(Account).where(
                    Account.is_admin == 1, Account.email_normalized == normalized
                )
            )
            if admin is None:
                raise RecoveryNotFound("administrator not found")
        admin_id = admin.id
        admin_email = admin.email_normalized

    try:
        new_hash = await hash_gate.run(hash_password, password)
    except (AuthUnavailable, ApiError) as exc:
        raise _dependency_error(exc) from exc

    async with write_transaction(factory) as session:
        current = await session.get(Account, admin_id)
        if current is None or not current.is_admin:
            raise RecoveryNotFound("administrator not found")
        current.password_hash = new_hash
        current.password_set_at = format_timestamp(now)
        current.password_policy_version = WEAK_PASSWORD_LIST_VERSION
        current.is_disabled = 0
        current.disabled_at = None
        current.disabled_by = None
        current.session_epoch += 1
        current.updated_at = format_timestamp(now)
        await session.execute(
            update(AuthSession)
            .where(AuthSession.account_id == admin_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=format_timestamp(now), revoked_reason="admin_recovery")
        )
        await session.execute(
            update(AccountLink)
            .where(
                AccountLink.account_id == admin_id,
                AccountLink.used_at.is_(None),
                AccountLink.revoked_at.is_(None),
            )
            .values(revoked_at=format_timestamp(now), revoked_reason="admin_recovery")
        )
        write_audit(
            session,
            event_type="admin_recovered",
            result="success",
            now=now,
            actor_kind="local_maintainer",
            target_account_id=admin_id,
            target_kind="account",
            target_id=admin_id,
            subject_ref=audit_subject(admin_email),
            detail={"method": method, "sessions_revoked": "all", "links_revoked": "all"},
        )
        return current


from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.audit import audit_subject, write_audit
from app.auth.clock import Clock
from app.auth.models import Account, AuthSession, InstanceBootstrap
from app.auth.passwords import (
    WEAK_PASSWORD_LIST_VERSION,
    AuthUnavailable,
    HashGate,
    hash_password,
    password_needs_rehash,
    validate_password,
    verify_dummy_password,
    verify_password,
)
from app.auth.rate_limit import (
    LimitRule,
    RateLimited,
    reserve_attempts,
    settle_attempts_in_session,
)
from app.auth.sessions import add_session, rotate_csrf, touch_session
from app.auth.tokens import (
    account_rate_key,
    constant_time_equals,
    source_ref,
    token_digest,
)
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.timeutil import format_timestamp

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_SOURCE_LIMIT = 30
LOGIN_SOURCE_WINDOW_SECONDS = 15 * 60


class AlreadyInitialized(Exception):
    pass


class InvalidEmail(ValueError):
    pass


@dataclass(frozen=True)
class LoginResult:
    account: Account
    session_id: str
    csrf_token: str
    expires_at: str
    raw_token: str


def normalize_email(raw: str) -> str:
    if not isinstance(raw, str):
        raise InvalidEmail("email must be a string")
    normalized = raw.strip().casefold()
    if not normalized or len(normalized) > 254 or len(normalized) < 3:
        raise InvalidEmail("invalid email")
    if not _EMAIL_RE.fullmatch(normalized):
        raise InvalidEmail("invalid email")
    return normalized


def _attempt_key(raw_email: str) -> str:
    try:
        normalized = normalize_email(raw_email)
    except InvalidEmail:
        normalized = str(raw_email).strip().casefold()[:254] or "invalid"
    return account_rate_key(normalized)


def _client_info(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    return user_agent[:200]


def _generic_failure() -> ApiError:
    return ApiError(401, "INVALID_CREDENTIALS", "邮箱或密码不正确")


async def _best_effort_auth_unavailable(
    factory: async_sessionmaker[AsyncSession],
    *,
    fact_ids: list[int],
    now: datetime,
    subject: str,
    source_key: str,
    reason_code: str = "auth_unavailable",
) -> None:
    """Release reserved attempts and audit an outage without masking the 503."""
    try:
        async with write_transaction(factory) as session:
            # Dependency outages are not credential failures: release, do not count.
            await settle_attempts_in_session(session, fact_ids, success=True)
            write_audit(
                session,
                event_type="login_failure",
                result="failure",
                now=now,
                actor_kind="anonymous",
                target_kind="account",
                subject_ref=audit_subject(subject),
                reason_code=reason_code,
                source_ref=source_key,
            )
    except Exception:
        # Best-effort only; never convert a fail-closed 503 into a success.
        pass


def _dependency_failure(exc: Exception) -> tuple[str, ApiError]:
    """Map hash dependency failures to a fail-closed, non-credential error."""
    if isinstance(exc, ApiError):
        if exc.code != "AUTH_BUSY":
            raise exc
        return "auth_busy", ApiError(503, "AUTH_BUSY", "认证服务繁忙，请稍后重试")
    return "auth_unavailable", ApiError(503, "AUTH_UNAVAILABLE", "认证服务当前不可用")


def _safe_reason_for_account(account: Account | None, email_valid: bool) -> str:
    if not email_valid:
        return "invalid_email"
    if account is None:
        return "unknown_account"
    if account.is_disabled:
        return "account_disabled"
    if not account.password_hash:
        return "password_not_set"
    return "bad_password"


async def init_admin(
    factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    password: str,
    clock: Clock,
    hash_gate: HashGate,
    method: str = "local_cli",
) -> Account:
    normalized = normalize_email(email)
    try:
        validate_password(password)
    except AuthUnavailable as exc:
        _, error = _dependency_failure(exc)
        raise error from exc
    now = clock.now()
    async with read_session(factory) as session:
        existing = await session.scalar(
            select(func.count()).select_from(Account).where(Account.is_admin == 1)
        )
        if existing:
            raise AlreadyInitialized("administrator already exists")
    try:
        password_hash = await hash_gate.run(hash_password, password)
    except (AuthUnavailable, ApiError) as exc:
        _, error = _dependency_failure(exc)
        raise error from exc
    try:
        async with write_transaction(factory) as session:
            existing = await session.scalar(
                select(func.count()).select_from(Account).where(Account.is_admin == 1)
            )
            bootstrap = await session.get(InstanceBootstrap, 1)
            if existing or bootstrap is not None:
                raise AlreadyInitialized("administrator already exists")
            account = Account(
                id=str(uuid.uuid4()),
                email_normalized=normalized,
                password_hash=password_hash,
                password_set_at=format_timestamp(now),
                password_policy_version=WEAK_PASSWORD_LIST_VERSION,
                is_admin=1,
                is_disabled=0,
                session_epoch=0,
                created_at=format_timestamp(now),
                updated_at=format_timestamp(now),
            )
            session.add(account)
            await session.flush()
            session.add(
                InstanceBootstrap(
                    id=1,
                    admin_account_id=account.id,
                    initialized_at=format_timestamp(now),
                    method=method,
                )
            )
            write_audit(
                session,
                event_type="bootstrap_admin_created",
                result="success",
                now=now,
                actor_kind="local_maintainer",
                target_account_id=account.id,
                target_kind="account",
                target_id=account.id,
                subject_ref=audit_subject(normalized),
                after={"is_admin": True, "status": "active"},
            )
            return account
    except IntegrityError as exc:
        raise AlreadyInitialized("administrator already exists") from exc


async def login(
    factory: async_sessionmaker[AsyncSession],
    *,
    email: str,
    password: str,
    clock: Clock,
    hash_gate: HashGate,
    client_host: str,
    user_agent: str | None,
) -> LoginResult:
    now = clock.now()
    email_valid = True
    try:
        normalized = normalize_email(email)
    except InvalidEmail:
        email_valid = False
        normalized = ""
    attempt_key = _attempt_key(email)
    source_key = source_ref(client_host or "unknown")
    rules = [
        LimitRule(
            bucket_class="login_account",
            bucket_key=attempt_key,
            limit=LOGIN_FAILURE_LIMIT,
            window_seconds=LOGIN_FAILURE_WINDOW_SECONDS,
        ),
        LimitRule(
            bucket_class="login_source",
            bucket_key=source_key,
            limit=LOGIN_SOURCE_LIMIT,
            window_seconds=LOGIN_SOURCE_WINDOW_SECONDS,
        ),
    ]
    try:
        fact_ids = await reserve_attempts(factory, rules, now)
    except RateLimited as exc:
        async with write_transaction(factory) as session:
            write_audit(
                session,
                event_type="login_limited",
                result="limited",
                now=now,
                actor_kind="anonymous",
                subject_ref=audit_subject(normalized or email),
                reason_code="RATE_LIMITED",
                source_ref=source_key,
                target_kind="account",
            )
        raise ApiError(
            429,
            "RATE_LIMITED",
            "尝试次数过多，请稍后再试",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc

    account: Account | None = None
    if email_valid:
        async with read_session(factory) as session:
            account = await session.scalar(
                select(Account).where(Account.email_normalized == normalized)
            )

    password_ok = False
    try:
        if account is None or not account.password_hash:
            # dummy_hash() itself hashes with Argon2 and must stay off the event loop.
            await hash_gate.run(verify_dummy_password, password)
        else:
            password_ok = await hash_gate.run(verify_password, account.password_hash, password)
    except (AuthUnavailable, ApiError) as exc:
        reason_code, error = _dependency_failure(exc)
        await _best_effort_auth_unavailable(
            factory,
            fact_ids=fact_ids,
            now=now,
            subject=normalized or email,
            source_key=source_key,
            reason_code=reason_code,
        )
        raise error from exc

    if password_ok and account is not None and account.can_login:
        new_hash: str | None = None
        try:
            needs_rehash = password_needs_rehash(account.password_hash or "")
        except (AuthUnavailable, ApiError) as exc:
            reason_code, error = _dependency_failure(exc)
            await _best_effort_auth_unavailable(
                factory,
                fact_ids=fact_ids,
                now=now,
                subject=normalized,
                source_key=source_key,
                reason_code=reason_code,
            )
            raise error from exc
        if needs_rehash:
            try:
                new_hash = await hash_gate.run(hash_password, password)
            except (AuthUnavailable, ApiError) as exc:
                reason_code, error = _dependency_failure(exc)
                await _best_effort_auth_unavailable(
                    factory,
                    fact_ids=fact_ids,
                    now=now,
                    subject=normalized,
                    source_key=source_key,
                    reason_code=reason_code,
                )
                raise error from exc
        state_changed = False
        async with write_transaction(factory) as session:
            current = await session.get(Account, account.id)
            if (
                current is None
                or not current.can_login
                or current.session_epoch != account.session_epoch
            ):
                await settle_attempts_in_session(session, fact_ids, success=False)
                write_audit(
                    session,
                    event_type="login_failure",
                    result="failure",
                    now=now,
                    actor_kind="anonymous",
                    target_account_id=account.id,
                    target_kind="account",
                    subject_ref=audit_subject(normalized),
                    reason_code="state_changed",
                    source_ref=source_key,
                )
                state_changed = True
            if not state_changed:
                current.last_login_at = format_timestamp(now)
                current.updated_at = format_timestamp(now)
                if new_hash:
                    current.password_hash = new_hash
                    current.password_set_at = format_timestamp(now)
                    current.password_policy_version = WEAK_PASSWORD_LIST_VERSION
                record, raw_token, csrf_token = add_session(
                    session,
                    current,
                    now,
                    source_ref=source_key,
                    client_info=_client_info(user_agent),
                )
                await settle_attempts_in_session(session, fact_ids, success=True)
                write_audit(
                    session,
                    event_type="login_success",
                    result="success",
                    now=now,
                    actor_kind="account",
                    actor_account_id=current.id,
                    target_account_id=current.id,
                    target_kind="account",
                    target_id=current.id,
                    subject_ref=audit_subject(normalized),
                    source_ref=source_key,
                )
                return LoginResult(
                    account=current,
                    session_id=record.id,
                    csrf_token=csrf_token,
                    expires_at=record.absolute_expires_at,
                    raw_token=raw_token,
                )
        if state_changed:
            raise _generic_failure()

    async with write_transaction(factory) as session:
        await settle_attempts_in_session(session, fact_ids, success=False)
        write_audit(
            session,
            event_type="login_failure",
            result="failure",
            now=now,
            actor_kind="anonymous",
            target_account_id=account.id if account else None,
            target_kind="account",
            subject_ref=audit_subject(normalized or email),
            reason_code=_safe_reason_for_account(account, email_valid),
            source_ref=source_key,
        )
    raise _generic_failure()


async def logout(
    factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    account_id: str,
    now: datetime,
    source_ref_value: str | None,
) -> None:
    async with write_transaction(factory) as session:
        record = await session.get(AuthSession, session_id)
        if record is not None and record.account_id == account_id and record.revoked_at is None:
            record.revoked_at = format_timestamp(now)
            record.revoked_reason = "logout"
        write_audit(
            session,
            event_type="logout",
            result="success",
            now=now,
            actor_kind="account",
            actor_account_id=account_id,
            target_account_id=account_id,
            target_kind="session",
            target_id=session_id,
            source_ref=source_ref_value,
        )


async def rotate_csrf_for_session(
    factory: async_sessionmaker[AsyncSession], session_id: str, now: datetime
) -> str:
    return await rotate_csrf(factory, session_id, now)


def csrf_matches(session_digest: str, presented: str | None) -> bool:
    if not presented:
        return False
    return constant_time_equals(session_digest, token_digest(presented))


async def touch(
    factory: async_sessionmaker[AsyncSession], session_id: str, now: datetime
) -> None:
    await touch_session(factory, session_id, now)


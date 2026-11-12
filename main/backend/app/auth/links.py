from __future__ import annotations

import contextlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
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
)
from app.auth.rate_limit import (
    LimitRule,
    RateLimited,
    reserve_attempts,
    settle_attempts,
    settle_attempts_in_session,
)
from app.auth.service import InvalidEmail, normalize_email
from app.auth.tokens import new_token, source_ref, token_digest
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.timeutil import format_timestamp

LinkPurpose = Literal["setup", "reset"]
LINK_TTL_SECONDS = 30 * 60
LINK_ISSUE_ADMIN_LIMIT = 10
LINK_ISSUE_ADMIN_WINDOW_SECONDS = 15 * 60
LINK_ISSUE_TARGET_LIMIT = 3
LINK_ISSUE_TARGET_WINDOW_SECONDS = 30 * 60
LINK_VERIFY_CREDENTIAL_LIMIT = 5
LINK_VERIFY_CREDENTIAL_WINDOW_SECONDS = 15 * 60
LINK_VERIFY_SOURCE_LIMIT = 30
LINK_VERIFY_SOURCE_WINDOW_SECONDS = 15 * 60
LINK_INVALID_MESSAGE = "链接无效或已过期"


@dataclass(frozen=True)
class IssuedLink:
    account_id: str
    link: str
    purpose: LinkPurpose
    expires_at: str


@dataclass(frozen=True)
class LinkInfo:
    account_id: str
    account_email: str
    purpose: LinkPurpose
    expires_at: str


def _invalid() -> ApiError:
    return ApiError(400, "LINK_INVALID", LINK_INVALID_MESSAGE)


def _password_error(exc: PasswordPolicyError) -> ApiError:
    return ApiError(422, exc.code, exc.message)


def _dependency_error(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        if exc.code == "AUTH_BUSY":
            return ApiError(503, "AUTH_BUSY", "认证服务繁忙，请稍后重试")
        return exc
    return ApiError(503, "AUTH_UNAVAILABLE", "认证服务当前不可用")


def _rate_limited(exc: RateLimited) -> ApiError:
    return ApiError(
        429,
        "RATE_LIMITED",
        "尝试次数过多，请稍后再试",
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


def _mask_email(email: str) -> str:
    local, separator, domain = email.partition("@")
    if not separator:
        return "***"
    return f"{local[:1] or '*'}***@{domain}"


def _purpose_value(purpose: str) -> LinkPurpose:
    if purpose not in ("setup", "reset"):
        raise ApiError(400, "INVALID_PURPOSE", "链接用途不合法")
    return purpose  # type: ignore[return-value]


def _link_usable(link: AccountLink, account: Account, now: datetime) -> bool:
    if link.used_at is not None or link.revoked_at is not None:
        return False
    if link.expires_at <= format_timestamp(now):
        return False
    if account.is_disabled:
        return False
    if link.purpose == "setup":
        return account.status == "pending_setup"
    return account.status == "active"


async def _require_admin(session: AsyncSession, admin_account_id: str) -> Account:
    admin = await session.get(Account, admin_account_id)
    if admin is None or not admin.is_admin or admin.is_disabled or not admin.password_hash:
        raise ApiError(403, "FORBIDDEN", "没有管理权限")
    return admin


async def list_accounts(factory: async_sessionmaker[AsyncSession]) -> list[Account]:
    async with read_session(factory) as session:
        result = await session.scalars(select(Account).order_by(Account.created_at, Account.id))
        return list(result.all())


async def create_account(
    factory: async_sessionmaker[AsyncSession],
    *,
    admin_account_id: str,
    email: str,
    clock: Clock,
) -> Account:
    try:
        normalized = normalize_email(email)
    except InvalidEmail as exc:
        raise ApiError(422, "INVALID_EMAIL", "邮箱格式不正确") from exc
    now = clock.now()
    try:
        async with write_transaction(factory) as session:
            await _require_admin(session, admin_account_id)
            existing = await session.scalar(
                select(Account.id).where(Account.email_normalized == normalized)
            )
            if existing is not None:
                raise ApiError(409, "ACCOUNT_EXISTS", "该邮箱已存在")
            account = Account(
                id=str(uuid.uuid4()),
                email_normalized=normalized,
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
            write_audit(
                session,
                event_type="account_created",
                result="success",
                now=now,
                actor_kind="admin",
                actor_account_id=admin_account_id,
                target_account_id=account.id,
                target_kind="account",
                target_id=account.id,
                subject_ref=audit_subject(normalized),
                after={"is_admin": False, "status": "pending_setup"},
            )
            return account
    except IntegrityError as exc:
        raise ApiError(409, "ACCOUNT_EXISTS", "该邮箱已存在") from exc


async def issue_link(
    factory: async_sessionmaker[AsyncSession],
    *,
    admin_account_id: str,
    account_id: str,
    purpose: str,
    app_public_url: str | None,
    clock: Clock,
) -> IssuedLink:
    selected_purpose = _purpose_value(purpose)
    if not app_public_url:
        raise ApiError(503, "APP_PUBLIC_URL_NOT_CONFIGURED", "未配置受信任的应用地址")
    now = clock.now()
    now_text = format_timestamp(now)
    async with read_session(factory) as session:
        await _require_admin(session, admin_account_id)
        target = await session.get(Account, account_id)
        if target is None or target.is_admin:
            raise ApiError(404, "ACCOUNT_NOT_FOUND", "账号不存在")
        if target.is_disabled:
            raise ApiError(409, "ACCOUNT_DISABLED", "账号已停用")
        if selected_purpose == "setup" and target.status != "pending_setup":
            raise ApiError(409, "LINK_PURPOSE_MISMATCH", "该账号不需要设置密码")
        if selected_purpose == "reset" and target.status != "active":
            raise ApiError(409, "LINK_PURPOSE_MISMATCH", "该账号不能重置密码")
        target_email = target.email_normalized

    rules = [
        LimitRule(
            bucket_class="link_issue_admin",
            bucket_key=admin_account_id,
            limit=LINK_ISSUE_ADMIN_LIMIT,
            window_seconds=LINK_ISSUE_ADMIN_WINDOW_SECONDS,
        ),
        LimitRule(
            bucket_class="link_issue_target",
            bucket_key=account_id,
            limit=LINK_ISSUE_TARGET_LIMIT,
            window_seconds=LINK_ISSUE_TARGET_WINDOW_SECONDS,
        ),
    ]
    try:
        fact_ids = await reserve_attempts(factory, rules, now)
    except RateLimited as exc:
        async with write_transaction(factory) as session:
            write_audit(
                session,
                event_type="account_link_issue_limited",
                result="limited",
                now=now,
                actor_kind="admin",
                actor_account_id=admin_account_id,
                target_account_id=account_id,
                target_kind="account",
                target_id=account_id,
                subject_ref=audit_subject(target_email),
                reason_code="RATE_LIMITED",
            )
        raise _rate_limited(exc) from exc

    token = new_token()
    digest = token_digest(token)
    expires_at = format_timestamp(now + timedelta(seconds=LINK_TTL_SECONDS))
    try:
        async with write_transaction(factory) as session:
            await _require_admin(session, admin_account_id)
            target = await session.get(Account, account_id)
            if target is None or target.is_admin:
                raise ApiError(404, "ACCOUNT_NOT_FOUND", "账号不存在")
            if target.is_disabled:
                raise ApiError(409, "ACCOUNT_DISABLED", "账号已停用")
            if selected_purpose == "setup" and target.status != "pending_setup":
                raise ApiError(409, "LINK_PURPOSE_MISMATCH", "该账号不需要设置密码")
            if selected_purpose == "reset" and target.status != "active":
                raise ApiError(409, "LINK_PURPOSE_MISMATCH", "该账号不能重置密码")
            await session.execute(
                update(AccountLink)
                .where(
                    AccountLink.account_id == account_id,
                    AccountLink.used_at.is_(None),
                    AccountLink.revoked_at.is_(None),
                )
                .values(revoked_at=now_text, revoked_reason="superseded")
            )
            session.add(
                AccountLink(
                    id=str(uuid.uuid4()),
                    account_id=account_id,
                    purpose=selected_purpose,
                    token_digest=digest,
                    created_at=now_text,
                    expires_at=expires_at,
                    created_by=admin_account_id,
                )
            )
            # Successful issuance consumes the budget; the pending fact remains counted.
            write_audit(
                session,
                event_type="account_link_issued",
                result="success",
                now=now,
                actor_kind="admin",
                actor_account_id=admin_account_id,
                target_account_id=account_id,
                target_kind="account",
                target_id=account_id,
                subject_ref=audit_subject(target.email_normalized),
                detail={"purpose": selected_purpose, "expires_at": expires_at},
            )
        return IssuedLink(
            account_id=account_id,
            link=f"{app_public_url.rstrip('/')}/set-password#token={token}",
            purpose=selected_purpose,
            expires_at=expires_at,
        )
    except Exception:
        # A rejected issuance must not consume the administrator's budget.
        with contextlib.suppress(Exception):
            await settle_attempts(factory, fact_ids, success=True)
        raise


async def inspect_link(
    factory: async_sessionmaker[AsyncSession], *, raw_token: str, now: datetime
) -> LinkInfo:
    if not isinstance(raw_token, str) or not raw_token or len(raw_token) > 512:
        raise _invalid()
    digest = token_digest(raw_token)
    async with read_session(factory) as session:
        row = (
            await session.execute(
                select(AccountLink, Account)
                .join(Account, AccountLink.account_id == Account.id)
                .where(AccountLink.token_digest == digest)
            )
        ).first()
        if row is None:
            raise _invalid()
        link, account = row
        if not _link_usable(link, account, now):
            raise _invalid()
        return LinkInfo(
            account_id=account.id,
            account_email=_mask_email(account.email_normalized),
            purpose=link.purpose,  # type: ignore[arg-type]
            expires_at=link.expires_at,
        )


async def consume_link(
    factory: async_sessionmaker[AsyncSession],
    *,
    raw_token: str,
    purpose: str,
    password: str,
    now: datetime,
    hash_gate: HashGate,
    client_host: str,
) -> Account:
    selected_purpose = _purpose_value(purpose)
    if not isinstance(raw_token, str) or not raw_token or len(raw_token) > 512:
        raise _invalid()
    digest = token_digest(raw_token)
    source_key = source_ref(client_host or "unknown")
    rules = [
        LimitRule(
            bucket_class="link_verify_credential",
            bucket_key=digest,
            limit=LINK_VERIFY_CREDENTIAL_LIMIT,
            window_seconds=LINK_VERIFY_CREDENTIAL_WINDOW_SECONDS,
        ),
        LimitRule(
            bucket_class="link_verify_source",
            bucket_key=source_key,
            limit=LINK_VERIFY_SOURCE_LIMIT,
            window_seconds=LINK_VERIFY_SOURCE_WINDOW_SECONDS,
        ),
    ]
    try:
        fact_ids = await reserve_attempts(factory, rules, now)
    except RateLimited as exc:
        async with write_transaction(factory) as session:
            write_audit(
                session,
                event_type="account_link_verify_limited",
                result="limited",
                now=now,
                actor_kind="anonymous",
                subject_ref=digest[:48],
                reason_code="RATE_LIMITED",
                source_ref=source_key,
            )
        raise _rate_limited(exc) from exc

    async with read_session(factory) as session:
        row = (
            await session.execute(
                select(AccountLink, Account)
                .join(Account, AccountLink.account_id == Account.id)
                .where(AccountLink.token_digest == digest)
            )
        ).first()
        if row is None:
            await settle_attempts(factory, fact_ids, success=False)
            raise _invalid()
        link, account = row
        if not _link_usable(link, account, now) or link.purpose != selected_purpose:
            await settle_attempts(factory, fact_ids, success=False)
            raise _invalid()
        account_id = account.id
        account_email = account.email_normalized

    try:
        validate_password(password)
    except PasswordPolicyError as exc:
        await settle_attempts(factory, fact_ids, success=False)
        raise _password_error(exc) from exc
    except AuthUnavailable as exc:
        await settle_attempts(factory, fact_ids, success=True)
        raise _dependency_error(exc) from exc

    try:
        password_hash = await hash_gate.run(hash_password, password)
    except (AuthUnavailable, ApiError) as exc:
        await settle_attempts(factory, fact_ids, success=True)
        raise _dependency_error(exc) from exc

    rejected: str | None = None
    async with write_transaction(factory) as session:
        link = await session.scalar(
            select(AccountLink).where(AccountLink.token_digest == digest)
        )
        account = await session.get(Account, account_id)
        if (
            link is None
            or account is None
            or link.purpose != selected_purpose
            or not _link_usable(link, account, now)
        ):
            rejected = "LINK_INVALID"
            await settle_attempts_in_session(session, fact_ids, success=False)
            write_audit(
                session,
                event_type="account_link_used",
                result="denied",
                now=now,
                actor_kind="anonymous",
                target_account_id=account_id,
                target_kind="account",
                target_id=account_id,
                subject_ref=audit_subject(account_email),
                reason_code="LINK_INVALID",
                source_ref=source_key,
            )
        else:
            account.password_hash = password_hash
            account.password_set_at = format_timestamp(now)
            account.password_policy_version = WEAK_PASSWORD_LIST_VERSION
            account.session_epoch = account.session_epoch + 1
            account.updated_at = format_timestamp(now)
            link.used_at = format_timestamp(now)
            await session.execute(
                update(AuthSession)
                .where(
                    AuthSession.account_id == account_id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=format_timestamp(now), revoked_reason="password_set")
            )
            await session.execute(
                update(AccountLink)
                .where(
                    AccountLink.account_id == account_id,
                    AccountLink.id != link.id,
                    AccountLink.used_at.is_(None),
                    AccountLink.revoked_at.is_(None),
                )
                .values(revoked_at=format_timestamp(now), revoked_reason="consumed")
            )
            await settle_attempts_in_session(session, fact_ids, success=True)
            write_audit(
                session,
                event_type="account_link_used",
                result="success",
                now=now,
                actor_kind="account",
                actor_account_id=account.id,
                target_account_id=account.id,
                target_kind="account",
                target_id=account.id,
                subject_ref=audit_subject(account_email),
                source_ref=source_key,
                detail={"purpose": selected_purpose},
            )
            return account
    if rejected:
        raise _invalid()
    raise _invalid()


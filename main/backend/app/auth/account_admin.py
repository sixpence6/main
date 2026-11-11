from __future__ import annotations

from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.audit import audit_subject, write_audit
from app.auth.models import Account, AccountLink, AuthSession
from app.db import write_transaction
from app.errors import ApiError
from app.timeutil import format_timestamp


def _snapshot(account: Account) -> dict[str, object]:
    return {
        "is_disabled": bool(account.is_disabled),
        "status": account.status,
        "session_epoch": account.session_epoch,
    }


async def _target_account(session: AsyncSession, account_id: str) -> Account:
    target = await session.get(Account, account_id)
    # 管理入口只操作普通账号；管理员账号不存在可被管理这一语义。
    if target is None or target.is_admin:
        raise ApiError(404, "ACCOUNT_NOT_FOUND", "账号不存在")
    return target


async def _set_disabled(
    factory: async_sessionmaker[AsyncSession],
    *,
    admin_account_id: str,
    account_id: str,
    disabled: bool,
    now: datetime,
    source_ref_value: str | None,
) -> Account:
    async with write_transaction(factory) as session:
        admin = await session.get(Account, admin_account_id)
        if admin is None or not admin.is_admin or admin.is_disabled or not admin.password_hash:
            raise ApiError(403, "FORBIDDEN", "没有管理权限")
        target = await _target_account(session, account_id)
        if bool(target.is_disabled) == disabled:
            return target

        before = _snapshot(target)
        timestamp = format_timestamp(now)
        if disabled:
            target.is_disabled = 1
            target.disabled_at = timestamp
            target.disabled_by = admin_account_id
            reason = "account_disabled"
            event_type = "account_disabled"
        else:
            target.is_disabled = 0
            target.disabled_at = None
            target.disabled_by = None
            reason = "account_restored"
            event_type = "account_restored"
        target.session_epoch += 1
        target.updated_at = timestamp

        session_result = await session.execute(
            update(AuthSession)
            .where(AuthSession.account_id == target.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=timestamp, revoked_reason=reason)
        )
        link_result = await session.execute(
            update(AccountLink)
            .where(
                AccountLink.account_id == target.id,
                AccountLink.used_at.is_(None),
                AccountLink.revoked_at.is_(None),
            )
            .values(revoked_at=timestamp, revoked_reason=reason)
        )
        after = _snapshot(target)
        write_audit(
            session,
            event_type=event_type,
            result="success",
            now=now,
            actor_kind="admin",
            actor_account_id=admin_account_id,
            target_account_id=target.id,
            target_kind="account",
            target_id=target.id,
            subject_ref=audit_subject(target.email_normalized),
            source_ref=source_ref_value,
            before=before,
            after=after,
            detail={
                "sessions_revoked": int(session_result.rowcount or 0),
                "links_revoked": int(link_result.rowcount or 0),
            },
        )
        return target


async def disable_account(
    factory: async_sessionmaker[AsyncSession],
    *,
    admin_account_id: str,
    account_id: str,
    now: datetime,
    source_ref_value: str | None = None,
) -> Account:
    return await _set_disabled(
        factory,
        admin_account_id=admin_account_id,
        account_id=account_id,
        disabled=True,
        now=now,
        source_ref_value=source_ref_value,
    )


async def restore_account(
    factory: async_sessionmaker[AsyncSession],
    *,
    admin_account_id: str,
    account_id: str,
    now: datetime,
    source_ref_value: str | None = None,
) -> Account:
    return await _set_disabled(
        factory,
        admin_account_id=admin_account_id,
        account_id=account_id,
        disabled=False,
        now=now,
        source_ref_value=source_ref_value,
    )

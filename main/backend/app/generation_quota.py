from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.audit import audit_subject, write_audit
from app.auth.models import Account
from app.config import Settings
from app.db import read_session
from app.errors import ApiError, validation_error
from app.models import Base
from app.timeutil import format_timestamp


class GenerationQuotaLimit(Base):
    """Instance-wide or per-account override of the generation limits.

    A missing row means "inherit the settings default". NULL columns inside an
    existing row also mean inherit. Account rows never carry an instance-slot
    override: only the instance row can change the shared concurrency cap.
    """

    __tablename__ = "generation_quota_limits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(36), nullable=False)
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    personal_slots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instance_slots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("scope", "scope_id", name="uq_generation_quota_limits_scope"),
        CheckConstraint(
            "scope IN ('instance','account')", name="ck_generation_quota_limits_scope"
        ),
        CheckConstraint(
            "daily_limit IS NULL OR daily_limit > 0",
            name="ck_generation_quota_limits_daily",
        ),
        CheckConstraint(
            "personal_slots IS NULL OR personal_slots > 0",
            name="ck_generation_quota_limits_personal",
        ),
        CheckConstraint(
            "instance_slots IS NULL OR instance_slots > 0",
            name="ck_generation_quota_limits_instance",
        ),
    )


@dataclass(frozen=True)
class EffectiveLimits:
    daily_limit: int
    personal_slots: int
    instance_slots: int
    project_slots: int


@dataclass(frozen=True)
class QuotaPolicy:
    daily_limit: int | None
    personal_slots: int | None
    instance_slots: int | None
    updated_at: str | None


EMPTY_POLICY = QuotaPolicy(None, None, None, None)


def default_limits(settings: Settings) -> EffectiveLimits:
    return EffectiveLimits(
        daily_limit=int(settings.generation_daily_limit),
        personal_slots=int(settings.generation_personal_slots),
        instance_slots=int(settings.generation_instance_slots),
        project_slots=int(settings.generation_project_slots),
    )


async def _row(
    session: AsyncSession, *, scope: str, scope_id: str
) -> GenerationQuotaLimit | None:
    return await session.scalar(
        select(GenerationQuotaLimit).where(
            GenerationQuotaLimit.scope == scope,
            GenerationQuotaLimit.scope_id == scope_id,
        )
    )


def _policy(row: GenerationQuotaLimit | None) -> QuotaPolicy:
    if row is None:
        return EMPTY_POLICY
    return QuotaPolicy(
        daily_limit=row.daily_limit,
        personal_slots=row.personal_slots,
        instance_slots=row.instance_slots,
        updated_at=row.updated_at,
    )


def _effective(
    settings: Settings,
    instance: GenerationQuotaLimit | None,
    account: GenerationQuotaLimit | None,
) -> EffectiveLimits:
    defaults = default_limits(settings)
    return EffectiveLimits(
        daily_limit=(
            account.daily_limit if account and account.daily_limit is not None
            else instance.daily_limit if instance and instance.daily_limit is not None
            else defaults.daily_limit
        ),
        personal_slots=(
            account.personal_slots if account and account.personal_slots is not None
            else instance.personal_slots if instance and instance.personal_slots is not None
            else defaults.personal_slots
        ),
        instance_slots=(
            instance.instance_slots if instance and instance.instance_slots is not None
            else defaults.instance_slots
        ),
        project_slots=defaults.project_slots,
    )


async def effective_limits(
    session: AsyncSession, *, settings: Settings, account_id: str
) -> EffectiveLimits:
    return _effective(
        settings,
        await _row(session, scope="instance", scope_id="instance"),
        await _row(session, scope="account", scope_id=account_id),
    )


async def read_instance_limits(
    factory: async_sessionmaker[AsyncSession], *, settings: Settings
) -> tuple[QuotaPolicy, EffectiveLimits]:
    async with read_session(factory) as session:
        instance = await _row(session, scope="instance", scope_id="instance")
        return _policy(instance), _effective(settings, instance, None)


async def read_account_limits(
    factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    account_id: str,
) -> tuple[QuotaPolicy, EffectiveLimits, str]:
    async with read_session(factory) as session:
        account = await session.get(Account, account_id)
        if account is None or account.is_admin:
            raise ApiError(404, "ACCOUNT_NOT_FOUND", "账号不存在。")
        instance = await _row(session, scope="instance", scope_id="instance")
        own = await _row(session, scope="account", scope_id=account_id)
        return _policy(own), _effective(settings, instance, own), account.email_normalized


def _positive_or_none(value: int | None, label: str) -> int | None:
    if value is not None and (not isinstance(value, int) or value <= 0):
        raise validation_error(f"{label}必须是正整数")
    return value


async def _require_admin(session: AsyncSession, admin_account_id: str) -> Account:
    admin = await session.get(Account, admin_account_id)
    if admin is None or not admin.can_login:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录")
    if not admin.is_admin:
        raise ApiError(403, "ADMIN_REQUIRED", "需要管理员权限。")
    return admin


async def _target_account(session: AsyncSession, account_id: str) -> Account:
    account = await session.get(Account, account_id)
    if account is None or account.is_admin:
        raise ApiError(404, "ACCOUNT_NOT_FOUND", "账号不存在。")
    return account


def _snapshot(
    policy: QuotaPolicy, effective: EffectiveLimits
) -> dict[str, Any]:
    return {
        "daily_limit": policy.daily_limit,
        "personal_slots": policy.personal_slots,
        "instance_slots": policy.instance_slots,
        "effective_daily_limit": effective.daily_limit,
        "effective_personal_slots": effective.personal_slots,
        "effective_instance_slots": effective.instance_slots,
        "effective_project_slots": effective.project_slots,
    }


async def _upsert(
    session: AsyncSession,
    *,
    row: GenerationQuotaLimit | None,
    scope: str,
    scope_id: str,
    daily_limit: int | None,
    personal_slots: int | None,
    instance_slots: int | None,
    admin_account_id: str,
    now: datetime,
) -> GenerationQuotaLimit | None:
    if daily_limit is None and personal_slots is None and instance_slots is None:
        if row is not None:
            await session.delete(row)
            await session.flush()
        return None
    timestamp = format_timestamp(now)
    if row is None:
        row = GenerationQuotaLimit(
            id=str(uuid.uuid4()),
            scope=scope,
            scope_id=scope_id,
            daily_limit=daily_limit,
            personal_slots=personal_slots,
            instance_slots=instance_slots,
            updated_by=admin_account_id,
            updated_at=timestamp,
        )
        session.add(row)
    else:
        row.daily_limit = daily_limit
        row.personal_slots = personal_slots
        row.instance_slots = instance_slots
        row.updated_by = admin_account_id
        row.updated_at = timestamp
    await session.flush()
    return row


async def update_instance_limits(
    factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    admin_account_id: str,
    daily_limit: int | None,
    personal_slots: int | None,
    instance_slots: int | None,
    now: datetime,
    source_ref: str | None = None,
) -> tuple[QuotaPolicy, EffectiveLimits]:
    from app.db import write_transaction

    daily_limit = _positive_or_none(daily_limit, "每日受理次数")
    personal_slots = _positive_or_none(personal_slots, "个人活跃槽位")
    instance_slots = _positive_or_none(instance_slots, "实例并发槽位")
    async with write_transaction(factory) as session:
        admin = await _require_admin(session, admin_account_id)
        row = await _row(session, scope="instance", scope_id="instance")
        before = _snapshot(_policy(row), _effective(settings, row, None))
        row = await _upsert(
            session,
            row=row,
            scope="instance",
            scope_id="instance",
            daily_limit=daily_limit,
            personal_slots=personal_slots,
            instance_slots=instance_slots,
            admin_account_id=admin.id,
            now=now,
        )
        after = _snapshot(_policy(row), _effective(settings, row, None))
        write_audit(
            session,
            event_type="generation_limits_updated",
            result="success",
            now=now,
            actor_kind="admin",
            actor_account_id=admin.id,
            target_account_id=None,
            target_kind="generation_limits",
            target_id="instance",
            subject_ref=audit_subject("instance"),
            source_ref=source_ref,
            before=before,
            after=after,
            detail={"scope": "instance"},
        )
        await session.flush()
        return _policy(row), _effective(settings, row, None)


async def update_account_limits(
    factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    admin_account_id: str,
    account_id: str,
    daily_limit: int | None,
    personal_slots: int | None,
    now: datetime,
    source_ref: str | None = None,
) -> tuple[QuotaPolicy, EffectiveLimits, str]:
    from app.db import write_transaction

    daily_limit = _positive_or_none(daily_limit, "每日受理次数")
    personal_slots = _positive_or_none(personal_slots, "个人活跃槽位")
    async with write_transaction(factory) as session:
        admin = await _require_admin(session, admin_account_id)
        target = await _target_account(session, account_id)
        instance = await _row(session, scope="instance", scope_id="instance")
        row = await _row(session, scope="account", scope_id=account_id)
        before = _snapshot(_policy(row), _effective(settings, instance, row))
        row = await _upsert(
            session,
            row=row,
            scope="account",
            scope_id=account_id,
            daily_limit=daily_limit,
            personal_slots=personal_slots,
            instance_slots=None,
            admin_account_id=admin.id,
            now=now,
        )
        after = _snapshot(_policy(row), _effective(settings, instance, row))
        write_audit(
            session,
            event_type="generation_limits_updated",
            result="success",
            now=now,
            actor_kind="admin",
            actor_account_id=admin.id,
            target_account_id=target.id,
            target_kind="account",
            target_id=target.id,
            subject_ref=audit_subject(target.email_normalized),
            source_ref=source_ref,
            before=before,
            after=after,
            detail={"scope": "account"},
        )
        await session.flush()
        return _policy(row), _effective(settings, instance, row), target.email_normalized


__all__ = [
    "EMPTY_POLICY",
    "EffectiveLimits",
    "GenerationQuotaLimit",
    "QuotaPolicy",
    "default_limits",
    "effective_limits",
    "read_account_limits",
    "read_instance_limits",
    "update_account_limits",
    "update_instance_limits",
]

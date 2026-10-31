from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.audit import write_audit
from app.auth.models import Account
from app.config import Settings
from app.db import read_session
from app.errors import ApiError, conflict, validation_error
from app.models import Base, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow.tables import Artifact, WorkflowRun

#: A accepted generation remains counted for this rolling window.
GENERATION_WINDOW_MS = 24 * 60 * 60 * 1000


class GenerationIntent(Base):
    """An immutable, one-shot authorization created by one user confirmation."""

    __tablename__ = "generation_intents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    owner_account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    operator_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    approval_id: Mapped[str | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requirement_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=True
    )
    artifact_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reference_usage: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    image_size: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="accepted")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "request_id", name="uq_generation_intents_project_request"),
        CheckConstraint(
            "status IN ('accepted','executing','delivered','failed','abandoned')",
            name="ck_generation_intents_status",
        ),
        CheckConstraint(
            "reference_usage IN ('none','product','style','composition')",
            name="ck_generation_intents_reference_usage",
        ),
        Index("ix_generation_intents_project_created", "project_id", "created_at"),
        Index("ix_generation_intents_run_created", "run_id", "created_at"),
    )


class GenerationUsage(Base):
    """The acceptance ledger. A row is never deleted when a task terminates."""

    __tablename__ = "generation_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    intent_id: Mapped[str] = mapped_column(
        ForeignKey("generation_intents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    window_start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    window_expires_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        Index("ix_generation_usage_account_window", "account_id", "window_start_ms"),
        CheckConstraint(
            "window_expires_ms > window_start_ms",
            name="ck_generation_usage_window",
        ),
    )


class GenerationSlot(Base):
    """A live personal/instance/project occupancy released only on proven terminal state."""

    __tablename__ = "generation_slots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    intent_id: Mapped[str] = mapped_column(
        ForeignKey("generation_intents.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    released_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "scope IN ('personal','instance','project')",
            name="ck_generation_slots_scope",
        ),
        Index("ix_generation_slots_active", "scope", "scope_id", "released_at"),
        Index("ix_generation_slots_intent", "intent_id"),
    )


@dataclass(frozen=True)
class AcceptanceResult:
    intent: GenerationIntent
    replay: bool


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _now_ms(now: datetime | None = None) -> int:
    return int((now or utc_now()).timestamp() * 1000)


def normalize_request_id(value: Any) -> str | None:
    """Validate the client's stable idempotency key. Absence is legacy-only."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise validation_error("request_id 必须是 UUID 字符串")
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError) as exc:
        raise validation_error("request_id 必须是 UUID 字符串") from exc


def _canonical_request(
    *,
    project_id: str,
    run_id: str,
    owner_account_id: str,
    operator_account_id: str,
    request_id: str,
    requirement_revision: int,
    artifact_id: str,
    artifact_digest: str,
    reference_asset_id: str | None,
    reference_usage: str,
    image_size: str,
) -> dict[str, Any]:
    return {
        "kind": "generation-intent-v1",
        "project_id": project_id,
        "run_id": run_id,
        "owner_account_id": owner_account_id,
        "operator_account_id": operator_account_id,
        "request_id": request_id,
        "requirement_revision": requirement_revision,
        "artifact_id": artifact_id,
        "artifact_digest": artifact_digest,
        "reference_asset_id": reference_asset_id,
        "reference_usage": reference_usage,
        "image_size": image_size,
    }


def intent_request_digest(
    *,
    project_id: str,
    run_id: str,
    owner_account_id: str,
    operator_account_id: str,
    request_id: str,
    requirement_revision: int,
    artifact_id: str,
    artifact_digest: str,
    reference_asset_id: str | None,
    reference_usage: str,
    image_size: str,
) -> str:
    return _digest(
        _canonical_request(
            project_id=project_id,
            run_id=run_id,
            owner_account_id=owner_account_id,
            operator_account_id=operator_account_id,
            request_id=request_id,
            requirement_revision=requirement_revision,
            artifact_id=artifact_id,
            artifact_digest=artifact_digest,
            reference_asset_id=reference_asset_id,
            reference_usage=reference_usage,
            image_size=image_size,
        )
    )


def _limit(settings: Settings, name: str) -> int:
    return int(getattr(settings, name))


async def _require_active_account(session: AsyncSession, account_id: str) -> Account:
    account = await session.get(Account, account_id)
    if account is None or not account.can_login:
        code = (
            "ACCOUNT_DISABLED"
            if account is not None and account.is_disabled
            else "AUTH_REQUIRED"
        )
        raise ApiError(401, code, "账号当前不可用，不能受理生成任务。")
    return account


async def _active_count(
    session: AsyncSession, *, scope: str, scope_id: str
) -> int:
    value = await session.scalar(
        select(func.count())
        .select_from(GenerationSlot)
        .where(
            GenerationSlot.scope == scope,
            GenerationSlot.scope_id == scope_id,
            GenerationSlot.released_at.is_(None),
        )
    )
    return int(value or 0)


async def _used_count(
    session: AsyncSession, *, account_id: str, now_ms: int
) -> int:
    cutoff = now_ms - GENERATION_WINDOW_MS
    value = await session.scalar(
        select(func.count())
        .select_from(GenerationUsage)
        .where(
            GenerationUsage.account_id == account_id,
            GenerationUsage.window_start_ms > cutoff,
            GenerationUsage.window_start_ms <= now_ms,
        )
    )
    return int(value or 0)


def _limit_error(code: str, message: str, *, limit: int, used: int) -> ApiError:
    return ApiError(429, code, message)


async def accept_in_session(
    session: AsyncSession,
    *,
    settings: Settings,
    project: Project,
    run: WorkflowRun,
    operator_account_id: str,
    artifact: Artifact,
    requirement_revision: int,
    reference_usage: str,
    image_size: str,
    request_id: str,
    approval_id: str | None = None,
    now: datetime | None = None,
) -> AcceptanceResult:
    """Create the immutable intent, usage row and three slots in one transaction.

    The caller already holds the BEGIN IMMEDIATE write transaction, so the
    counts read here cannot race another acceptance.
    """

    if project.owner_account_id is None:
        raise conflict("OWNERSHIP_REQUIRED", "项目缺少归属，不能受理生成任务。")
    if project.owner_account_id != operator_account_id:
        raise conflict("OWNERSHIP_MISMATCH", "项目归属不一致，不能受理生成任务。")
    await _require_active_account(session, operator_account_id)
    if project.revision != requirement_revision:
        raise conflict("REVISION_CONFLICT", "项目需求版本已更新，请刷新后重新确认。")
    if run.project_id != project.id:
        raise conflict("WORKFLOW_RUN_CHANGED", "当前流程已变化，请刷新后重新确认。")
    current_artifact = await session.get(Artifact, artifact.id)
    if (
        current_artifact is None
        or current_artifact.project_id != project.id
        or current_artifact.run_id != run.id
        or current_artifact.digest != artifact.digest
    ):
        raise conflict("CARD_DIGEST_MISMATCH", "需求卡版本已变化，请刷新后重新确认。")
    if image_size.strip() != image_size or not image_size:
        raise validation_error("图片尺寸无效")
    if reference_usage not in {"none", "product", "style", "composition"}:
        raise validation_error("参考图用途无效")

    digest = intent_request_digest(
        project_id=project.id,
        run_id=run.id,
        owner_account_id=project.owner_account_id,
        operator_account_id=operator_account_id,
        request_id=request_id,
        requirement_revision=requirement_revision,
        artifact_id=artifact.id,
        artifact_digest=artifact.digest,
        reference_asset_id=project.reference_asset_id,
        reference_usage=reference_usage,
        image_size=image_size,
    )
    existing = await session.scalar(
        select(GenerationIntent).where(
            GenerationIntent.project_id == project.id,
            GenerationIntent.request_id == request_id,
        )
    )
    if existing is not None:
        if (
            existing.request_digest != digest
            or existing.artifact_digest != artifact.digest
            or existing.requirement_revision != requirement_revision
            or existing.reference_asset_id != project.reference_asset_id
            or existing.reference_usage != reference_usage
            or existing.image_size != image_size
            or existing.status not in {"accepted", "executing", "delivered"}
        ):
            raise conflict(
                "INTENT_REPLAY_CONFLICT",
                "同一请求标识已绑定不同的生成参数，请刷新后重新确认。",
            )
        return AcceptanceResult(intent=existing, replay=True)

    now_dt = now or utc_now()
    now_ms = _now_ms(now_dt)
    used = await _used_count(session, account_id=operator_account_id, now_ms=now_ms)
    daily_limit = _limit(settings, "generation_daily_limit")
    if used >= daily_limit:
        raise _limit_error(
            "GENERATION_LIMIT_REACHED",
            f"过去 24 小时已受理 {used} 次，达到上限 {daily_limit} 次。",
            limit=daily_limit,
            used=used,
        )
    personal_limit = _limit(settings, "generation_personal_slots")
    personal_active = await _active_count(
        session, scope="personal", scope_id=operator_account_id
    )
    if personal_active >= personal_limit:
        raise _limit_error(
            "GENERATION_SLOT_BUSY",
            "你已有进行中的生成任务，请等待其结束。",
            limit=personal_limit,
            used=personal_active,
        )
    instance_limit = _limit(settings, "generation_instance_slots")
    instance_active = await _active_count(session, scope="instance", scope_id="instance")
    if instance_active >= instance_limit:
        raise _limit_error(
            "GENERATION_SLOT_BUSY",
            "实例当前生成任务已满，请稍后重试。",
            limit=instance_limit,
            used=instance_active,
        )
    project_limit = _limit(settings, "generation_project_slots")
    project_active = await _active_count(
        session, scope="project", scope_id=project.id
    )
    if project_active >= project_limit:
        raise _limit_error(
            "GENERATION_SLOT_BUSY",
            "该项目已有进行中的生成任务，请等待其结束。",
            limit=project_limit,
            used=project_active,
        )

    intent = GenerationIntent(
        id=str(uuid.uuid4()),
        project_id=project.id,
        run_id=run.id,
        owner_account_id=project.owner_account_id,
        operator_account_id=operator_account_id,
        approval_id=approval_id,
        request_id=request_id,
        request_digest=digest,
        requirement_revision=requirement_revision,
        artifact_id=artifact.id,
        artifact_digest=artifact.digest,
        reference_asset_id=project.reference_asset_id,
        reference_usage=reference_usage,
        image_size=image_size,
        status="accepted",
        created_at=format_timestamp(now_dt),
        updated_at=format_timestamp(now_dt),
    )
    session.add(intent)
    await session.flush()
    session.add(
        GenerationUsage(
            id=str(uuid.uuid4()),
            intent_id=intent.id,
            account_id=operator_account_id,
            project_id=project.id,
            window_start_ms=now_ms,
            window_expires_ms=now_ms + GENERATION_WINDOW_MS,
            created_at=format_timestamp(now_dt),
        )
    )
    for scope, scope_id in (
        ("personal", operator_account_id),
        ("instance", "instance"),
        ("project", project.id),
    ):
        session.add(
            GenerationSlot(
                id=str(uuid.uuid4()),
                intent_id=intent.id,
                scope=scope,
                scope_id=scope_id,
                created_at=format_timestamp(now_dt),
            )
        )
    await session.flush()
    write_audit(
        session,
        event_type="generation_intent_accepted",
        result="success",
        now=now_dt,
        actor_kind="account",
        actor_account_id=operator_account_id,
        target_account_id=operator_account_id,
        target_kind="project",
        target_id=project.id,
        subject_ref=intent.id,
        reason_code=None,
        detail={
            "request_id": request_id,
            "requirement_revision": requirement_revision,
            "daily_limit": daily_limit,
            "used_before": used,
            "used_after": used + 1,
            "personal_limit": personal_limit,
            "instance_limit": instance_limit,
            "project_limit": project_limit,
        },
    )
    await session.flush()
    return AcceptanceResult(intent=intent, replay=False)


async def latest_intent(
    factory: async_sessionmaker[AsyncSession], *, project_id: str, run_id: str
) -> GenerationIntent | None:
    async with read_session(factory) as session:
        return await session.scalar(
            select(GenerationIntent)
            .where(
                GenerationIntent.project_id == project_id,
                GenerationIntent.run_id == run_id,
            )
            .order_by(GenerationIntent.created_at.desc(), GenerationIntent.id.desc())
            .limit(1)
        )


async def intent_for_request(
    session: AsyncSession, *, project_id: str, request_id: str
) -> GenerationIntent | None:
    return await session.scalar(
        select(GenerationIntent).where(
            GenerationIntent.project_id == project_id,
            GenerationIntent.request_id == request_id,
        )
    )


async def usage_snapshot(
    factory: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    account_id: str | None,
    project_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_ms = _now_ms(now)
    async with read_session(factory) as session:
        used = (
            await _used_count(session, account_id=account_id, now_ms=now_ms)
            if account_id
            else 0
        )
        personal = (
            await _active_count(session, scope="personal", scope_id=account_id)
            if account_id
            else 0
        )
        instance = await _active_count(session, scope="instance", scope_id="instance")
        project = await _active_count(session, scope="project", scope_id=project_id)
    daily_limit = _limit(settings, "generation_daily_limit")
    personal_limit = _limit(settings, "generation_personal_slots")
    return {
        "daily_limit": daily_limit,
        "used": used,
        "remaining": max(daily_limit - used, 0),
        "window_started_at": None,
        "window_expires_at": None,
        "personal_active": personal,
        "personal_limit": personal_limit,
        "instance_active": instance,
        "instance_limit": _limit(settings, "generation_instance_slots"),
        "project_active": project,
        "project_limit": _limit(settings, "generation_project_slots"),
    }


async def release_slots_in_session(
    session: AsyncSession, *, intent_id: str, reason: str, now: datetime | None = None
) -> int:
    released_at = format_timestamp(now or utc_now())
    result = await session.execute(
        update(GenerationSlot)
        .where(
            GenerationSlot.intent_id == intent_id,
            GenerationSlot.released_at.is_(None),
        )
        .values(released_at=released_at, release_reason=reason[:64])
    )
    await session.flush()
    return int(result.rowcount or 0)


async def set_executing_in_session(
    session: AsyncSession, intent: GenerationIntent, *, now: datetime | None = None
) -> None:
    if intent.status == "accepted":
        intent.status = "executing"
        intent.updated_at = format_timestamp(now or utc_now())
        await session.flush()


async def abandon_intent(
    factory: async_sessionmaker[AsyncSession],
    *,
    intent_id: str,
    reason: str,
    now: datetime | None = None,
) -> bool:
    """Abandon an unaccepted/executing intent and release its live slots.

    The usage ledger is deliberately untouched: an accepted generation remains
    counted even when the prompt cannot be grounded in the confirmed card.
    """
    from app.db import write_transaction

    changed = False
    async with write_transaction(factory) as session:
        intent = await session.get(GenerationIntent, intent_id)
        if intent is None or intent.status not in {"accepted", "executing"}:
            return False
        timestamp = format_timestamp(now or utc_now())
        intent.status = "abandoned"
        intent.updated_at = timestamp
        await release_slots_in_session(session, intent_id=intent.id, reason=reason, now=now)
        write_audit(
            session,
            event_type="generation_intent_abandoned",
            result="success",
            now=now or utc_now(),
            actor_kind="system",
            target_kind="generation_intent",
            target_id=intent.id,
            reason_code=reason[:64],
            detail={"intent_id": intent.id, "reason": reason[:120]},
        )
        await session.flush()
        changed = True
    return changed


async def set_intent_status(
    factory: async_sessionmaker[AsyncSession],
    *,
    intent_id: str,
    status: str,
    now: datetime | None = None,
) -> None:
    if status not in {"accepted", "executing", "delivered", "failed", "abandoned"}:
        raise ValueError(f"invalid intent status {status}")
    async with read_session(factory) as session:
        intent = await session.get(GenerationIntent, intent_id)
        if intent is None:
            return
    from app.db import write_transaction

    async with write_transaction(factory) as session:
        intent = await session.get(GenerationIntent, intent_id)
        if intent is not None:
            intent.status = status
            intent.updated_at = format_timestamp(now or utc_now())
            await session.flush()


__all__ = [
    "GENERATION_WINDOW_MS",
    "AcceptanceResult",
    "GenerationIntent",
    "GenerationSlot",
    "GenerationUsage",
    "abandon_intent",
    "accept_in_session",
    "intent_for_request",
    "intent_request_digest",
    "latest_intent",
    "normalize_request_id",
    "release_slots_in_session",
    "set_executing_in_session",
    "set_intent_status",
    "usage_snapshot",
]

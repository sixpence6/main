from __future__ import annotations

import asyncio
import hashlib
import json
import stat
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuditEvent
from app.config import Settings
from app.db import read_session, write_transaction
from app.models import Asset, Generation, Project
from app.services.assets import inspect_image
from app.timeutil import format_timestamp, utc_now
from app.workflow.tables import (
    Approval,
    Artifact,
    Delivery,
    DiagnosticEvent,
    GenerationJob,
    LegacyItem,
)

# T20：保留期清理只处理明确的终态候选，绝不按目录递归或按时间全表删除。
CANDIDATE_TTL = timedelta(hours=24)
DIAGNOSTIC_TTL = timedelta(days=30)
AUDIT_TTL = timedelta(days=90)
# 清理后保留数据库行，但写入可审计的保留期标记；备份据此区分
# “到期已清理”与真正的数据库悬挂引用。
RETENTION_EXPIRED_NOTE = "retention_expired"
TERMINAL_GENERATION_STATUSES = frozenset({"succeeded", "failed", "unknown"})
NON_TERMINAL_JOB_STATUSES = frozenset(
    {"created", "submit_pending", "submitted", "remote_pending", "result_fetched"}
)
PROTECTED_JOB_STATUSES = frozenset({"approved", "published"})
PROTECTED_DIAGNOSTIC_EVENTS = frozenset(
    {
        "recovery_checkpoint",
        "recovery_resumed",
        "reference_requirement_changed",
        "reference_usage_selected",
        "requirements_brief_approved",
        "requirements_confirmation_invalidated",
        "requirements_confirmed",
        "requirements_revision_requested",
        "generation_risk_acknowledged",
    }
)
_REPARSE_POINT_ATTRIBUTE = 0x400
_MAX_TEXT = 160


@dataclass(frozen=True)
class RetentionPolicy:
    candidate_ttl: timedelta = CANDIDATE_TTL
    diagnostic_ttl: timedelta = DIAGNOSTIC_TTL
    audit_ttl: timedelta = AUDIT_TTL


@dataclass(frozen=True)
class RetentionFailure:
    object_kind: str
    object_id: str
    stage: str
    error_code: str


@dataclass
class RetentionReport:
    scanned_generations: int = 0
    scanned_diagnostics: int = 0
    scanned_audits: int = 0
    deleted_asset_ids: set[str] = field(default_factory=set)
    deleted_diagnostic_ids: set[str] = field(default_factory=set)
    deleted_audit_ids: set[str] = field(default_factory=set)
    failures: list[RetentionFailure] = field(default_factory=list)


@dataclass(frozen=True)
class GenerationRetention:
    """视图展示只读派生信息，不持久化，也不反推或重写 completed_at。"""

    status: str
    terminal_at: str
    expires_at: str


@dataclass(frozen=True)
class _Candidate:
    asset: Asset
    expires_at: datetime
    preserved: bool
    error_code: str | None = None


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def _deadline(generation: Generation, policy: RetentionPolicy) -> datetime | None:
    # 原终止时间唯一权威来源是终态时写入的 completed_at；禁止用 created_at、
    # updated_at、启动时间或任何当前时间推算。
    terminal = _parse_timestamp(generation.completed_at)
    return None if terminal is None else terminal + policy.candidate_ttl


def _contains_id(value: Any, target: str) -> bool:
    if isinstance(value, str):
        return value == target
    if isinstance(value, dict):
        return any(_contains_id(item, target) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_id(item, target) for item in value)
    return False


def _json_contains_id(raw: str | None, target: str) -> bool:
    if not raw:
        return False
    try:
        return _contains_id(json.loads(raw), target)
    except (TypeError, ValueError):
        return target in raw


def _has_reparse_component(path: Path, base: Path) -> bool:
    current = path
    while current != base and current != current.parent:
        try:
            info = current.lstat()
        except OSError:
            # 缺失组件不会成为可删除目标；按不安全处理，调用方会跳过。
            return True
        if stat.S_ISLNK(info.st_mode):
            return True
        if getattr(info, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE:
            return True
        current = current.parent
    return False


def _safe_candidate_path(settings: Settings, asset: Asset) -> tuple[Path | None, str | None]:
    raw = (asset.relative_path or "").strip()
    if not raw:
        return None, "ASSET_PATH_UNSAFE"
    relative = Path(raw)
    if relative.is_absolute() or relative.drive:
        return None, "ASSET_PATH_UNSAFE"
    if any(part in ("", ".", "..") for part in relative.parts):
        return None, "ASSET_PATH_UNSAFE"
    base = settings.resolved_data_dir().resolve()
    if not relative.parts or relative.parts[0] != settings.generated_dir.name:
        return None, "ASSET_PATH_UNSAFE"
    lexical = base / relative
    # 已删除的文件视为无可清理目标，保证重复运行幂等且不误报失败。
    if not lexical.exists():
        return None, None
    if _has_reparse_component(lexical, base):
        return None, "ASSET_PATH_UNSAFE"
    try:
        resolved = lexical.resolve()
    except OSError:
        return None, "ASSET_PATH_UNSAFE"
    if resolved == base or not resolved.is_relative_to(base):
        return None, "ASSET_PATH_UNSAFE"
    if resolved != lexical and _has_reparse_component(resolved, base):
        return None, "ASSET_PATH_UNSAFE"
    if not resolved.is_file():
        return None, None
    return resolved, None


def _digest_matches(path: Path, asset: Asset) -> bool:
    expected = (asset.sha256 or "").strip().lower()
    if len(expected) != 64:
        return False
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == expected
    except OSError:
        return False


async def _project_dependencies(
    session: AsyncSession, generation: Generation, asset: Asset
) -> bool:
    # 任何明确的共享依赖都保护候选文件：已发布/批准交付、参考图、历史引用、
    # 有效审批与已批准产物载荷。宁可保留，也绝不扩大删除范围。
    if asset.review_status in {"approved", "legacy_unreviewed"}:
        return True
    if asset.kind != "generated":
        return True
    if await session.scalar(select(Delivery.id).where(Delivery.asset_id == asset.id).limit(1)):
        return True
    if await session.scalar(
        select(Generation.id).where(Generation.reference_asset_id == asset.id).limit(1)
    ):
        return True
    if await session.scalar(
        select(Project.id).where(Project.reference_asset_id == asset.id).limit(1)
    ):
        return True
    if await session.scalar(
        select(Asset.id)
        .where(Asset.relative_path == asset.relative_path, Asset.id != asset.id)
        .limit(1)
    ):
        return True
    if await session.scalar(
        select(LegacyItem.id)
        .where(
            or_(
                and_(LegacyItem.kind == "asset", LegacyItem.ref_id == asset.id),
                and_(LegacyItem.kind == "generation", LegacyItem.ref_id == generation.id),
            )
        )
        .limit(1)
    ):
        return True

    jobs = list(
        (
            await session.scalars(
                select(GenerationJob).where(
                    GenerationJob.project_id == asset.project_id,
                    or_(
                        GenerationJob.id == generation.workflow_job_id,
                        GenerationJob.generation_id == generation.id,
                        GenerationJob.result_asset_id == asset.id,
                    )
                )
            )
        ).all()
    )
    if any(job.status in PROTECTED_JOB_STATUSES for job in jobs):
        return True
    if any(job.status in NON_TERMINAL_JOB_STATUSES for job in jobs):
        return True

    # 只有已批准的交付/清单类产物才构成有效依赖；候选、被拒或 unknown 的
    # 审查记录只是数据库审计材料，不得阻止到期候选文件清理。质检报告即使
    # 获批，也只证明报告可展示，不能把失败图片永久保活。
    approved_artifacts = list(
        (
            await session.scalars(
                select(Artifact).where(
                    Artifact.project_id == asset.project_id,
                    Artifact.status == "approved",
                    Artifact.kind != "quality_report",
                )
            )
        ).all()
    )
    if any(_json_contains_id(item.payload_json, asset.id) for item in approved_artifacts):
        return True
    # 未撤销的审批检查记录是有效证据；已撤销审批不授权旧文件继续保留。
    active_approvals = list(
        (
            await session.scalars(
                select(Approval).where(
                    Approval.project_id == asset.project_id,
                    Approval.revoked_at.is_(None),
                )
            )
        ).all()
    )
    return any(_json_contains_id(item.checks_json, asset.id) for item in active_approvals)


async def _load_candidate(
    session: AsyncSession, generation: Generation, policy: RetentionPolicy
) -> _Candidate | None:
    if generation.result_asset_id is None:
        return None
    asset = await session.get(Asset, generation.result_asset_id)
    if asset is None:
        return None
    deadline = _deadline(generation, policy)
    if deadline is None:
        return None
    if asset.kind != "generated":
        return _Candidate(asset, deadline, True, "ASSET_KIND_UNSUPPORTED")
    if asset.project_id != generation.project_id:
        return _Candidate(asset, deadline, True, "ASSET_OWNERSHIP_MISMATCH")
    preserved = await _project_dependencies(session, generation, asset)
    return _Candidate(asset, deadline, preserved)


async def generation_retention(
    app, generation: Generation, *, now: datetime | None = None
) -> GenerationRetention | None:
    """按 completed_at 派生展示状态；没有终态时间就返回空，绝不猜测。"""
    if generation.completed_at is None or generation.status not in TERMINAL_GENERATION_STATUSES:
        return None
    policy = RetentionPolicy()
    deadline = _deadline(generation, policy)
    if deadline is None:
        return None
    async with read_session(app.state.session_factory) as session:
        candidate = await _load_candidate(session, generation, policy)
    if candidate is None:
        return None
    moment = now or utc_now()
    if candidate.preserved:
        status = "preserved"
    elif moment >= deadline:
        status = "expired"
    else:
        status = "active"
    return GenerationRetention(
        status=status,
        terminal_at=generation.completed_at,
        expires_at=format_timestamp(deadline),
    )


async def asset_is_retention_expired(app, asset: Asset) -> bool:
    """供共享读取门禁使用：到期候选在文件尚未清理前也不可读取。"""
    async with read_session(app.state.session_factory) as session:
        generations = list(
            (
                await session.scalars(
                    select(Generation).where(
                        Generation.result_asset_id == asset.id,
                        Generation.completed_at.is_not(None),
                    )
                )
            ).all()
        )
        now = utc_now()
        for generation in generations:
            candidate = await _load_candidate(session, generation, RetentionPolicy())
            if candidate is None:
                continue
            if not candidate.preserved and now >= candidate.expires_at:
                return True
    return False


async def _record_retention_event(
    app,
    event: str,
    *,
    project_id: str | None,
    error_code: str | None = None,
    duration_ms: int = 0,
) -> None:
    # 固定字段白名单，绝不写入路径、密钥、完整 Base64、供应商原文或堆栈。
    safe_detail = {
        "correlation_id": str(uuid.uuid4())[:64],
        "stage": "retention",
        "error_code": error_code[:_MAX_TEXT] if error_code else None,
        "duration_ms": int(duration_ms),
    }
    try:
        now = utc_now()
        async with write_transaction(app.state.session_factory) as session:
            session.add(
                DiagnosticEvent(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    event=event[:64],
                    detail_json=json.dumps(safe_detail, ensure_ascii=False, sort_keys=True),
                    created_at=format_timestamp(now),
                )
            )
            await session.flush()
    except Exception:
        # 审计失败不能反向扩大删除范围，也不能阻断其他候选的安全清理。
        return


def _failure(
    report: RetentionReport,
    generation: Generation,
    stage: str,
    code: str,
    *,
    object_id: str | None = None,
) -> None:
    report.failures.append(
        RetentionFailure(
            object_kind="generation",
            object_id=object_id or generation.id,
            stage=stage,
            error_code=code,
        )
    )


def _unlink_candidate(path: Path) -> None:
    path.unlink()


async def _clean_candidate(
    app,
    generation: Generation,
    *,
    policy: RetentionPolicy,
    now: datetime,
    report: RetentionReport,
    started: float,
) -> None:
    deadline = _deadline(generation, policy)
    if deadline is None:
        _failure(report, generation, "candidate", "RETENTION_TERMINAL_TIME_MISSING")
        await _record_retention_event(
            app,
            "retention_cleanup_skipped",
            project_id=generation.project_id,
            error_code="RETENTION_TERMINAL_TIME_MISSING",
            duration_ms=_duration_ms(started),
        )
        return
    if now < deadline:
        return
    async with read_session(app.state.session_factory) as session:
        candidate = await _load_candidate(session, generation, policy)
    if candidate is None:
        return
    if candidate.error_code is not None:
        _failure(
            report, generation, "candidate", candidate.error_code, object_id=candidate.asset.id
        )
        await _record_retention_event(
            app,
            "retention_cleanup_failed",
            project_id=generation.project_id,
            error_code=candidate.error_code,
            duration_ms=_duration_ms(started),
        )
        return
    if candidate.preserved:
        return
    asset = candidate.asset
    path, path_error = _safe_candidate_path(app.state.settings, asset)
    if path_error is not None:
        _failure(report, generation, "path", path_error, object_id=asset.id)
        await _record_retention_event(
            app,
            "retention_cleanup_failed",
            project_id=generation.project_id,
            error_code=path_error,
            duration_ms=_duration_ms(started),
        )
        return
    if path is None:
        # 文件已不存在：重复清理的幂等分支，不伪造删除成功。
        return
    if asset.mime_type not in {"image/png", "image/jpeg"} or not _digest_matches(path, asset):
        _failure(report, generation, "digest", "DIGEST_MISMATCH", object_id=asset.id)
        await _record_retention_event(
            app,
            "retention_cleanup_failed",
            project_id=generation.project_id,
            error_code="DIGEST_MISMATCH",
            duration_ms=_duration_ms(started),
        )
        return
    try:
        info = inspect_image(await asyncio.to_thread(path.read_bytes))
    except Exception:
        _failure(report, generation, "image", "ASSET_IMAGE_INVALID", object_id=asset.id)
        await _record_retention_event(
            app,
            "retention_cleanup_failed",
            project_id=generation.project_id,
            error_code="ASSET_IMAGE_INVALID",
            duration_ms=_duration_ms(started),
        )
        return
    if (info.width, info.height) != (asset.width, asset.height):
        _failure(report, generation, "image", "ASSET_DIMENSION_MISMATCH", object_id=asset.id)
        await _record_retention_event(
            app,
            "retention_cleanup_failed",
            project_id=generation.project_id,
            error_code="ASSET_DIMENSION_MISMATCH",
            duration_ms=_duration_ms(started),
        )
        return
    try:
        await asyncio.to_thread(_unlink_candidate, path)
    except OSError:
        _failure(report, generation, "file", "FILE_DELETE_FAILED", object_id=asset.id)
        await _record_retention_event(
            app,
            "retention_cleanup_failed",
            project_id=generation.project_id,
            error_code="FILE_DELETE_FAILED",
            duration_ms=_duration_ms(started),
        )
        return
    async with write_transaction(app.state.session_factory) as session:
        asset_row = await session.get(Asset, asset.id)
        if asset_row is not None:
            asset_row.review_note = RETENTION_EXPIRED_NOTE
            await session.flush()
    report.deleted_asset_ids.add(asset.id)
    await _record_retention_event(
        app,
        "retention_candidate_cleaned",
        project_id=generation.project_id,
        error_code=None,
        duration_ms=_duration_ms(started),
    )


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


async def _clean_diagnostics(
    app,
    *,
    policy: RetentionPolicy,
    now: datetime,
    report: RetentionReport,
    started: float,
) -> None:
    async with read_session(app.state.session_factory) as session:
        events = list((await session.scalars(select(DiagnosticEvent))).all())
    for event in events:
        if event.event in PROTECTED_DIAGNOSTIC_EVENTS:
            continue
        created = _parse_timestamp(event.created_at)
        if created is None or now - created < policy.diagnostic_ttl:
            continue
        try:
            async with write_transaction(app.state.session_factory) as session:
                row = await session.get(DiagnosticEvent, event.id)
                if row is None:
                    continue
                await session.delete(row)
                await session.flush()
        except Exception:
            code = "DIAGNOSTIC_DELETE_FAILED"
            report.failures.append(
                RetentionFailure(
                    object_kind="diagnostic",
                    object_id=event.id,
                    stage="diagnostic",
                    error_code=code,
                )
            )
            await _record_retention_event(
                app,
                "retention_cleanup_failed",
                project_id=event.project_id,
                error_code=code,
                duration_ms=_duration_ms(started),
            )
            continue
        report.deleted_diagnostic_ids.add(event.id)


async def _clean_audits(
    app,
    *,
    policy: RetentionPolicy,
    now: datetime,
    report: RetentionReport,
    started: float,
) -> None:
    """Security audit records expire after the fixed 90-day retention window.

    The audit table is append-only from the product surface; this local retention
    pass is the only place that removes rows. A failure keeps the row and is
    reported without widening the deletion scope.
    """
    async with read_session(app.state.session_factory) as session:
        events = list((await session.scalars(select(AuditEvent))).all())
    for event in events:
        created = _parse_timestamp(event.occurred_at)
        if created is None or now - created < policy.audit_ttl:
            continue
        try:
            async with write_transaction(app.state.session_factory) as session:
                row = await session.get(AuditEvent, event.id)
                if row is None:
                    continue
                await session.delete(row)
                await session.flush()
        except Exception:
            code = "AUDIT_DELETE_FAILED"
            report.failures.append(
                RetentionFailure(
                    object_kind="audit",
                    object_id=event.id,
                    stage="audit",
                    error_code=code,
                )
            )
            await _record_retention_event(
                app,
                "retention_cleanup_failed",
                project_id=None,
                error_code=code,
                duration_ms=_duration_ms(started),
            )
            continue
        report.deleted_audit_ids.add(event.id)


async def run_retention_cleanup(
    app,
    *,
    now: datetime | None = None,
    policy: RetentionPolicy | None = None,
) -> RetentionReport:
    resolved_policy = policy or RetentionPolicy()
    moment = now or utc_now()
    started = time.monotonic()
    report = RetentionReport()
    async with read_session(app.state.session_factory) as session:
        generations = list(
            (
                await session.scalars(
                    select(Generation).where(
                        Generation.status.in_(tuple(TERMINAL_GENERATION_STATUSES))
                    )
                )
            ).all()
        )
        report.scanned_diagnostics = int(
            await session.scalar(select(func.count()).select_from(DiagnosticEvent)) or 0
        )
        report.scanned_audits = int(
            await session.scalar(select(func.count()).select_from(AuditEvent)) or 0
        )
    report.scanned_generations = len(generations)
    for generation in generations:
        await _clean_candidate(
            app,
            generation,
            policy=resolved_policy,
            now=moment,
            report=report,
            started=started,
        )
    await _clean_diagnostics(
        app,
        policy=resolved_policy,
        now=moment,
        report=report,
        started=started,
    )
    await _clean_audits(
        app,
        policy=resolved_policy,
        now=moment,
        report=report,
        started=started,
    )
    return report


__all__ = [
    "AUDIT_TTL",
    "CANDIDATE_TTL",
    "DIAGNOSTIC_TTL",
    "GenerationRetention",
    "RETENTION_EXPIRED_NOTE",
    "RetentionFailure",
    "RetentionPolicy",
    "RetentionReport",
    "asset_is_retention_expired",
    "generation_retention",
    "run_retention_cleanup",
]

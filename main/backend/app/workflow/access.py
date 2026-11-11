from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import Asset, Generation
from app.workflow import templates
from app.workflow.contracts import digest_payload
from app.workflow.tables import (
    Approval,
    Artifact,
    Delivery,
    GenerationJob,
    LegacyItem,
    WorkflowRun,
)


@dataclass(frozen=True)
class AccessDecision:
    allow: bool
    reason_code: str | None = None


@dataclass(frozen=True)
class ProjectAccessPolicy:
    """项目级旧产物访问策略。

    旧产物是否可读只取决于数据库中的审查事实，绝不根据 generation.status、
    project.confirmed_revision 或前端状态推断。每个对外出口都复用同一策略。
    """

    project_id: str
    legacy_message_ids: frozenset[str] = frozenset()
    legacy_generation_ids: frozenset[str] = frozenset()
    legacy_asset_ids: frozenset[str] = frozenset()
    has_legacy_items: bool = False
    has_workflow_run: bool = False

    @property
    def notice(self) -> str:
        return templates.render("LEGACY_UNREVIEWED")

    @property
    def project_output_hidden(self) -> bool:
        # 旧项目中的需求卡/提示词没有独立补审记录，只要存在任何旧产物就整体隐藏。
        return self.has_legacy_items

    @property
    def old_entries_paused(self) -> bool:
        # 旧写入口尚未接入 v2 门禁：有旧产物或已启动 v2 流程时明确暂停。
        return self.has_legacy_items or self.has_workflow_run

    def hides_message(self, message_id: str) -> bool:
        return message_id in self.legacy_message_ids

    def hides_generation(self, generation_id: str) -> bool:
        return generation_id in self.legacy_generation_ids

    def hides_asset(self, asset_id: str | None) -> bool:
        return bool(asset_id) and asset_id in self.legacy_asset_ids


def _checks(approval: Approval) -> dict[str, object]:
    try:
        parsed = json.loads(approval.checks_json)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _matches_asset(
    session: AsyncSession,
    asset: Asset,
    approval: Approval,
    checks: dict[str, object],
) -> bool:
    """审批记录必须与资产身份和摘要同时绑定，缺一不可。"""
    if not asset.sha256:
        # 缺少摘要时不得回退到 v1 的“本地文件存在即可读”。
        return False
    checks_asset_id = checks.get("asset_id")
    checks_digest = checks.get("asset_digest")
    # 生成图审批必须显式记录摘要；checks 为空或只有 asset_id 一律拒绝。
    if not isinstance(checks_digest, str) or not checks_digest:
        return False
    if checks_digest != asset.sha256:
        return False
    if checks_asset_id is not None and checks_asset_id != asset.id:
        return False
    # 最直接可信的绑定：审批 checks 同时记录资产 id 与摘要。
    if checks_asset_id == asset.id:
        return True
    # 兼容以 artifact 载荷记录的审批；artifact 身份、状态、摘要和载荷缺一不可。
    if not approval.artifact_id:
        return False
    artifact = await session.get(Artifact, approval.artifact_id)
    if artifact is None or artifact.id != approval.artifact_id:
        return False
    if artifact.status != "approved":
        return False
    if not approval.artifact_digest or artifact.digest != approval.artifact_digest:
        return False
    try:
        payload = json.loads(artifact.payload_json)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("asset_id") != asset.id:
        return False
    return payload.get("asset_digest") == asset.sha256


async def asset_approval_matches(session: AsyncSession, asset: Asset, approval: Approval) -> bool:
    """供交付阶段复用同一套资产/审批绑定校验。"""
    return await _matches_asset(session, asset, approval, _checks(approval))


async def load_project_access_policies(
    session: AsyncSession, project_ids: list[str]
) -> dict[str, ProjectAccessPolicy]:
    """一次读取多个项目的旧产物策略，列表页也不绕过门禁。"""
    unique_ids = sorted(set(project_ids))
    if not unique_ids:
        return {}
    items = (
        await session.scalars(select(LegacyItem).where(LegacyItem.project_id.in_(unique_ids)))
    ).all()
    # 即使历史数据缺少 legacy_items 行，只要资产本身被标记为未审查也必须隐藏。
    legacy_assets = (
        await session.scalars(
            select(Asset).where(
                Asset.project_id.in_(unique_ids),
                Asset.review_status == "legacy_unreviewed",
            )
        )
    ).all()
    run_project_ids = set(
        (
            await session.scalars(
                select(WorkflowRun.project_id)
                .where(WorkflowRun.project_id.in_(unique_ids))
                .distinct()
            )
        ).all()
    )

    message_ids: dict[str, set[str]] = {project_id: set() for project_id in unique_ids}
    generation_ids: dict[str, set[str]] = {project_id: set() for project_id in unique_ids}
    asset_ids: dict[str, set[str]] = {project_id: set() for project_id in unique_ids}
    approved_generation_ids: set[str] = set()
    item_counts: dict[str, int] = {project_id: 0 for project_id in unique_ids}
    for item in items:
        if item.status != "legacy_unreviewed":
            # 已补审的历史项不再触发默认隔离，否则项目会永久无法恢复。
            if item.kind == "generation":
                approved_generation_ids.add(item.ref_id)
            continue
        item_counts[item.project_id] = item_counts.get(item.project_id, 0) + 1
        if item.kind == "message":
            message_ids.setdefault(item.project_id, set()).add(item.ref_id)
        elif item.kind == "generation":
            generation_ids.setdefault(item.project_id, set()).add(item.ref_id)
        elif item.kind == "asset":
            asset_ids.setdefault(item.project_id, set()).add(item.ref_id)
    for asset in legacy_assets:
        asset_ids.setdefault(asset.project_id, set()).add(asset.id)
        item_counts[asset.project_id] = item_counts.get(asset.project_id, 0) + 1

    # 旧生成若指向未审查资产，即使缺少 generation 类型的 legacy_items 行也隐藏。
    if asset_ids:
        legacy_asset_ids = {asset_id for values in asset_ids.values() for asset_id in values}
        generations = (
            await session.scalars(
                select(Generation).where(
                    Generation.project_id.in_(unique_ids),
                    Generation.result_asset_id.in_(legacy_asset_ids),
                )
            )
        ).all()
        for generation in generations:
            if generation.id in approved_generation_ids:
                continue
            generation_ids.setdefault(generation.project_id, set()).add(generation.id)

    return {
        project_id: ProjectAccessPolicy(
            project_id=project_id,
            legacy_message_ids=frozenset(message_ids.get(project_id, set())),
            legacy_generation_ids=frozenset(generation_ids.get(project_id, set())),
            legacy_asset_ids=frozenset(asset_ids.get(project_id, set())),
            has_legacy_items=item_counts.get(project_id, 0) > 0,
            has_workflow_run=project_id in run_project_ids,
        )
        for project_id in unique_ids
    }


async def load_project_access_policy(session: AsyncSession, project_id: str) -> ProjectAccessPolicy:
    policies = await load_project_access_policies(session, [project_id])
    return policies[project_id]


def legacy_flow_disabled() -> ApiError:
    """Stable tombstone for write entrances that no longer have a flow."""
    return ApiError(409, "LEGACY_FLOW_DISABLED", templates.render("LEGACY_FLOW_DISABLED"))


async def ensure_legacy_entry_available(
    session: AsyncSession, project_id: str
) -> ProjectAccessPolicy:
    """旧 v1 写入口统一守卫；前端禁用不能替代后端授权。"""
    policy = await load_project_access_policy(session, project_id)
    if policy.old_entries_paused:
        raise legacy_flow_disabled()
    return policy


async def _active_approval(
    session: AsyncSession, project_id: str, approval_type: str
) -> Approval | None:
    return await session.scalar(
        select(Approval)
        .where(
            Approval.project_id == project_id,
            Approval.approval_type == approval_type,
            Approval.revoked_at.is_(None),
        )
        .order_by(Approval.created_at.desc())
        .limit(1)
    )


def _artifact_payload(artifact: Artifact) -> dict[str, object]:
    try:
        payload = json.loads(artifact.payload_json)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _active_approvals(
    session: AsyncSession, project_id: str, approval_type: str
) -> list[Approval]:
    return list(
        (
            await session.scalars(
                select(Approval)
                .where(
                    Approval.project_id == project_id,
                    Approval.approval_type == approval_type,
                    Approval.revoked_at.is_(None),
                )
                .order_by(Approval.created_at.desc())
            )
        ).all()
    )


async def _v2_access(
    session: AsyncSession,
    settings: Settings,
    asset: Asset,
    job: GenerationJob,
    *,
    download: bool,
) -> AccessDecision:
    """v2 交付资产的统一 G08 门禁。

    只要资产被 GenerationJob 引用，就必须同时满足：图片批准、交付清单批准、
    原确认需求快照、规则版本、已发布交付记录、真实文件摘要与尺寸。任一条件
    不满足都固定拒绝，绝不回退到“文件存在即可读”。
    """
    # 延迟导入，避免与项目服务形成循环依赖。
    from app.services.assets import inspect_image, resolve_asset_file

    if (
        job.result_asset_id != asset.id
        or job.project_id != asset.project_id
        or job.status != "published"
    ):
        return AccessDecision(False, "ASSET_UNAVAILABLE")

    delivery = await session.scalar(
        select(Delivery)
        .where(
            Delivery.project_id == asset.project_id,
            Delivery.generation_job_id == job.id,
            Delivery.asset_id == asset.id,
            Delivery.status == "published",
        )
        .order_by(Delivery.published_at.desc())
        .limit(1)
    )
    if delivery is None:
        return AccessDecision(False, "NOT_PUBLISHED")

    image_approval = None
    for candidate in await _active_approvals(session, asset.project_id, "image"):
        if candidate.approved_by != "output_reviewer":
            continue
        if candidate.rules_version != "2.0.0" or candidate.prompt_version != "2.0.0":
            continue
        if candidate.requirement_snapshot_digest != job.confirmed_snapshot_digest:
            continue
        if await _matches_asset(session, asset, candidate, _checks(candidate)):
            image_approval = candidate
            break
    if image_approval is None:
        return AccessDecision(False, "DIGEST_MISMATCH")

    manifest_approval = None
    for candidate in await _active_approvals(session, asset.project_id, "delivery_manifest"):
        if (
            candidate.approved_by != "output_reviewer"
            or candidate.rules_version != "2.0.0"
            or candidate.prompt_version != "2.0.0"
            or candidate.requirement_snapshot_digest != job.confirmed_snapshot_digest
            or candidate.artifact_id != delivery.manifest_artifact_id
        ):
            continue
        manifest_approval = candidate
        break
    if manifest_approval is None:
        return AccessDecision(False, "APPROVAL_MISSING")
    if not manifest_approval.artifact_id:
        return AccessDecision(False, "APPROVAL_MISSING")
    manifest = await session.get(Artifact, manifest_approval.artifact_id)
    if (
        manifest is None
        or manifest.project_id != asset.project_id
        or manifest.status != "approved"
        or not manifest_approval.artifact_digest
        or manifest.digest != manifest_approval.artifact_digest
    ):
        return AccessDecision(False, "DIGEST_MISMATCH")
    payload = _artifact_payload(manifest)
    if digest_payload(payload) != manifest.digest:
        return AccessDecision(False, "DIGEST_MISMATCH")
    if payload.get("asset_id") != asset.id:
        return AccessDecision(False, "DIGEST_MISMATCH")
    run = await session.get(WorkflowRun, job.run_id)
    if (
        run is None
        or run.project_id != asset.project_id
        or payload.get("requirement_revision") != run.requirement_revision
    ):
        return AccessDecision(False, "REQUIREMENT_CHANGED")

    if (
        not delivery.asset_digest
        or delivery.asset_digest != asset.sha256
        or not delivery.filename
        or delivery.manifest_artifact_id != manifest.id
    ):
        return AccessDecision(False, "DIGEST_MISMATCH")
    if download and not delivery.filename:
        return AccessDecision(False, "NOT_PUBLISHED")

    path = resolve_asset_file(settings, asset)
    if path is None:
        return AccessDecision(False, "ASSET_UNAVAILABLE")
    data = await asyncio.to_thread(path.read_bytes)
    if not asset.sha256 or hashlib.sha256(data).hexdigest() != asset.sha256:
        return AccessDecision(False, "DIGEST_MISMATCH")
    try:
        info = inspect_image(data)
    except Exception:
        return AccessDecision(False, "ASSET_UNAVAILABLE")
    if info.width != asset.width or info.height != asset.height:
        return AccessDecision(False, "DIGEST_MISMATCH")
    return AccessDecision(True)


def _asset_path(settings: Settings, asset: Asset):
    base = settings.resolved_data_dir().resolve()
    candidate = (base / asset.relative_path).resolve()
    if not candidate.is_relative_to(base):
        return None
    return candidate if candidate.is_file() else None


async def can_access_asset(
    session: AsyncSession,
    settings: Settings,
    asset: Asset,
    *,
    download: bool = False,
) -> AccessDecision:
    """Single gate for every asset read or download.

    The workflow may evolve, but no route may bypass this fail-closed check.
    """
    if asset.review_status == "legacy_unreviewed":
        return AccessDecision(False, "LEGACY_UNREVIEWED")
    if asset.review_status == "rejected":
        return AccessDecision(False, "ASSET_REJECTED")

    if asset.kind == "reference":
        # Reference bytes are user input, not generated content, but they still
        # leave the private data root. Verify the file resolves inside that root,
        # matches the recorded digest when one exists, and keeps the recorded
        # dimensions. Legacy references without a digest stay readable, but a
        # tampered file of another size is rejected.
        path = _asset_path(settings, asset)
        if path is None:
            return AccessDecision(False, "ASSET_FILE_MISSING")
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError:
            return AccessDecision(False, "ASSET_FILE_MISSING")
        if asset.sha256 and hashlib.sha256(data).hexdigest() != asset.sha256:
            return AccessDecision(False, "DIGEST_MISMATCH")
        from app.services.assets import inspect_image

        try:
            info = inspect_image(data)
        except Exception:
            return AccessDecision(False, "ASSET_UNAVAILABLE")
        if info.width != asset.width or info.height != asset.height:
            return AccessDecision(False, "DIGEST_MISMATCH")
        return AccessDecision(True)

    if asset.kind != "generated":
        return AccessDecision(False, "ASSET_UNAVAILABLE")
    if asset.review_status != "approved":
        return AccessDecision(False, "ASSET_NOT_REVIEWED")

    path = _asset_path(settings, asset)
    if path is None:
        return AccessDecision(False, "ASSET_FILE_MISSING")
    try:
        actual_digest = await asyncio.to_thread(
            lambda: hashlib.sha256(path.read_bytes()).hexdigest()
        )
    except OSError:
        return AccessDecision(False, "ASSET_FILE_MISSING")
    if not asset.sha256 or actual_digest != asset.sha256:
        return AccessDecision(False, "DIGEST_MISMATCH")

    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.project_id == asset.project_id,
            Approval.approval_type == "image",
            Approval.revoked_at.is_(None),
        )
        .order_by(Approval.created_at.desc())
        .limit(1)
    )
    if approval is None or approval.approved_by not in {"user", "output_reviewer"}:
        return AccessDecision(False, "APPROVAL_MISSING")
    checks = _checks(approval)
    job = await session.scalar(
        select(GenerationJob)
        .where(GenerationJob.result_asset_id == asset.id)
        .order_by(GenerationJob.created_at.desc())
        .limit(1)
    )
    if job is not None:
        return await _v2_access(session, settings, asset, job, download=download)

    if "legacy_item_id" in checks and approval.rules_version != "2.0.0":
        return AccessDecision(False, "RULES_MISMATCH")
    if not await _matches_asset(session, asset, approval, checks):
        return AccessDecision(False, "DIGEST_MISMATCH")

    if download:
        delivery = await session.scalar(
            select(Delivery)
            .where(
                Delivery.project_id == asset.project_id,
                Delivery.asset_id == asset.id,
                Delivery.status == "published",
            )
            .order_by(Delivery.published_at.desc())
            .limit(1)
        )
        if delivery is None:
            return AccessDecision(False, "NOT_PUBLISHED")
        if not delivery.asset_digest or delivery.asset_digest != asset.sha256:
            return AccessDecision(False, "DIGEST_MISMATCH")
        if not delivery.filename:
            return AccessDecision(False, "NOT_PUBLISHED")
    return AccessDecision(True)


__all__ = [
    "AccessDecision",
    "ProjectAccessPolicy",
    "can_access_asset",
    "asset_approval_matches",
    "ensure_legacy_entry_available",
    "load_project_access_policies",
    "load_project_access_policy",
]

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import ApiError
from app.models import Asset, Generation
from app.workflow import templates
from app.workflow.tables import Approval, Artifact, Delivery, LegacyItem, WorkflowRun


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

async def load_project_access_policies(
    session: AsyncSession, project_ids: list[str]
) -> dict[str, ProjectAccessPolicy]:
    """一次读取多个项目的旧产物策略，列表页也不绕过门禁。"""
    unique_ids = sorted(set(project_ids))
    if not unique_ids:
        return {}
    items = (
        await session.scalars(
            select(LegacyItem).where(LegacyItem.project_id.in_(unique_ids))
        )
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
    item_counts: dict[str, int] = {project_id: 0 for project_id in unique_ids}
    for item in items:
        if item.status != "legacy_unreviewed":
            # 已补审的历史项不再触发默认隔离，否则项目会永久无法恢复。
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


async def load_project_access_policy(
    session: AsyncSession, project_id: str
) -> ProjectAccessPolicy:
    policies = await load_project_access_policies(session, [project_id])
    return policies[project_id]


async def ensure_legacy_entry_available(
    session: AsyncSession, project_id: str
) -> ProjectAccessPolicy:
    """旧 v1 写入口统一守卫；前端禁用不能替代后端授权。"""
    policy = await load_project_access_policy(session, project_id)
    if policy.old_entries_paused:
        raise ApiError(409, "LEGACY_FLOW_DISABLED", templates.render("LEGACY_FLOW_DISABLED"))
    return policy


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
    del settings  # reserved for path roots and future policy inputs
    if asset.review_status == "legacy_unreviewed":
        return AccessDecision(False, "LEGACY_UNREVIEWED")
    if asset.review_status == "rejected":
        return AccessDecision(False, "ASSET_REJECTED")

    if asset.kind == "reference":
        return AccessDecision(True)

    if asset.kind != "generated":
        return AccessDecision(False, "ASSET_UNAVAILABLE")
    if asset.review_status != "approved":
        return AccessDecision(False, "ASSET_NOT_REVIEWED")

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
    if approval is None:
        return AccessDecision(False, "APPROVAL_MISSING")
    checks = _checks(approval)
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
    "ensure_legacy_entry_available",
    "load_project_access_policies",
    "load_project_access_policy",
]

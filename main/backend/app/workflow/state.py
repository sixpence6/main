from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.acceptance import latest_intent, usage_snapshot
from app.adapters.registry import AdapterRegistry
from app.auth.principal import Principal
from app.config import Settings
from app.db import ProjectLocks, read_session, write_transaction
from app.errors import conflict, not_found_project
from app.models import Asset, Project
from app.ownership import revalidate_principal
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
from app.workflow.access import can_access_asset, load_project_access_policy
from app.workflow.contracts import digest_payload, validate_data
from app.workflow.projection import build_task_state
from app.workflow.schemas_workflow import (
    ApprovalView,
    ArtifactView,
    DeliveryView,
    GenerationIntentView,
    GenerationJobView,
    GenerationUsageView,
    IssueView,
    LegacyItemView,
    WorkflowVersionView,
    WorkflowView,
)
from app.workflow.snapshot import (
    SnapshotCompatibilityError,
    execution_snapshot_from_checks,
)
from app.workflow.tables import (
    Approval,
    Artifact,
    ArtifactReview,
    Delivery,
    DiagnosticEvent,
    GenerationJob,
    LegacyItem,
    RevisionBudget,
    RoleCall,
    WorkflowRun,
)

BRIEF_FIELDS = (
    "product_description",
    "purpose",
    "style",
    "composition",
    "text_content",
)
APPROVAL_ORDER = (
    "requirements_card",
    "generation_prompt",
    "quality_report",
    "image",
    "delivery_manifest",
)
APPROVAL_APPROVERS = {
    "requirements_card": "user",
    "generation_prompt": "output_reviewer",
    "quality_report": "output_reviewer",
    "image": "output_reviewer",
    "delivery_manifest": "output_reviewer",
}


@dataclass
class WorkflowContext:
    app: Any
    project: Project
    run: WorkflowRun | None
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    registry: AdapterRegistry
    http: Any
    locks: ProjectLocks
    principal: Principal | None = None

    @property
    def project_id(self) -> str:
        return self.project.id

    @property
    def run_id(self) -> str:
        if self.run is None:
            raise conflict("WORKFLOW_NOT_STARTED", "流程尚未开始")
        return self.run.id

    @property
    def revision(self) -> int:
        return self.project.revision


async def load_context(
    app: Any, project_id: str, *, principal: Principal | None = None
) -> WorkflowContext:
    async with read_session(app.state.session_factory) as session:
        if principal is None:
            project = await session.get(Project, project_id)
        else:
            await revalidate_principal(session, principal)
            project = await session.scalar(
                select(Project).where(
                    Project.id == project_id,
                    Project.owner_account_id == principal.account_id,
                )
            )
        if project is None:
            raise not_found_project()
        run = await session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project_id)
            .order_by(WorkflowRun.sequence_no.desc())
            .limit(1)
        )
    return WorkflowContext(
        app=app,
        project=project,
        run=run,
        session_factory=app.state.session_factory,
        settings=app.state.settings,
        registry=app.state.registry,
        http=app.state.http,
        locks=app.state.locks,
        principal=principal,
    )


async def reload_context(ctx: WorkflowContext) -> WorkflowContext:
    fresh = await load_context(ctx.app, ctx.project_id, principal=ctx.principal)
    fresh.locks = ctx.locks
    return fresh


async def create_run(ctx: WorkflowContext, stage: str = "S01") -> WorkflowRun:
    if ctx.run is not None:
        return ctx.run
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        highest = await session.scalar(
            select(func.max(WorkflowRun.sequence_no)).where(
                WorkflowRun.project_id == ctx.project_id
            )
        )
        run = WorkflowRun(
            id=str(uuid4()),
            project_id=ctx.project_id,
            sequence_no=(highest or 0) + 1,
            stage=stage,
            status="active",
            requirement_revision=ctx.project.revision,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        await session.flush()
    ctx.run = run
    return run


async def start_new_run(
    ctx: WorkflowContext, stage: str = "S03", *, requirement_revision: int | None = None
) -> WorkflowRun:
    """Create the next immutable workflow run for a new version.

    The previous run is never mutated: its artifacts, approvals and delivery
    bytes stay attached to their own snapshot. This is what makes history
    access independent from the current requirement revision.
    """
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        highest = await session.scalar(
            select(func.max(WorkflowRun.sequence_no)).where(
                WorkflowRun.project_id == ctx.project_id
            )
        )
        run = WorkflowRun(
            id=str(uuid4()),
            project_id=ctx.project_id,
            sequence_no=(highest or 0) + 1,
            stage=stage,
            status="active",
            requirement_revision=(
                requirement_revision
                if requirement_revision is not None
                else ctx.project.revision
            ),
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        await session.flush()
    ctx.run = run
    return run


async def _run_for_update(ctx: WorkflowContext, session: AsyncSession) -> WorkflowRun:
    if ctx.run is None:
        raise conflict("WORKFLOW_NOT_STARTED", "流程尚未开始")
    run = await session.get(WorkflowRun, ctx.run.id)
    if run is None:
        raise conflict("WORKFLOW_NOT_STARTED", "流程尚未开始")
    return run


async def set_stage(
    ctx: WorkflowContext,
    stage: str,
    status: str = "active",
    *,
    issue_code: str | None = None,
    message: str | None = None,
    template_id: str | None = None,
    params: dict[str, str] | None = None,
) -> WorkflowRun:
    if stage not in templates.STAGE_LABELS:
        raise ValueError(f"unknown stage {stage}")
    if status not in {"active", "needs_input", "blocked", "completed", "failed"}:
        raise ValueError(f"unknown status {status}")
    resolved_params = dict(params or {})
    if template_id is not None:
        if message is None:
            message = templates.render(template_id, **resolved_params)
        if issue_code is None:
            issue_code = template_id
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        run = await _run_for_update(ctx, session)
        run.stage = stage
        run.status = status
        run.issue_code = issue_code
        run.issue_message = message
        run.template_id = template_id
        run.template_params_json = (
            json.dumps(resolved_params, ensure_ascii=False, sort_keys=True)
            if resolved_params
            else None
        )
        run.updated_at = now
        await session.flush()
    ctx.run = run
    return run


async def set_run_status(ctx: WorkflowContext, status: str) -> WorkflowRun:
    if ctx.run is None:
        raise conflict("WORKFLOW_NOT_STARTED", "流程尚未开始")
    return await set_stage(ctx, ctx.run.stage, status)


async def set_intent(ctx: WorkflowContext, route: str, action: str | None) -> None:
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        run = await _run_for_update(ctx, session)
        run.intent_route = route
        run.intent_action = action
        run.updated_at = now
        await session.flush()
    ctx.run = run


@dataclass(frozen=True)
class RevisionReservation:
    """A persisted claim on one artifact revision, taken before any model call."""

    revision_no: int
    used: int
    max_allowed: int


async def reserve_artifact_revision(
    ctx: WorkflowContext, kind: str
) -> RevisionReservation:
    """Persist one candidate-budget deduction before the candidate exists.

    The deduction survives refresh, process restart and technical failure. It
    is deliberately not tied to a successful model response, so a caller can
    never exceed the hard three-candidate budget by retrying after failures.
    """
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        run = await _run_for_update(ctx, session)
        budget = await session.get(RevisionBudget, (run.id, kind))
        if budget is None:
            budget = RevisionBudget(
                run_id=run.id, artifact_kind=kind, used=0, max_allowed=3
            )
            session.add(budget)
            await session.flush()
        if budget.used >= budget.max_allowed:
            raise conflict(
                "REVISION_BUDGET_EXHAUSTED",
                "该产物已达到最多三次候选的上限，请保留当前结果或重新开始流程",
            )
        budget.used += 1
        run.updated_at = now
        await session.flush()
        return RevisionReservation(
            revision_no=budget.used,
            used=budget.used,
            max_allowed=budget.max_allowed,
        )


async def claim_recovery(
    ctx: WorkflowContext, operation_id: str, *, stage: str
) -> None:
    """Atomically claim a failed/blocked run for one explicit recovery call.

    The claim is persisted on the workflow run so two application instances
    using independent SQLite connections cannot replay the same checkpoint at
    the same time. It is released in a finally block by the caller; startup
    repair clears claims left behind by a hard process crash.
    """
    if not operation_id:
        raise conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        run = await _run_for_update(ctx, session)
        if run.status not in {"blocked", "failed"} or run.stage != stage:
            raise conflict(
                "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
            )
        if run.recovery_claim_id is not None:
            raise conflict(
                "PROJECT_BUSY", "项目正在处理其他写操作，请稍后重试"
            )
        run.recovery_claim_id = operation_id
        run.recovery_claimed_at = now
        run.updated_at = now
        await session.flush()


async def release_recovery(
    ctx: WorkflowContext, operation_id: str
) -> None:
    """Release a recovery claim owned by the given operation."""
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        run = await _run_for_update(ctx, session)
        if run.recovery_claim_id != operation_id:
            return
        run.recovery_claim_id = None
        run.recovery_claimed_at = None
        run.updated_at = now
        await session.flush()


async def start_artifact(
    ctx: WorkflowContext,
    kind: str,
    payload: Any,
    status: str = "candidate",
    *,
    revision_no: int | None = None,
) -> Artifact:
    """Persist one artifact revision, consuming or reusing one hard-budget slot.

    Callers that reserve with reserve_artifact_revision pass the returned
    revision number here so the same attempt is not counted twice.
    """
    raw = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
    model = validate_data(kind, raw)
    data = model.model_dump(mode="json")
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        run = await _run_for_update(ctx, session)
        budget = await session.get(RevisionBudget, (run.id, kind))
        if budget is None:
            budget = RevisionBudget(
                run_id=run.id, artifact_kind=kind, used=0, max_allowed=3
            )
            session.add(budget)
            await session.flush()
        if revision_no is None:
            if budget.used >= budget.max_allowed:
                raise conflict(
                    "REVISION_BUDGET_EXHAUSTED",
                    "该产物已达到最多三次候选的上限，请保留当前结果或重新开始流程",
                )
            budget.used += 1
            assigned = budget.used
        else:
            if revision_no < 1 or revision_no > budget.used:
                raise conflict("REVISION_BUDGET_EXHAUSTED", "候选预算扣占已失效，请重新开始流程")
            assigned = revision_no
        artifact = Artifact(
            id=str(uuid4()),
            run_id=run.id,
            project_id=ctx.project_id,
            kind=kind,
            revision_no=assigned,
            status=status,
            payload_json=json.dumps(data, ensure_ascii=False, sort_keys=True),
            digest=digest_payload(data),
            created_at=now,
        )
        session.add(artifact)
        run.updated_at = now
        await session.flush()
    return artifact


# Compatibility alias for callers that use the architecture's start_artifact wording.
add_artifact = start_artifact


async def latest_project_artifact(
    ctx: WorkflowContext, kind: str, *, status: str | None = "approved"
) -> Artifact | None:
    """Read the newest artifact of a kind across every run in the project."""
    async with read_session(ctx.session_factory) as session:
        statement = select(Artifact).where(
            Artifact.project_id == ctx.project_id, Artifact.kind == kind
        )
        if status is not None:
            statement = statement.where(Artifact.status == status)
        return await session.scalar(
            statement.order_by(Artifact.created_at.desc()).limit(1)
        )


async def latest_artifact(
    ctx: WorkflowContext, kind: str, *, status: str | None = None
) -> Artifact | None:
    async with read_session(ctx.session_factory) as session:
        statement = select(Artifact).where(
            Artifact.run_id == ctx.run_id, Artifact.kind == kind
        )
        if status is not None:
            statement = statement.where(Artifact.status == status)
        return await session.scalar(
            statement.order_by(Artifact.revision_no.desc()).limit(1)
        )


async def approved_artifact(ctx: WorkflowContext, kind: str) -> Artifact | None:
    return await latest_artifact(ctx, kind, status="approved")


async def require_approved_artifact(ctx: WorkflowContext, kind: str) -> Artifact:
    artifact = await approved_artifact(ctx, kind)
    if artifact is None:
        raise conflict("ARTIFACT_NOT_APPROVED", "所需产物尚未通过审查")
    return artifact


async def set_artifact_status(
    ctx: WorkflowContext, artifact_id: str, status: str
) -> Artifact:
    if status not in {"candidate", "approved", "rejected", "superseded"}:
        raise ValueError(f"unknown artifact status {status}")
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        artifact = await session.get(Artifact, artifact_id)
        if artifact is None or artifact.project_id != ctx.project_id:
            raise conflict("ARTIFACT_NOT_FOUND", "产物不存在")
        artifact.status = status
        run = await session.get(WorkflowRun, artifact.run_id)
        if run is not None:
            run.updated_at = now
        await session.flush()
    return artifact


async def add_review(
    ctx: WorkflowContext,
    artifact: Artifact,
    *,
    reviewer_role: str,
    decision: str,
    checks: list[dict[str, Any]] | dict[str, Any],
    target_kind: str = "artifact",
    raw: dict[str, Any] | None = None,
) -> ArtifactReview:
    if decision not in {"pass", "revise", "reject", "unknown"}:
        raise ValueError(f"unknown review decision {decision}")
    now = format_timestamp(utc_now())
    checks_value = checks if isinstance(checks, dict) else {"checks": checks}
    async with write_transaction(ctx.session_factory) as session:
        review = ArtifactReview(
            id=str(uuid4()),
            artifact_id=artifact.id,
            project_id=ctx.project_id,
            reviewer_role=reviewer_role,
            target_kind=target_kind,
            decision=decision,
            checks_json=json.dumps(checks_value, ensure_ascii=False, sort_keys=True),
            raw_json=(
                json.dumps(raw, ensure_ascii=False, sort_keys=True)
                if raw is not None
                else None
            ),
            created_at=now,
        )
        stored = await session.get(Artifact, artifact.id)
        if stored is not None:
            if decision == "pass":
                stored.status = "approved"
            elif decision == "reject":
                stored.status = "rejected"
        session.add(review)
        await session.flush()
    return review


async def latest_model_fingerprint(ctx: WorkflowContext, role: str | None = None) -> str:
    async with read_session(ctx.session_factory) as session:
        statement = select(RoleCall).where(RoleCall.run_id == ctx.run_id)
        if role is not None:
            statement = statement.where(RoleCall.role == role)
        call = await session.scalar(
            statement.order_by(RoleCall.started_at.desc()).limit(1)
        )
    return (call.model_fingerprint if call is not None else None) or "unavailable"


def _brief_snapshot(project: Project) -> dict[str, str]:
    return {name: getattr(project, name) or "" for name in BRIEF_FIELDS}


async def requirement_snapshot_digest(ctx: WorkflowContext) -> str:
    artifact = await approved_artifact(ctx, "final_brief")
    if artifact is None:
        artifact = await approved_artifact(ctx, "brief_draft")
    if artifact is not None:
        return artifact.digest
    return digest_payload(_brief_snapshot(ctx.project))


async def referenced_material_digest(ctx: WorkflowContext) -> str:
    artifact = await approved_artifact(ctx, "reference_report")
    return artifact.digest if artifact is not None else digest_payload({"reference": "none"})


async def latest_approval(
    ctx: WorkflowContext, approval_type: str, *, active_only: bool = True
) -> Approval | None:
    async with read_session(ctx.session_factory) as session:
        statement = select(Approval).where(
            Approval.project_id == ctx.project_id,
            Approval.approval_type == approval_type,
        )
        if active_only:
            statement = statement.where(Approval.revoked_at.is_(None))
        return await session.scalar(statement.order_by(Approval.created_at.desc()).limit(1))


async def add_approval(
    ctx: WorkflowContext,
    *,
    approval_type: str,
    artifact: Artifact,
    approved_by: str,
    checks: dict[str, Any] | None = None,
    requirement_digest: str | None = None,
    material_digest: str | None = None,
    model_fingerprint: str | None = None,
    target_digest: str | None = None,
    session: AsyncSession | None = None,
    revoke_existing: bool = True,
) -> Approval:
    """写入不可变审批记录。

    target_digest 绑定审批目标摘要。图片批准的目标是候选图字节，而其外键
    仍指向质检报告产物；这样报告批准与图片批准不会共用同一摘要。
    session 允许调用方把审批与发布写入同一事务，避免已发布但无审批。
    """
    if approval_type not in APPROVAL_ORDER:
        raise ValueError(f"unknown approval type {approval_type}")
    expected_approver = APPROVAL_APPROVERS[approval_type]
    if approved_by != expected_approver:
        raise ValueError(
            f"approval type {approval_type} must be issued by {expected_approver}"
        )
    if artifact.status != "approved":
        raise conflict("ARTIFACT_NOT_APPROVED", "产物尚未通过审查，无法批准")
    req_digest = requirement_digest or await requirement_snapshot_digest(ctx)
    mat_digest = material_digest or await referenced_material_digest(ctx)
    fingerprint = model_fingerprint or await latest_model_fingerprint(ctx)
    row_digest = target_digest or artifact.digest
    now = format_timestamp(utc_now())

    async def _persist(db: AsyncSession) -> Approval:
        existing = await db.scalar(
            select(Approval)
            .where(
                Approval.project_id == ctx.project_id,
                Approval.approval_type == approval_type,
                Approval.revoked_at.is_(None),
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        if (
            existing is not None
            and existing.artifact_id == artifact.id
            and existing.artifact_digest == row_digest
            and existing.requirement_snapshot_digest == req_digest
        ):
            # A digest-equal rewrite is still a new artifact revision. An old
            # approval must never be inherited by a re-reviewed text candidate.
            return existing
        # Requirements and prompt approvals are superseded by a new version.
        # Image/delivery approvals are version-scoped evidence: a new published
        # version must not revoke approvals that keep older assets readable.
        if (
            revoke_existing
            and existing is not None
            and approval_type in {"requirements_card", "generation_prompt"}
        ):
            existing.revoked_at = now
            existing.revocation_reason = "superseded"
        approval = Approval(
            id=str(uuid4()),
            project_id=ctx.project_id,
            artifact_id=artifact.id,
            approval_type=approval_type,
            artifact_digest=row_digest,
            approved_by=approved_by,
            requirement_snapshot_digest=req_digest,
            referenced_material_digest=mat_digest,
            prompt_version="2.0.0",
            rules_version="2.0.0",
            model_fingerprint=fingerprint[:200],
            checks_json=json.dumps(checks or {}, ensure_ascii=False, sort_keys=True),
            created_at=now,
        )
        db.add(approval)
        await db.flush()
        return approval

    if session is not None:
        return await _persist(session)
    async with write_transaction(ctx.session_factory) as opened:
        return await _persist(opened)


async def revoke_active_approval_types(
    ctx: WorkflowContext, approval_types: tuple[str, ...], reason: str
) -> int:
    """Revoke only the named active approvals; published history is untouched."""
    now = format_timestamp(utc_now())
    revoked = 0
    async with write_transaction(ctx.session_factory) as session:
        approvals = (
            await session.scalars(
                select(Approval).where(
                    Approval.project_id == ctx.project_id,
                    Approval.approval_type.in_(approval_types),
                    Approval.revoked_at.is_(None),
                )
            )
        ).all()
        for approval in approvals:
            approval.revoked_at = now
            approval.revocation_reason = reason[:64]
            revoked += 1
        await session.flush()
    return revoked


async def _approval_protected_by_published_delivery(
    session: AsyncSession, approval: Approval
) -> bool:
    if approval.approval_type not in {"image", "delivery_manifest"}:
        return False
    if approval.approval_type == "delivery_manifest":
        if not approval.artifact_id:
            return False
        delivery = await session.scalar(
            select(Delivery).where(
                Delivery.project_id == approval.project_id,
                Delivery.manifest_artifact_id == approval.artifact_id,
                Delivery.status == "published",
            )
        )
        return delivery is not None
    checks = _json_dict(approval.checks_json)
    asset_id = checks.get("asset_id")
    if not isinstance(asset_id, str) or not asset_id:
        return False
    delivery = await session.scalar(
        select(Delivery).where(
            Delivery.project_id == approval.project_id,
            Delivery.asset_id == asset_id,
            Delivery.status == "published",
        )
    )
    return delivery is not None


async def revoke_downstream_approvals(
    ctx: WorkflowContext,
    reason: str,
    *,
    keep_types: tuple[str, ...] = (),
) -> int:
    now = format_timestamp(utc_now())
    revoked = 0
    async with write_transaction(ctx.session_factory) as session:
        approvals = (
            await session.scalars(
                select(Approval).where(
                    Approval.project_id == ctx.project_id,
                    Approval.revoked_at.is_(None),
                )
            )
        ).all()
        for approval in approvals:
            if approval.approval_type in keep_types:
                continue
            if await _approval_protected_by_published_delivery(session, approval):
                continue
            approval.revoked_at = now
            approval.revocation_reason = reason[:64]
            revoked += 1
        if revoked:
            deliveries = (
                await session.scalars(
                    select(Delivery).where(
                        Delivery.project_id == ctx.project_id,
                        Delivery.status == "approved",
                    )
                )
            ).all()
            for delivery in deliveries:
                delivery.status = "revoked"
        await session.flush()
    return revoked


async def invalidate_requirements(ctx: WorkflowContext, reason: str) -> int:
    """Change the requirement revision once and revoke every dependent approval."""
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        run = await _run_for_update(ctx, session)
        run.requirement_revision += 1
        run.updated_at = now
        await session.flush()
    ctx.run = run
    return await revoke_downstream_approvals(ctx, reason)


async def record_diagnostic(
    ctx: WorkflowContext, event: str, detail: dict[str, Any] | None = None
) -> None:
    async with write_transaction(ctx.session_factory) as session:
        session.add(
            DiagnosticEvent(
                id=str(uuid4()),
                project_id=ctx.project_id,
                event=event[:64],
                detail_json=json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
                created_at=format_timestamp(utc_now()),
            )
        )
        await session.flush()


def _legacy_views(items: list[LegacyItem]) -> list[LegacyItemView]:
    return [
        LegacyItemView(
            id=item.id,
            kind=item.kind,
            ref_id=item.ref_id,
            status=item.status,
            note=item.note,
            reviewed_at=item.reviewed_at,
            created_at=item.created_at,
        )
        for item in items
    ]


def _safe_approval_checks(raw: str | None) -> dict[str, Any]:
    confirmation = _json_dict(raw).get("confirmation")
    return {"confirmation": confirmation} if isinstance(confirmation, dict) else {}


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _version_execution_snapshot(
    session: AsyncSession, project_id: str, brief_artifact: Artifact | None
) -> dict[str, Any] | None:
    """Load the confirmation snapshot that belongs to this version's brief.

    History must never resolve the snapshot through current project columns or
    the currently active approval. Matching the published run's approved
    final_brief artifact keeps the original confirmation immutable.
    """
    if brief_artifact is None:
        return None
    approval = await session.scalar(
        select(Approval)
        .where(
            Approval.project_id == project_id,
            Approval.approval_type == "requirements_card",
            Approval.artifact_id == brief_artifact.id,
            Approval.artifact_digest == brief_artifact.digest,
        )
        .order_by(Approval.created_at.asc())
        .limit(1)
    )
    if approval is None:
        return None
    try:
        return execution_snapshot_from_checks(_json_dict(approval.checks_json))
    except SnapshotCompatibilityError as exc:
        raise conflict(
            "SNAPSHOT_INCOMPATIBLE", "历史确认快照版本不兼容，不能静默重解释"
        ) from exc


async def _build_versions(
    session: AsyncSession, project_id: str, settings: Settings
) -> list[WorkflowVersionView]:
    """Build the immutable published-version history for the workspace.

    Only a published delivery whose asset is approved and whose digest still
    matches may appear. Candidate or approved-but-unpublished assets never
    enter the history list, and no artifact payload is exposed unless that
    artifact is approved.
    """
    jobs = (
        await session.scalars(
            select(GenerationJob)
            .where(GenerationJob.project_id == project_id)
            .order_by(GenerationJob.created_at.asc())
        )
    ).all()
    versions: list[WorkflowVersionView] = []
    for job in jobs:
        delivery = await session.scalar(
            select(Delivery)
            .where(
                Delivery.project_id == project_id,
                Delivery.generation_job_id == job.id,
                Delivery.status == "published",
            )
            .order_by(Delivery.published_at.desc())
            .limit(1)
        )
        if delivery is None:
            continue
        run = await session.get(WorkflowRun, job.run_id)
        if run is None:
            continue
        asset = await session.get(Asset, job.result_asset_id) if job.result_asset_id else None
        # Resolve the exact inputs this generation job used. A run can gain
        # later approved revisions (text repair, review renewal); history must
        # keep showing the job's own brief and prompt, not the newest ones.
        brief_artifact = await session.scalar(
            select(Artifact)
            .where(
                Artifact.run_id == job.run_id,
                Artifact.kind == "final_brief",
                Artifact.status == "approved",
                Artifact.digest == job.confirmed_snapshot_digest,
            )
            .order_by(Artifact.revision_no.desc())
            .limit(1)
        )
        prompt_artifact = (
            await session.get(Artifact, job.prompt_artifact_id)
            if job.prompt_artifact_id
            else None
        )
        if (
            prompt_artifact is not None
            and (
                prompt_artifact.run_id != job.run_id
                or prompt_artifact.kind != "prompt_plan"
                or prompt_artifact.status != "approved"
            )
        ):
            prompt_artifact = None
        from app.prompt_preparation import historical_prompt_readable
        if not await historical_prompt_readable(session, job, prompt_artifact):
            prompt_artifact = None
        quality_artifact = await session.scalar(
            select(Artifact)
            .where(
                Artifact.run_id == job.run_id,
                Artifact.kind.in_(("quality_report", "quality_recheck")),
                Artifact.status == "approved",
            )
            .order_by(Artifact.created_at.desc())
            .limit(1)
        )
        execution_snapshot = await _version_execution_snapshot(
            session, project_id, brief_artifact
        )
        can_download = bool(
            asset is not None
            and delivery.filename
            and (await can_access_asset(session, settings, asset, download=True)).allow
        )
        versions.append(
            WorkflowVersionView(
                run_id=job.run_id,
                sequence_no=run.sequence_no,
                requirement_revision=run.requirement_revision,
                status="published",
                generation_job_id=job.id,
                asset_id=job.result_asset_id if can_download else None,
                filename=delivery.filename if can_download else None,
                width=asset.width if can_download and asset is not None else None,
                height=asset.height if can_download and asset is not None else None,
                caption=delivery.caption,
                published_at=delivery.published_at,
                brief=_json_dict(brief_artifact.payload_json) if brief_artifact else None,
                prompt=_json_dict(prompt_artifact.payload_json) if prompt_artifact else None,
                quality=_json_dict(quality_artifact.payload_json) if quality_artifact else None,
                execution_snapshot=execution_snapshot,
                execution_snapshot_digest=(
                    execution_snapshot.get("snapshot_digest")
                    if execution_snapshot is not None
                    else None
                ),
                snapshot_schema_version=(
                    execution_snapshot.get("snapshot_schema_version")
                    if execution_snapshot is not None
                    else None
                ),
                contract_version=(
                    execution_snapshot.get("contract_version")
                    if execution_snapshot is not None
                    else None
                ),
                can_download=can_download,
                created_at=job.created_at,
            )
        )
    return versions


async def build_workflow_view(ctx: WorkflowContext) -> WorkflowView:
    from app.workflow import dispatch

    if ctx.run is None:
        # 尚未启动 v2 流程的项目也必须展示旧产物占位，不能因没有 run 而漏掉。
        async with read_session(ctx.session_factory) as session:
            legacy = list(
                (
                    await session.scalars(
                        select(LegacyItem)
                        .where(LegacyItem.project_id == ctx.project_id)
                        .order_by(LegacyItem.created_at.asc())
                    )
                ).all()
            )
        # A valid reference upload immediately makes S02 available. Until the
        # report is approved the user only sees the original image preview and
        # the fixed stage status; no report payload is exposed.
        initial_action = (
            "analyze_reference" if ctx.project.reference_asset_id else "submit_request"
        )
        actions = [dispatch.action_view(initial_action)]
        return WorkflowView(
            project_id=ctx.project_id,
            status="none",
            project_revision=ctx.project.revision,
            requirement_revision=ctx.project.revision,
            available_actions=actions,
            legacy=_legacy_views(legacy),
            task_state=build_task_state(
                project=ctx.project,
                run=None,
                job=None,
                delivery=None,
                versions=[],
                intent=None,
                active_snapshot=None,
                artifacts=[],
            ),
            updated_at=ctx.project.updated_at,
        )

    async with read_session(ctx.session_factory) as session:
        artifacts = (
            await session.scalars(
                select(Artifact)
                .where(Artifact.run_id == ctx.run.id)
                .order_by(Artifact.created_at.asc(), Artifact.revision_no.asc())
            )
        ).all()
        approvals = (
            await session.scalars(
                select(Approval)
                .where(Approval.project_id == ctx.project_id)
                .order_by(Approval.created_at.asc())
            )
        ).all()
        legacy = (
            await session.scalars(
                select(LegacyItem)
                .where(LegacyItem.project_id == ctx.project_id)
                .order_by(LegacyItem.created_at.asc())
            )
        ).all()
        job = await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.run_id == ctx.run.id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        versions = await _build_versions(session, ctx.project_id, ctx.settings)
        policy = await load_project_access_policy(session, ctx.project_id)
        delivery = None
        if job is not None:
            delivery = await session.scalar(
                select(Delivery)
                .where(Delivery.generation_job_id == job.id)
                .order_by(Delivery.created_at.desc())
                .limit(1)
            )

    from app.prompt_preparation import historical_prompt_readable

    history_prompt_id = None
    if job is not None:
        async with read_session(ctx.session_factory) as session:
            prompt = (
                await session.get(Artifact, job.prompt_artifact_id)
                if job.prompt_artifact_id else None
            )
            if await historical_prompt_readable(session, job, prompt):
                history_prompt_id = job.prompt_artifact_id

    usage = None
    intent_view = None
    if ctx.project.owner_account_id is not None:
        intent = await latest_intent(
            ctx.session_factory,
            project_id=ctx.project_id,
            run_id=ctx.run.id,
        )
        if intent is not None:
            intent_view = GenerationIntentView(
                id=intent.id,
                request_id=intent.request_id,
                status=intent.status,
                artifact_digest=intent.artifact_digest,
                requirement_revision=intent.requirement_revision,
                created_at=intent.created_at,
            )
        usage = GenerationUsageView(
            **await usage_snapshot(
                ctx.session_factory,
                settings=ctx.settings,
                account_id=ctx.project.owner_account_id,
                project_id=ctx.project_id,
            )
        )

    issue = None
    if ctx.run.template_id or ctx.run.issue_message:
        params = (
            _json_dict(ctx.run.template_params_json) if ctx.run.template_id else {}
        )
        # Templated issues always use the reviewed copy. The legacy issue_message
        # field may contain reviewer prose and must stay out of the safe view.
        if ctx.run.template_id:
            message = templates.render(ctx.run.template_id, **params)
        elif ctx.run.stage == "S03" and ctx.run.status == "needs_input":
            # Legacy requirement questions have no template id.
            message = ctx.run.issue_message
        else:
            message = None
        issue = IssueView(
            code=ctx.run.issue_code or ctx.run.template_id or "ISSUE",
            message=message or "",
            template_id=ctx.run.template_id,
            params={key: str(value) for key, value in params.items()},
        )

    from app.prompt_preparation import safe_preview

    preview = await safe_preview(ctx)
    artifact_views: list[ArtifactView] = []
    for artifact in artifacts:
        payload = (
            _json_dict(artifact.payload_json)
            if artifact.status == "approved"
            and (
                artifact.kind != "prompt_plan"
                or preview is not None
                and preview["request"]["prompt_artifact_id"] == artifact.id
                or history_prompt_id == artifact.id
            )
            else None
        )
        artifact_views.append(
            ArtifactView(
                id=artifact.id,
                kind=artifact.kind,
                revision_no=artifact.revision_no,
                status=artifact.status,
                digest=artifact.digest,
                payload=payload,
                created_at=artifact.created_at,
            )
        )

    approval_views = [
        ApprovalView(
            id=approval.id,
            approval_type=approval.approval_type,
            artifact_id=approval.artifact_id,
            artifact_digest=approval.artifact_digest,
            approved_by=approval.approved_by,
            requirement_snapshot_digest=approval.requirement_snapshot_digest,
            referenced_material_digest=approval.referenced_material_digest,
            prompt_version=approval.prompt_version,
            rules_version=approval.rules_version,
            model_fingerprint=approval.model_fingerprint,
            checks=_safe_approval_checks(approval.checks_json),
            created_at=approval.created_at,
            revoked_at=approval.revoked_at,
            revocation_reason=approval.revocation_reason,
        )
        for approval in approvals
    ]

    active_execution_snapshot = None
    for approval in approvals:
        if (
            approval.approval_type != "requirements_card"
            or approval.revoked_at is not None
        ):
            continue
        try:
            candidate_snapshot = execution_snapshot_from_checks(
                _json_dict(approval.checks_json)
            )
        except SnapshotCompatibilityError as exc:
            raise conflict(
                "SNAPSHOT_INCOMPATIBLE", "确认快照版本不兼容，不能静默重解释"
            ) from exc
        if candidate_snapshot is not None:
            active_execution_snapshot = candidate_snapshot

    actions = dispatch.available_actions(ctx)
    if preview is not None and preview["can_confirm"]:
        # Keep the explicit repair and abandonment choices beside final
        # confirmation; refreshing or reopening the page must not remove them.
        actions = [dispatch.action_view("confirm_prompt")] + [
            view for view in actions if view.id != "confirm_prompt"
        ]
    task_state = build_task_state(
        project=ctx.project,
        run=ctx.run,
        job=job,
        delivery=delivery,
        versions=versions,
        intent=intent_view,
        active_snapshot=active_execution_snapshot,
        artifacts=artifact_views,
    )

    return WorkflowView(
        prompt_preview=preview,
        project_id=ctx.project_id,
        run_id=ctx.run.id,
        stage=ctx.run.stage,
        status=ctx.run.status,
        project_revision=ctx.project.revision,
        requirement_revision=ctx.run.requirement_revision,
        intent_route=ctx.run.intent_route,
        intent_action=ctx.run.intent_action,
        issue=issue,
        artifacts=artifact_views,
        approvals=approval_views,
        available_actions=actions,
        legacy=_legacy_views(list(legacy)),
        generation_job=(
            GenerationJobView(
                id=job.id,
                status=job.status,
                request_id=job.request_id,
                submit_attempted=job.submit_attempted,
                result_asset_id=(
                    None if policy.hides_asset(job.result_asset_id) else job.result_asset_id
                ),
                error_code=job.error_code,
                error_message=job.error_message,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
            if job is not None
            else None
        ),
        versions=versions,
        execution_snapshot=active_execution_snapshot,
        execution_snapshot_digest=(
            active_execution_snapshot.get("snapshot_digest")
            if active_execution_snapshot is not None
            else None
        ),
        usage=usage,
        intent=intent_view,
        task_state=task_state,
        delivery=(
            DeliveryView(
                id=delivery.id,
                status=delivery.status,
                asset_id=delivery.asset_id,
                filename=delivery.filename,
                caption=delivery.caption,
                published_at=delivery.published_at,
                created_at=delivery.created_at,
            )
            if delivery is not None and not policy.hides_asset(delivery.asset_id)
            else None
        ),
        updated_at=ctx.run.updated_at,
    )


__all__ = [
    "WorkflowContext",
    "load_context",
    "reload_context",
    "create_run",
    "set_stage",
    "set_run_status",
    "set_intent",
    "reserve_artifact_revision",
    "RevisionReservation",
    "start_artifact",
    "add_artifact",
    "latest_artifact",
    "approved_artifact",
    "require_approved_artifact",
    "set_artifact_status",
    "add_review",
    "latest_model_fingerprint",
    "requirement_snapshot_digest",
    "referenced_material_digest",
    "latest_approval",
    "add_approval",
    "revoke_downstream_approvals",
    "invalidate_requirements",
    "record_diagnostic",
    "build_workflow_view",
]

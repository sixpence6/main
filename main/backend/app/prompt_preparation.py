"""Frozen, reviewed image requests. Preparing is not authorization to execute.

No final-execution command exists in T01: every new image attempt fails closed.
The existing acceptance ledger continues to own count and occupancy.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from app.acceptance import GenerationIntent
from app.auth.audit import write_audit
from app.auth.models import Account
from app.db import read_session, write_transaction
from app.errors import ApiError, conflict
from app.models import Asset, Base, Project
from app.ownership import revalidate_principal
from app.services.assets import resolve_asset_file
from app.services.generations import service_fingerprint
from app.timeutil import format_timestamp, utc_now
from app.workflow.access import can_access_asset
from app.workflow.contracts import digest_payload
from app.workflow.snapshot import execution_snapshot_from_checks
from app.workflow.tables import Approval, Artifact, ArtifactReview, GenerationJob, WorkflowRun

CONTRACT_VERSION = "prompt-request-1.0.0"


class PreparedPrompt(Base):
    __tablename__ = "prepared_prompts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("generation_intents.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.id"), nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("intent_id", "artifact_id", name="uq_prepared_prompt_candidate"),
        CheckConstraint(
            "status IN ('awaiting_confirmation','invalidated')", name="ck_prepared_prompt_status"
        ),
    )


def final_confirmation_required() -> ApiError:
    return conflict("FINAL_CONFIRMATION_REQUIRED", "尚未获得此提示词的最终执行授权，不能生成图片。")


def require_image_execution() -> None:
    """T01 has no final authorization issuer. Never infer one from old approvals."""
    raise final_confirmation_required()


def compose_request_text(positive: str, negative: str) -> str:
    # The adapters have one text input, not a native negative-prompt parameter.
    # Freeze the exact combined text before independent review, never on click.
    return positive if not negative else f"{positive}\n\n禁止内容（Negative prompt）:\n{negative}"


async def build_request(ctx, payload, final_artifact, *, session=None) -> dict:
    if session is None:
        async with read_session(ctx.session_factory) as opened:
            return await build_request(ctx, payload, final_artifact, session=opened)
    project = await session.get(Project, ctx.project_id)
    run = await session.get(WorkflowRun, ctx.run_id)
    if project is None or run is None or project.owner_account_id is None:
        raise conflict("PROMPT_STALE", "准备上下文不可用。")
    intent = await session.scalar(
        select(GenerationIntent)
        .where(GenerationIntent.project_id == project.id, GenerationIntent.run_id == run.id)
        .order_by(GenerationIntent.created_at.desc())
        .limit(1)
    )
    confirmation = await session.scalar(
        select(Approval)
        .where(
            Approval.project_id == project.id,
            Approval.approval_type == "requirements_card",
            Approval.revoked_at.is_(None),
            Approval.artifact_id == final_artifact.id,
        )
        .order_by(Approval.created_at.desc())
        .limit(1)
    )
    if (
        intent is None
        or intent.status != "accepted"
        or confirmation is None
        or confirmation.approved_by != "user"
        or confirmation.artifact_digest != final_artifact.digest
        or confirmation.requirement_snapshot_digest != final_artifact.digest
        or intent.artifact_digest != final_artifact.digest
        or intent.owner_account_id != project.owner_account_id
        or project.confirmed_revision != project.revision
        or run.requirement_revision != project.revision
        or intent.requirement_revision != run.requirement_revision
        or payload["count"] != 1
        or payload["size"] != intent.image_size
        or payload["size"] != ctx.settings.visual().image_size
        or payload.get("reference_asset_id") != project.reference_asset_id
    ):
        raise conflict("PROMPT_STALE", "准备请求与已确认需求不一致。")
    requirements = json.loads(final_artifact.payload_json)
    if digest_payload(requirements) != final_artifact.digest:
        raise conflict("PROMPT_STALE", "需求快照已变化。")
    dependencies = []
    reference_id = payload.get("reference_asset_id")
    if reference_id:
        asset = await session.get(Asset, reference_id)
        capabilities = ctx.registry.visual_capabilities(ctx.settings.visual().adapter_id or "")
        if (
            asset is None
            or asset.project_id != project.id
            or asset.kind != "reference"
            or not asset.sha256
            or capabilities is None
            or capabilities.reference_image is not True
        ):
            raise conflict("REFERENCE_UNAVAILABLE", "参考图片不可用于准备。")
        if not (await can_access_asset(session, ctx.settings, asset)).allow:
            raise conflict("REFERENCE_UNAVAILABLE", "参考图片内容门禁未通过。")
        report = await session.scalar(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.run_id == run.id,
                Artifact.kind == "reference_report",
                Artifact.status == "approved",
            )
            .order_by(Artifact.revision_no.desc())
            .limit(1)
        )
        if report is None:
            raise conflict("REFERENCE_UNAVAILABLE", "参考图片审查已失效。")
        report_payload = json.loads(report.payload_json)
        evidence = [
            e
            for bucket in ("observations", "texts")
            for item in report_payload.get(bucket, [])
            for e in item.get("evidence_ids", [])
        ]
        if (
            digest_payload(report_payload) != report.digest
            or report_payload.get("reference_usage") != intent.reference_usage
            or not evidence
            or any(e != "reference:" + asset.id for e in evidence)
        ):
            raise conflict("REFERENCE_UNAVAILABLE", "参考图片与审查依赖不一致。")
        reference = resolve_asset_file(ctx.settings, asset)
        if reference is None or hashlib.sha256(reference.read_bytes()).hexdigest() != asset.sha256:
            raise conflict("REFERENCE_UNAVAILABLE", "参考图片内容版本不可用。")
        if (
            capabilities.reference_mime_types
            and asset.mime_type not in capabilities.reference_mime_types
            or capabilities.provider_max_reference_bytes is not None
            and asset.byte_size > capabilities.provider_max_reference_bytes
        ):
            raise conflict("REFERENCE_UNAVAILABLE", "参考图片超出适配器边界。")
        dependencies.append(
            {
                "asset_id": asset.id,
                "content_version": asset.sha256,
                "sha256": asset.sha256,
                "mime_type": asset.mime_type,
                "byte_size": asset.byte_size,
                "usage": intent.reference_usage,
                "report_artifact_id": report.id,
                "report_digest": report.digest,
            }
        )
    visual = ctx.settings.visual()
    return {
        "contract_version": CONTRACT_VERSION,
        "project_id": project.id,
        "run_id": run.id,
        "intent_id": intent.id,
        "owner_account_id": intent.owner_account_id,
        "operator_account_id": intent.operator_account_id,
        "project_revision": project.revision,
        "requirement_revision": run.requirement_revision,
        "requirements": requirements,
        "requirements_artifact_id": final_artifact.id,
        "requirements_digest": final_artifact.digest,
        "requirements_approval_id": confirmation.id,
        "execution_requirements": execution_snapshot_from_checks(
            json.loads(confirmation.checks_json)
        ),
        "positive_prompt": payload["positive_prompt"],
        "negative_prompt": payload["negative_prompt"],
        "prompt": compose_request_text(payload["positive_prompt"], payload["negative_prompt"]),
        "negative_strategy": "combined-text-v1",
        "references": dependencies,
        "output": {"count": 1, "size": payload["size"], "response_format": "b64_json"},
        "service": {
            "adapter_id": visual.adapter_id,
            "model": visual.model,
            "fingerprint": service_fingerprint(visual),
        },
    }


async def approve_and_wait(
    ctx,
    artifact,
    final_artifact,
    payload,
    reviewed_request,
    checks,
    review_output,
    *,
    recovering=False,
) -> None:
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        if ctx.principal is not None:
            await revalidate_principal(session, ctx.principal)
        current = await session.get(Artifact, artifact.id)
        final = await session.get(Artifact, final_artifact.id)
        run = await session.get(WorkflowRun, ctx.run_id)
        project = await session.get(Project, ctx.project_id)
        if (
            current is None
            or final is None
            or current.status != "candidate"
            or current.digest != digest_payload(payload)
            or final.status != "approved"
            or run is None
            or run.stage != "S07"
            or not (
                run.status == "active"
                or recovering
                and run.status in {"failed", "blocked"}
                and run.recovery_claim_id
            )
            or project is None
            or ctx.principal is not None
            and project.owner_account_id != ctx.principal.account_id
        ):
            raise conflict("PROMPT_STALE", "提示词准备上下文已变化。")
        actual = await build_request(ctx, payload, final, session=session)
        if actual != reviewed_request:
            raise conflict("PROMPT_STALE", "请求或参考在审查期间已变化。")
        intent = await session.get(GenerationIntent, actual["intent_id"])
        for account_id in {intent.owner_account_id, intent.operator_account_id} - {None}:
            account = await session.get(Account, account_id)
            if account is None or not account.can_login:
                raise conflict("ACCOUNT_DISABLED", "账号不可用，准备结果已隔离。")
        existing_job = await session.scalar(
            select(GenerationJob.id).where(GenerationJob.intent_id == intent.id)
        )
        if existing_job is not None:
            raise final_confirmation_required()
        current.status = "approved"
        session.add(
            ArtifactReview(
                id=str(uuid4()),
                artifact_id=current.id,
                project_id=ctx.project_id,
                reviewer_role="output_reviewer",
                target_kind="artifact",
                decision="pass",
                checks_json=json.dumps(checks, ensure_ascii=False, sort_keys=True),
                raw_json=json.dumps(review_output.data, ensure_ascii=False),
                created_at=now,
            )
        )
        from app.workflow.state import add_approval

        approval = await add_approval(
            ctx,
            approval_type="generation_prompt",
            artifact=current,
            approved_by="output_reviewer",
            checks=checks,
            model_fingerprint=review_output.model_fingerprint,
            requirement_digest=final.digest,
            session=session,
        )
        actual = {
            **actual,
            "prompt_artifact_id": current.id,
            "prompt_artifact_revision": current.revision_no,
            "prompt_artifact_digest": current.digest,
            "prompt_approval_id": approval.id,
            "ruleset_version": checks["ruleset_version"],
            "approval_rules_version": approval.rules_version,
            "approval_prompt_version": approval.prompt_version,
        }
        old = (
            await session.scalars(
                select(PreparedPrompt).where(
                    PreparedPrompt.intent_id == intent.id,
                    PreparedPrompt.status == "awaiting_confirmation",
                )
            )
        ).all()
        for previous in old:
            previous.status = "invalidated"
        snapshot = PreparedPrompt(
            id=str(uuid4()),
            intent_id=intent.id,
            project_id=ctx.project_id,
            run_id=ctx.run_id,
            artifact_id=current.id,
            approval_id=approval.id,
            request_json=json.dumps(actual, ensure_ascii=False, sort_keys=True),
            request_digest=digest_payload(actual),
            status="awaiting_confirmation",
            created_at=now,
        )
        session.add(snapshot)
        run.status = "needs_input"
        run.issue_code = None
        run.issue_message = None
        run.template_id = None
        run.template_params_json = None
        run.updated_at = now
        write_audit(
            session,
            event_type="prompt.prepared",
            result="success",
            now=utc_now(),
            actor_kind="account",
            actor_account_id=intent.operator_account_id,
            target_kind="prepared_prompt",
            target_id=snapshot.id,
            after={"status": snapshot.status, "request_digest": snapshot.request_digest},
        )
        await session.flush()
    ctx.run = run


async def safe_preview(ctx) -> dict | None:
    if ctx.run is None:
        return None
    async with read_session(ctx.session_factory) as session:
        snapshot = await session.scalar(
            select(PreparedPrompt)
            .where(
                PreparedPrompt.run_id == ctx.run_id,
                PreparedPrompt.status == "awaiting_confirmation",
            )
            .order_by(PreparedPrompt.created_at.desc())
            .limit(1)
        )
        if snapshot is None:
            return None
        artifact = await session.get(Artifact, snapshot.artifact_id)
        approval = await session.get(Approval, snapshot.approval_id)
        try:
            request = json.loads(snapshot.request_json)
            if (
                artifact is None
                or artifact.status != "approved"
                or approval is None
                or approval.revoked_at is not None
                or approval.artifact_id != artifact.id
                or approval.artifact_digest != artifact.digest
                or approval.requirement_snapshot_digest != request.get("requirements_digest")
                or approval.rules_version != request.get("approval_rules_version")
                or approval.prompt_version != request.get("approval_prompt_version")
                or json.loads(approval.checks_json).get("ruleset_version")
                != request.get("ruleset_version")
                or request.get("prompt_artifact_id") != artifact.id
                or request.get("prompt_artifact_digest") != artifact.digest
                or request.get("prompt_artifact_revision") != artifact.revision_no
                or request.get("prompt_approval_id") != approval.id
                or approval.approved_by != "output_reviewer"
                or approval.approval_type != "generation_prompt"
                or approval.project_id != ctx.project_id
                or snapshot.project_id != ctx.project_id
                or digest_payload(request) != snapshot.request_digest
                or artifact.digest != digest_payload(json.loads(artifact.payload_json))
            ):
                return None
            final = await session.get(Artifact, request["requirements_artifact_id"])
            if final is None or final.status != "approved":
                return None
            actual = await build_request(
                ctx, json.loads(artifact.payload_json), final, session=session
            )
            if any(request.get(key) != value for key, value in actual.items()):
                return None
        except (ApiError, ValueError, KeyError, OSError):
            return None
        return {
            "id": snapshot.id,
            "status": snapshot.status,
            "request_digest": snapshot.request_digest,
            "request": request,
            "created_at": snapshot.created_at,
            "can_confirm": False,
        }

"""Frozen, reviewed image requests. Preparing is not authorization to execute.

No final-execution command exists in T01: every new image attempt fails closed.
The existing acceptance ledger continues to own count and occupancy.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.acceptance import GenerationIntent
from app.auth.audit import write_audit
from app.auth.models import Account
from app.confirmation_invalidation import (
    invalidation_reasons,
    raise_for_reasons,
    require_snapshot_binding,
)
from app.corrections import source_bytes
from app.db import read_session, write_transaction
from app.errors import ApiError, conflict
from app.models import Asset, Base, ImageCorrection, Project
from app.ownership import revalidate_principal
from app.prompt_content import extract_prompt, has_prompt_body
from app.services.assets import resolve_asset_file
from app.services.generations import service_fingerprint
from app.timeutil import format_timestamp, utc_now
from app.workflow.access import can_access_asset
from app.workflow.contracts import digest_payload
from app.workflow.snapshot import execution_snapshot_from_checks
from app.workflow.tables import Approval, Artifact, ArtifactReview, GenerationJob, WorkflowRun

CONTRACT_VERSION = "prompt-request-1.1.0"


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
            "status IN ('awaiting_confirmation','authorized','invalidated')",
            name="ck_prepared_prompt_status",
        ),
    )


def final_confirmation_required() -> ApiError:
    return conflict("FINAL_CONFIRMATION_REQUIRED", "尚未获得此提示词的最终执行授权，不能生成图片。")


def require_image_execution() -> None:
    """T01 has no final authorization issuer. Never infer one from old approvals."""
    raise final_confirmation_required()


def configuration_fingerprint(role) -> str:
    """Opaque exact execution configuration, separate from historical job identity.

    The adapter consumes the original URL (including query). Never expose that
    URL or credentials, and never change service_fingerprint's historical rules.
    """
    return digest_payload(
        {
            "version": "exact-image-config-v1",
            "adapter_id": role.adapter_id,
            "base_url": role.base_url,
            "model": role.model,
            "image_size": role.image_size,
            "timeout_seconds": role.timeout_seconds,
            "credential_digest": hashlib.sha256(
                (role.api_key.get_secret_value() if role.api_key else "").encode("utf-8")
            ).hexdigest(),
        }
    )


def compose_request_text(positive: str, negative: str) -> str:
    # The adapters have one text input, not a native negative-prompt parameter.
    # Freeze the exact combined text before independent review, never on click.
    return positive if not negative else f"{positive}\n\n禁止内容（Negative prompt）:\n{negative}"


def require_prompt_body(request: dict) -> None:
    """A frozen heading or fence is never an executable image prompt."""
    if not has_prompt_body(request.get("positive_prompt", "")) or not has_prompt_body(
        request.get("prompt", "")
    ):
        raise conflict("PROMPT_EMPTY", "提示词缺少实际正文，请编辑并保存完整提示词后重新确认。")


async def build_request(
    ctx, payload, final_artifact, *, session=None, authorized=False, check_body=True
) -> dict:
    if session is None:
        async with read_session(ctx.session_factory) as opened:
            return await build_request(
                ctx, payload, final_artifact, session=opened, authorized=authorized,
                check_body=check_body,
            )
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
    correction = await session.scalar(
        select(ImageCorrection).where(ImageCorrection.intent_id == intent.id)
    ) if intent is not None else None
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
        or intent.status
        not in ({"accepted", "executing", "delivered"} if authorized else {"accepted"})
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
        or (correction is None and payload.get("reference_asset_id") != project.reference_asset_id)
    ):
        raise conflict("PROMPT_STALE", "准备请求与已确认需求不一致。")
    requirements = json.loads(final_artifact.payload_json)
    if digest_payload(requirements) != final_artifact.digest:
        raise conflict("PROMPT_STALE", "需求快照已变化。")
    dependencies = []
    correction_payload = None
    reference_id = payload.get("reference_asset_id")
    if correction is not None:
        asset, _ = await source_bytes(session, ctx.settings, correction)
        capabilities = ctx.registry.visual_capabilities(ctx.settings.visual().adapter_id or "")
        if (
            not asset.sha256
            or capabilities is None
            or capabilities.reference_image is not True
        ):
            raise conflict("CORRECTION_NOT_REGISTERED", "修正图来源不可用。")
        if (
            capabilities.reference_mime_types
            and asset.mime_type not in capabilities.reference_mime_types
            or capabilities.provider_max_reference_bytes is not None
            and asset.byte_size > capabilities.provider_max_reference_bytes
        ):
            raise conflict("CORRECTION_NOT_REGISTERED", "修正图来源超出适配器边界。")
        dependencies.append(
            {
                "asset_id": asset.id,
                "content_version": asset.sha256,
                "sha256": asset.sha256,
                "mime_type": asset.mime_type,
                "byte_size": asset.byte_size,
                "usage": "edit_source",
                "report_artifact_id": None,
                "report_digest": None,
            }
        )
        correction_payload = {
            "correction_id": correction.id,
            "source_job_id": correction.source_job_id,
            "source_asset_id": correction.source_asset_id,
            "source_asset_sha256": correction.source_asset_sha256,
            "instruction": correction.instruction,
        }
    elif reference_id:
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
    request = {
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
        **({"correction": correction_payload} if correction_payload is not None else {}),
        "service": {
            "adapter_id": visual.adapter_id,
            "model": visual.model,
            "fingerprint": service_fingerprint(visual),
            "configuration_fingerprint": configuration_fingerprint(visual),
        },
    }

    if check_body:
        require_prompt_body(request)
    return request


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
        from app.corrections import correction_for_intent
        from app.workflow.state import add_approval

        correction = await correction_for_intent(session, intent.id)
        approval = await add_approval(
            ctx,
            approval_type="generation_prompt",
            artifact=current,
            approved_by="output_reviewer",
            checks=checks,
            model_fingerprint=review_output.model_fingerprint,
            requirement_digest=final.digest,
            session=session,
            revoke_existing=correction is None,
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


async def validated_request(
    ctx, session, snapshot, *, authorized=False, check_body=True
) -> dict:
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
            or snapshot.run_id != ctx.run_id
            or artifact.project_id != ctx.project_id
            or artifact.run_id != ctx.run_id
            or digest_payload(request) != snapshot.request_digest
            or artifact.digest != digest_payload(json.loads(artifact.payload_json))
        ):
            raise conflict("PROMPT_STALE", "提示词批准或冻结请求已失效。")
        final = await session.get(Artifact, request["requirements_artifact_id"])
        if (final is None or final.status != "approved"
                or final.project_id != ctx.project_id or final.run_id != ctx.run_id):
            raise conflict("PROMPT_STALE", "提示词批准或冻结请求已失效。")
        require_snapshot_binding(snapshot, request)
        actual = await build_request(
            ctx, json.loads(artifact.payload_json), final, session=session, authorized=authorized,
            check_body=False,
        )
        reasons = invalidation_reasons(request, actual)
        if reasons:
            raise_for_reasons(reasons)
    except (ValueError, KeyError, OSError) as exc:
        raise conflict("PROMPT_STALE", "提示词批准或冻结请求已失效。") from exc
    if check_body:
        require_prompt_body(request)
    return request


async def invalidate_awaiting(
    ctx, *, intent_id: str | None = None, reason: str
) -> int:
    """Mark frozen, not-yet-executed prompt previews stale without deleting facts."""

    async with write_transaction(ctx.session_factory) as session:
        statement = select(PreparedPrompt).where(
            PreparedPrompt.project_id == ctx.project_id,
            PreparedPrompt.run_id == ctx.run_id,
            PreparedPrompt.status == "awaiting_confirmation",
        )
        if intent_id is not None:
            statement = statement.where(PreparedPrompt.intent_id == intent_id)
        snapshots = list((await session.scalars(statement)).all())
        for snapshot in snapshots:
            snapshot.status = "invalidated"
        if snapshots:
            write_audit(
                session,
                event_type="prompt.invalidated",
                result="success",
                now=utc_now(),
                actor_kind="system",
                target_kind="project",
                target_id=ctx.project_id,
                reason_code=reason,
                detail={
                    "intent_id": intent_id,
                    "count": len(snapshots),
                    "snapshot_ids": [snapshot.id for snapshot in snapshots],
                },
            )
        await session.flush()
        return len(snapshots)


async def _preview_source_markdown(ctx, session, snapshot, request) -> str | None:
    """Recover only the approved document that could have produced this candidate."""
    approval = await session.get(Approval, snapshot.approval_id)
    # User edits are authoritative; an old role document must never replace them.
    if json.loads(approval.checks_json).get("user_edited"):
        return None
    candidate = await session.get(Artifact, snapshot.artifact_id)
    previous_time = await session.scalar(
        select(func.max(Artifact.created_at)).where(
            Artifact.project_id == ctx.project_id,
            Artifact.run_id == ctx.run_id,
            Artifact.kind == "prompt_plan",
            Artifact.revision_no < candidate.revision_no,
        )
    )
    statement = select(Artifact).where(
        Artifact.project_id == ctx.project_id,
        Artifact.run_id == ctx.run_id,
        Artifact.kind == "prompt_markdown",
        Artifact.status == "approved",
        Artifact.created_at <= candidate.created_at,
        Artifact.created_at <= snapshot.created_at,
    )
    if previous_time is not None:
        statement = statement.where(Artifact.created_at > previous_time)
    document = await session.scalar(
        statement.order_by(Artifact.created_at.desc(), Artifact.revision_no.desc()).limit(1)
    )
    if document is None:
        return None
    try:
        payload = json.loads(document.payload_json)
        if (
            digest_payload(payload) != document.digest
            or payload.get("format") != "markdown"
            or payload.get("role") != "generation_engineer"
            or payload.get("project_revision") != request["project_revision"]
            or payload.get("reference_asset_id") != ctx.project.reference_asset_id
            or not isinstance(payload.get("content"), str)
        ):
            return None
        for binding in payload.get("upstream_documents", []):
            upstream = await session.get(Artifact, binding["id"])
            latest = None
            if upstream is not None:
                latest = await session.scalar(
                    select(Artifact.id).where(
                        Artifact.project_id == ctx.project_id,
                        Artifact.kind == upstream.kind,
                        Artifact.status == "approved",
                    ).order_by(Artifact.created_at.desc(), Artifact.revision_no.desc()).limit(1)
                )
            if (
                upstream is None
                or latest != upstream.id
                or upstream.project_id != ctx.project_id
                or upstream.status != "approved"
                or upstream.created_at > document.created_at
                or upstream.digest != binding["digest"]
                or upstream.digest != digest_payload(json.loads(upstream.payload_json))
            ):
                return None
        return payload["content"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


async def safe_preview(ctx) -> dict | None:
    if ctx.run is None:
        return None
    async with read_session(ctx.session_factory) as session:
        snapshot = await session.scalar(
            select(PreparedPrompt)
            .where(
                PreparedPrompt.project_id == ctx.project_id,
                PreparedPrompt.run_id == ctx.run_id,
                PreparedPrompt.status == "awaiting_confirmation",
            )
            .order_by(PreparedPrompt.created_at.desc())
            .limit(1)
        )
        if snapshot is None:
            return None
        try:
            request = await validated_request(ctx, session, snapshot, check_body=False)
        except ApiError:
            return None
        from app.final_confirmation import command_context

        context = await command_context(ctx, snapshot, session)
        can_edit = False
        if context is not None and ctx.principal.account_id != request["owner_account_id"]:
            context = None
        if context is not None:
            try:
                await revalidate_principal(session, ctx.principal)
                can_edit = ctx.run.stage == "S07" and ctx.run.status == "needs_input"
            except ApiError:
                context = None
        source = (
            await _preview_source_markdown(ctx, session, snapshot, request)
            if can_edit else None
        )
        warning = None
        valid_body = True
        try:
            require_prompt_body(request)
        except ApiError:
            valid_body = False
            warning = "保存的提示词缺少实际正文，暂不能确认。请补全提示词并保存后重新确认。"
            if source is not None:
                warning = "保存的提示词缺少实际正文，暂不能确认。可从原始 Markdown 恢复正文，编辑保存后重新确认。"
        if source is not None and warning is None:
            warning = extract_prompt(source).warning
        return {
            "id": snapshot.id,
            "status": snapshot.status,
            "request_digest": snapshot.request_digest,
            "request": request,
            "created_at": snapshot.created_at,
            "can_confirm": context is not None and valid_body,
            "can_edit": can_edit,
            "source_markdown": source,
            "parse_warning": warning,
            "command_context": context,
        }


async def historical_prompt_readable(session, job, artifact) -> bool:
    """Validate original content approval, never infer new execution permission."""
    if (
        artifact is None
        or artifact.status != "approved"
        or artifact.kind != "prompt_plan"
        or artifact.id != job.prompt_artifact_id
        or artifact.project_id != job.project_id
        or artifact.run_id != job.run_id
    ):
        return False
    try:
        if digest_payload(json.loads(artifact.payload_json)) != artifact.digest:
            return False
    except (ValueError, TypeError):
        return False
    approvals = (
        await session.scalars(
            select(Approval).where(
                Approval.project_id == job.project_id,
                Approval.artifact_id == artifact.id,
                Approval.approval_type == "generation_prompt",
                Approval.revoked_at.is_(None),
                Approval.artifact_digest == artifact.digest,
                Approval.requirement_snapshot_digest == job.confirmed_snapshot_digest,
                Approval.approved_by == "output_reviewer",
                Approval.rules_version == "2.0.0",
                Approval.prompt_version == "2.0.0",
            )
        )
    ).all()
    if not approvals:
        return False
    original = await session.scalar(
        select(Artifact).where(
            Artifact.project_id == job.project_id,
            Artifact.run_id == job.run_id,
            Artifact.kind == "final_brief",
            Artifact.status == "approved",
            Artifact.digest == job.confirmed_snapshot_digest,
        )
    )
    try:
        return (
            original is not None
            and digest_payload(json.loads(original.payload_json)) == original.digest
        )
    except (ValueError, TypeError):
        return False


async def edit_prepared_prompt(ctx, payload) -> None:
    """Persist the user's exact text as a new waiting snapshot; never submit."""
    from pydantic import BaseModel, ConfigDict, Field, ValidationError

    from app.workflow.contracts import GenerationData
    from app.workflow.state import add_approval

    class Edit(BaseModel):
        model_config = ConfigDict(extra="forbid")
        snapshot_id: str
        snapshot_digest: str
        positive_prompt: str = Field(min_length=1, max_length=12000)
        negative_prompt: str = Field(max_length=6000)

    try:
        edit = Edit.model_validate(payload)
    except ValidationError:
        raise conflict("PROMPT_INVALID", "提示词不能为空，正向最多 12000 字，反向最多 6000 字。") from None
    if not edit.positive_prompt.strip():
        raise conflict("PROMPT_INVALID", "请填写正向提示词。")
    require_prompt_body({"positive_prompt": edit.positive_prompt, "prompt": edit.positive_prompt})
    if ctx.principal is None:
        raise final_confirmation_required()
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        await revalidate_principal(session, ctx.principal)
        project = await session.get(Project, ctx.project_id)
        run = await session.get(WorkflowRun, ctx.run_id)
        old = await session.get(PreparedPrompt, edit.snapshot_id)
        if (project is None or project.owner_account_id != ctx.principal.account_id
                or project.revision != ctx.revision or run is None
                or run.stage != "S07" or run.status != "needs_input"
                or old is None or old.run_id != ctx.run_id
                or old.status != "awaiting_confirmation"
                or old.request_digest != edit.snapshot_digest):
            raise conflict("PROMPT_STALE", "提示词已变化，请刷新后再修改。")
        request = await validated_request(ctx, session, old, check_body=False)
        source = await session.get(Artifact, old.artifact_id)
        plan = json.loads(source.payload_json)
        plan.update(positive_prompt=edit.positive_prompt, negative_prompt=edit.negative_prompt)
        plan = GenerationData.model_validate(plan).model_dump(mode="json")
        if digest_payload(plan) == source.digest:
            return
        revision = (await session.scalar(select(func.max(Artifact.revision_no)).where(
            Artifact.run_id == ctx.run_id, Artifact.kind == "prompt_plan"
        )) or 0) + 1
        artifact = Artifact(
            id=str(uuid4()), run_id=ctx.run_id, project_id=ctx.project_id,
            kind="prompt_plan", revision_no=revision, status="approved",
            payload_json=json.dumps(plan, ensure_ascii=False, sort_keys=True),
            digest=digest_payload(plan), created_at=now,
        )
        session.add(artifact)
        await session.flush()
        checks = {"ruleset_version": request["ruleset_version"],
                  "user_edited": True, "source_snapshot_id": old.id,
                  "operator_account_id": ctx.principal.account_id,
                  "content_review": "skipped"}
        approval = await add_approval(
            ctx, approval_type="generation_prompt", artifact=artifact,
            approved_by="output_reviewer", checks=checks,
            model_fingerprint="user-edit:exact-text", session=session,
            requirement_digest=request["requirements_digest"],
        )
        final = await session.get(Artifact, request["requirements_artifact_id"])
        actual = await build_request(ctx, plan, final, session=session)
        actual.update(prompt_artifact_id=artifact.id,
                      prompt_artifact_revision=artifact.revision_no,
                      prompt_artifact_digest=artifact.digest,
                      prompt_approval_id=approval.id,
                      ruleset_version=request["ruleset_version"],
                      approval_rules_version=approval.rules_version,
                      approval_prompt_version=approval.prompt_version)
        old.status = "invalidated"
        snapshot = PreparedPrompt(
            id=str(uuid4()), intent_id=old.intent_id, project_id=ctx.project_id,
            run_id=ctx.run_id, artifact_id=artifact.id, approval_id=approval.id,
            request_json=json.dumps(actual, ensure_ascii=False, sort_keys=True),
            request_digest=digest_payload(actual), status="awaiting_confirmation", created_at=now,
        )
        session.add(snapshot)
        write_audit(session, event_type="prompt.user_edited", result="success", now=utc_now(),
                    actor_kind="account", actor_account_id=ctx.principal.account_id,
                    target_kind="prepared_prompt", target_id=snapshot.id,
                    after={"source_snapshot_id": old.id, "request_digest": snapshot.request_digest})
        await session.flush()

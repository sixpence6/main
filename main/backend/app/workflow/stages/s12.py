from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import select

from app.adapters.base import OrganizeImage
from app.db import read_session
from app.errors import ApiError, conflict
from app.models import Asset, Message, Project
from app.workflow import templates
from app.workflow.adjudication import RULESET_VERSION, ruleset_from_payload, s07_rules
from app.workflow.roles import (
    RoleOutput,
    _capability_error,
    _capability_ready,
    run_role,
    run_role_request,
)
from app.workflow.stages import s01, s02, s03, s04, s05, s07, s09, s10
from app.workflow.state import (
    WorkflowContext,
    add_approval,
    add_review,
    claim_recovery,
    latest_artifact,
    record_diagnostic,
    release_recovery,
    set_artifact_status,
    set_intent,
    set_stage,
)
from app.workflow.tables import (
    Artifact,
    DiagnosticEvent,
    RevisionBudget,
    RoleCall,
    WorkflowRun,
)

STAGE = "S12"
ACTIONS = [
    "continue_workflow",
    "recover_submission",
    "resume_after_restart",
    "cleanup_expired",
]

_TECHNICAL_RUN_CODES = {"SERVICE_UNAVAILABLE", "REVIEW_UNAVAILABLE", "CAPABILITY_NOT_READY"}
# Reviewer contract defects surface as typed rejections, never as outages.
_REVIEW_PROTOCOL_CODES = {"REVIEW_INVALID", "REVIEW_REJECTED"}
# A malformed reviewer answer may be replayed once the contract is fixed; a
# substantive rejection must not be retried without the user changing the input.
RETRYABLE_REVIEW_ISSUES = frozenset({"REVIEW_UNAVAILABLE", "REVIEW_INVALID"})
_RECOVERABLE_CALL_STATUSES = {"error", "timeout"}
_ARTIFACT_KIND_BY_STAGE = {
    "S02": "reference_report",
    "S03": "brief_draft",
    "S04": "creative_direction",
    "S05": "visual_plan",
    "S07": "prompt_plan",
    "S09": "quality_report",
    "S10": "delivery_manifest",
}


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    if ctx.run is None:
        return []
    if ctx.run.status == "failed":
        return ["continue_workflow"]
    if ctx.run.status == "blocked" and ctx.run.issue_code in RETRYABLE_REVIEW_ISSUES:
        return ["continue_workflow"]
    return []


def _fixed_service() -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE"))


def _fixed_review() -> ApiError:
    return ApiError(503, "REVIEW_UNAVAILABLE", templates.render("REVIEW_UNAVAILABLE"))


async def _stage_error(
    ctx: WorkflowContext, stage: str, error: ApiError, *, fallback: str = "REVIEW_UNAVAILABLE"
) -> str:
    """Persist the typed failure before re-raising the matching public error."""
    if error.code in _REVIEW_PROTOCOL_CODES:
        await set_stage(ctx, stage, "blocked", issue_code=error.code, template_id=error.code)
        return error.code
    if error.code == "CAPABILITY_NOT_READY":
        template_id = "CAPABILITY_NOT_READY"
    elif error.code == "REVIEW_UNAVAILABLE":
        template_id = "REVIEW_UNAVAILABLE"
    else:
        template_id = fallback
    await set_stage(ctx, stage, "failed", issue_code=template_id, template_id=template_id)
    return template_id


async def _review_error(ctx: WorkflowContext, stage: str, error: ApiError) -> None:
    if error.code in _REVIEW_PROTOCOL_CODES:
        await set_stage(ctx, stage, "blocked", issue_code=error.code, template_id=error.code)
        raise error
    await set_stage(
        ctx, stage, "failed", issue_code="REVIEW_UNAVAILABLE", template_id="REVIEW_UNAVAILABLE"
    )
    raise _fixed_review() from None


def _stale() -> ApiError:
    return conflict("STALE_RECOVERY", templates.render("RUN_SUPERSEDED"))


def _unavailable() -> ApiError:
    return conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_recoverable_checkpoint(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> bool:
    status = checkpoint.get("status")
    if status in _RECOVERABLE_CALL_STATUSES or status == "capability":
        return True
    # A review that completed at the provider but failed the stage's semantic
    # validation has no error checkpoint. Its completed request is still the
    # exact, safe unit to replay when the run is parked on REVIEW_UNAVAILABLE.
    return (
        status == "completed"
        and checkpoint.get("phase") == "review"
        and ctx.run is not None
        and ctx.run.status in {"failed", "blocked"}
        and ctx.run.issue_code in RETRYABLE_REVIEW_ISSUES
    )


async def _latest_checkpoint(ctx: WorkflowContext) -> dict[str, Any] | None:
    async with read_session(ctx.session_factory) as session:
        events = list(
            (
                await session.scalars(
                    select(DiagnosticEvent)
                    .where(
                        DiagnosticEvent.project_id == ctx.project_id,
                        DiagnosticEvent.event == "recovery_checkpoint",
                    )
                    .order_by(DiagnosticEvent.created_at.desc())
                    .limit(64)
                )
            ).all()
        )
    for event in events:
        detail = _json_dict(event.detail_json)
        if detail.get("run_id") != ctx.run_id:
            continue
        if not _is_recoverable_checkpoint(ctx, detail):
            continue
        return detail
    return None


async def _is_resumed(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> bool:
    operation_id = checkpoint.get("operation_id")
    if not operation_id:
        return False
    async with read_session(ctx.session_factory) as session:
        events = list(
            (
                await session.scalars(
                    select(DiagnosticEvent)
                    .where(
                        DiagnosticEvent.project_id == ctx.project_id,
                        DiagnosticEvent.event == "recovery_resumed",
                    )
                    .order_by(DiagnosticEvent.created_at.desc())
                    .limit(64)
                )
            ).all()
        )
    return any(
        _json_dict(event.detail_json).get("operation_id") == operation_id for event in events
    )


def _verify_checkpoint(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    if checkpoint.get("stage") != ctx.run.stage:
        raise _stale()
    if checkpoint.get("project_revision") != ctx.project.revision:
        raise _stale()
    if checkpoint.get("requirement_revision") != ctx.run.requirement_revision:
        raise _stale()
    if checkpoint.get("reference_asset_id") != ctx.project.reference_asset_id:
        raise _stale()


async def _ensure_budget(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    if checkpoint.get("phase") == "review":
        return
    kind = _ARTIFACT_KIND_BY_STAGE.get(str(checkpoint.get("stage")))
    if not kind:
        return
    async with read_session(ctx.session_factory) as session:
        budget = await session.get(RevisionBudget, (ctx.run_id, kind))
    if budget is not None and budget.used >= budget.max_allowed:
        raise conflict(
            "REVISION_BUDGET_EXHAUSTED",
            "该产物已达到最多三次候选的上限，请保留当前结果或重新开始流程",
        )


def _ensure_capability(ctx: WorkflowContext, capability: str) -> None:
    llm, reason = _capability_ready(ctx, capability)
    if llm is None:
        raise _capability_error(capability, reason)


async def _vision_images(ctx: WorkflowContext) -> tuple[OrganizeImage, ...]:
    if not ctx.project.reference_asset_id:
        return ()
    async with read_session(ctx.session_factory) as session:
        asset = await session.get(Asset, ctx.project.reference_asset_id)
    if asset is None:
        raise conflict("REFERENCE_NOT_FOUND", templates.render("REFERENCE_INVALID"))
    from app.services.assets import resolve_asset_file

    path = resolve_asset_file(ctx.settings, asset)
    if path is None:
        raise conflict("REFERENCE_NOT_FOUND", templates.render("REFERENCE_INVALID"))
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError:
        raise conflict("REFERENCE_NOT_FOUND", templates.render("REFERENCE_INVALID")) from None
    return (OrganizeImage(data=data, mime_type=asset.mime_type),)


async def _artifact_by_id(ctx: WorkflowContext, artifact_id: str) -> Artifact | None:
    async with read_session(ctx.session_factory) as session:
        artifact = await session.get(Artifact, artifact_id)
    if artifact is None or artifact.project_id != ctx.project_id or artifact.run_id != ctx.run_id:
        return None
    return artifact


async def _latest_user_message(ctx: WorkflowContext) -> Message | None:
    async with read_session(ctx.session_factory) as session:
        return await session.scalar(
            select(Message)
            .where(Message.project_id == ctx.project_id, Message.role == "user")
            .order_by(Message.sequence_no.desc())
            .limit(1)
        )


async def _apply_s01_route(ctx: WorkflowContext, target: dict[str, Any]) -> None:
    route = str(target.get("route"))
    action = str(target.get("action"))
    await set_intent(ctx, route, action)
    message = await _latest_user_message(ctx)
    revision = message.project_revision if message is not None else ctx.project.revision
    if route in {"out_of_scope", "mixed"}:
        await s01._append_assistant(ctx, templates.render("SCOPE_REFUSAL"), revision)
        await set_stage(
            ctx,
            "S01",
            "blocked",
            issue_code="MIXED_SCOPE" if route == "mixed" else "OUT_OF_SCOPE",
            template_id="SCOPE_REFUSAL",
        )
        return
    if route == "unclear":
        await s01._append_assistant(ctx, templates.render("INTENT_UNCLEAR"), revision)
        await set_stage(
            ctx,
            "S01",
            "needs_input",
            issue_code="MISSING_INPUT",
            template_id="INTENT_UNCLEAR",
        )
        return
    if route == "workflow_action":
        await s01._append_assistant(ctx, templates.render("INTENT_UNCLEAR"), revision)
        await set_stage(
            ctx,
            "S01",
            "needs_input",
            issue_code="UNAUTHORIZED_ACTION",
            template_id="INTENT_UNCLEAR",
        )
        return
    next_stage = "S02" if ctx.project.reference_asset_id else "S03"
    await set_stage(ctx, next_stage, "active")


async def _resume_s01_producer(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    message = await _latest_user_message(ctx)
    if message is None:
        raise _unavailable()
    text = message.content
    evidence_id = message.id
    available_actions = (
        ["answer_intent_clarification"]
        if ctx.run is not None and ctx.run.status in {"needs_input", "blocked"}
        else ["submit_request"]
    )
    try:
        intent = await run_role(
            ctx,
            "intent_router",
            "S01",
            {
                "user_text": text,
                "current_stage": ctx.run.stage if ctx.run else "S01",
                "current_status": ctx.run.status if ctx.run else "none",
                "reference_asset_id": ctx.project.reference_asset_id,
                "available_actions": available_actions,
            },
            required_rule_ids=s01._REQUIRED_RULES,
            allowed_evidence_ids=(evidence_id,),
            attempt=int(checkpoint.get("attempt") or 1),
        )
        route, action = s01._validate_intent(intent, evidence_id)
        review = await run_role(
            ctx,
            "output_reviewer",
            "S01",
            {"reviewed_role": "intent_router", "target_kind": "intent_route"},
            review_targets=[
                {
                    "target_id": evidence_id,
                    "target_kind": "intent_route",
                    "user_text": text,
                    "route": route,
                    "action": action,
                }
            ],
            required_rule_ids=s01._REQUIRED_RULES,
            allowed_evidence_ids=(evidence_id,),
            attempt=int(checkpoint.get("attempt") or 1),
        )
        s01._validate_review(review, evidence_id)
    except ApiError as exc:
        await _stage_error(ctx, "S01", exc, fallback="SERVICE_UNAVAILABLE")
        raise
    await _apply_s01_route(ctx, {"route": route, "action": action, "target_id": evidence_id})


async def _resume_s01_review(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    request_raw = str(checkpoint.get("request_json") or "")
    request = _json_dict(request_raw)
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    targets = payload.get("review_targets") or []
    if not targets or not isinstance(targets[0], dict):
        raise _unavailable()
    target = targets[0]
    target_id = str(target.get("target_id") or "")
    try:
        output = await run_role_request(
            ctx,
            "output_reviewer",
            "S01",
            request_raw,
            capability="TEXT",
            attempt=int(checkpoint.get("attempt") or 1),
        )
        s01._validate_review(output, target_id)
    except ApiError as exc:
        await _review_error(ctx, "S01", exc)
    await _apply_s01_route(ctx, target)


async def _resume_s07_review(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    """Replay a persisted S07 reviewer request through the same rule catalog."""
    request_raw, _, payload = _request_parts(checkpoint)
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    targets = payload.get("review_targets") or []
    target = targets[0] if targets and isinstance(targets[0], dict) else {}
    target_id = str(target.get("target_id") or "")
    if task.get("role") != "output_reviewer" or not request_raw or not target_id:
        raise _stale()
    artifact = await _artifact_by_id(ctx, target_id)
    latest = await latest_artifact(ctx, "prompt_plan")
    if artifact is not None and (
        artifact.kind != "prompt_plan"
        or latest is None
        or latest.id != artifact.id
        or artifact.status != "candidate"
    ):
        if artifact.status == "candidate":
            await set_artifact_status(ctx, artifact.id, "superseded")
        artifact = None
    if artifact is None:
        raise _stale()
    input_data = (
        payload.get("input_data") if isinstance(payload.get("input_data"), dict) else {}
    )
    ruleset = ruleset_from_payload(input_data) or s07_rules()
    allowed = set(task.get("allowed_evidence_ids") or []) | {target_id}
    try:
        output = await run_role_request(
            ctx,
            "output_reviewer",
            "S07",
            request_raw,
            capability="TEXT",
            attempt=int(checkpoint.get("attempt") or 1),
        )
    except ApiError:
        await set_stage(
            ctx,
            "S07",
            "failed",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise _fixed_review() from None
    # A contract defect stays a typed protocol rejection: the candidate is not
    # approved, the run is not marked failed and no approval is issued.
    _, decision, checks = s07._validate_review(
        output,
        target_id=target_id,
        allowed_evidence=tuple(allowed),
        ruleset=ruleset,
    )
    if ctx.project.revision != checkpoint.get(
        "project_revision"
    ) or ctx.run.requirement_revision != checkpoint.get("requirement_revision"):
        await set_artifact_status(ctx, artifact.id, "superseded")
        raise _stale()
    review_checks = {"ruleset_version": RULESET_VERSION, "checks": checks}
    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision=decision,
        checks=review_checks,
        raw=output.data,
    )
    if decision == "pass":
        approved = await latest_artifact(ctx, "prompt_plan", status="approved")
        if approved is None:
            raise ApiError(
                409,
                "PROMPT_REVIEW_INVALID",
                "独立审查结果不完整，提示词未放行。",
            )
        await add_approval(
            ctx,
            approval_type="generation_prompt",
            artifact=approved,
            approved_by="output_reviewer",
            checks=review_checks,
            model_fingerprint=output.model_fingerprint,
        )
        await set_stage(ctx, "S08", "active")
    else:
        await set_stage(
            ctx,
            "S07",
            "blocked",
            issue_code=f"REVIEW_{decision.upper()}",
            template_id=f"REVIEW_{decision.upper()}",
        )


async def _resume_s03_review(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    request_raw = str(checkpoint.get("request_json") or "")
    request = _json_dict(request_raw)
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    targets = payload.get("review_targets") or []
    target = targets[0] if targets and isinstance(targets[0], dict) else {}
    target_id = str(target.get("target_id") or "")
    artifact = await _artifact_by_id(ctx, target_id)
    if artifact is not None:
        latest = await latest_artifact(ctx, "brief_draft")
        if latest is None or latest.id != artifact.id or artifact.status != "candidate":
            if artifact.status == "candidate":
                await set_artifact_status(ctx, artifact.id, "superseded")
            raise _stale()
        allowed = set(payload.get("task", {}).get("allowed_evidence_ids") or []) | {target_id}
        try:
            output = await run_role_request(
                ctx,
                "output_reviewer",
                "S03",
                request_raw,
                capability="TEXT",
                attempt=int(checkpoint.get("attempt") or 1),
            )
            decision, checks, _repair = s03._validate_review(
                output, target_id=target_id, allowed_evidence=allowed
            )
        except ApiError as exc:
            await _review_error(ctx, "S03", exc)
        if ctx.project.revision != checkpoint.get(
            "project_revision"
        ) or ctx.run.requirement_revision != checkpoint.get("requirement_revision"):
            await set_artifact_status(ctx, artifact.id, "superseded")
            raise _stale()
        await add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision=decision,
            checks=checks,
            raw=output.data,
        )
        if decision == "pass":
            await set_stage(ctx, "S04", "active")
        else:
            await set_stage(
                ctx,
                "S03",
                "blocked",
                issue_code=f"REVIEW_{decision.upper()}",
                template_id=f"REVIEW_{decision.upper()}",
            )
        return

    # A needs_input candidate has no artifact. Replay its review, then show the
    # already-produced questions only after the reviewer passes.
    async with read_session(ctx.session_factory) as session:
        producer_call = await session.scalar(
            select(RoleCall)
            .where(
                RoleCall.run_id == ctx.run_id,
                RoleCall.role == "requirements_consultant",
                RoleCall.status == "ok",
            )
            .order_by(RoleCall.started_at.desc())
            .limit(1)
        )
    if producer_call is None or not producer_call.response_json:
        raise _unavailable()
    response = _json_dict(producer_call.response_json)
    try:
        output = await run_role_request(
            ctx,
            "output_reviewer",
            "S03",
            request_raw,
            capability="TEXT",
            attempt=int(checkpoint.get("attempt") or 1),
        )
        decision, _checks, _repair = s03._validate_review(
            output,
            target_id=target_id,
            allowed_evidence=set(payload.get("task", {}).get("allowed_evidence_ids") or [])
            | {target_id},
        )
    except ApiError as exc:
        await _review_error(ctx, "S03", exc)
    if decision != "pass":
        await set_stage(
            ctx,
            "S03",
            "blocked",
            issue_code=f"REVIEW_{decision.upper()}",
            template_id="REVIEW_UNAVAILABLE",
        )
        return
    role_output = RoleOutput(
        role="requirements_consultant",
        status=str(response.get("status") or ""),
        data=response.get("data") if isinstance(response.get("data"), dict) else None,
        issues=response.get("issues") or [],
        questions=response.get("questions") or [],
    )
    if role_output.status != "needs_input":
        raise _stale()
    await set_stage(
        ctx,
        "S03",
        "needs_input",
        issue_code="MISSING_INPUT",
        message=s03._joined_questions(role_output),
    )


async def _resume_s04_review(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    request_raw = str(checkpoint.get("request_json") or "")
    request = _json_dict(request_raw)
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    targets = payload.get("review_targets") or []
    target = targets[0] if targets and isinstance(targets[0], dict) else {}
    target_id = str(target.get("target_id") or "")
    artifact = await _artifact_by_id(ctx, target_id)
    if artifact is None or artifact.kind != "creative_direction" or artifact.status != "candidate":
        raise _stale()
    latest = await latest_artifact(ctx, "creative_direction")
    if latest is None or latest.id != artifact.id:
        raise _stale()
    allowed = set(payload.get("task", {}).get("allowed_evidence_ids") or []) | {target_id}
    try:
        output = await run_role_request(
            ctx,
            "output_reviewer",
            "S04",
            request_raw,
            capability="TEXT",
            attempt=int(checkpoint.get("attempt") or 1),
        )
        decision, checks = s04._validate_review_shape(output, target_id, allowed)
    except ApiError as exc:
        if exc.code in _REVIEW_PROTOCOL_CODES:
            await set_stage(ctx, "S04", "blocked", issue_code=exc.code, template_id=exc.code)
            raise
        await set_stage(
            ctx,
            "S04",
            "blocked",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise _fixed_review() from None
    if ctx.project.revision != checkpoint.get("project_revision"):
        await set_artifact_status(ctx, artifact.id, "superseded")
        raise _stale()
    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision=decision,
        checks={
            "ruleset_version": RULESET_VERSION,
            "checks": checks,
            "rule_ids": list(s04.REQUIRED_RULES),
        },
    )
    if decision == "pass":
        await set_stage(ctx, "S05", "active")
        return
    if decision == "revise":
        await set_stage(
            ctx,
            "S04",
            "blocked",
            issue_code="REVIEW_REVISE",
            template_id="ACTION_UNAVAILABLE",
        )
    elif decision == "unknown":
        await set_stage(
            ctx,
            "S04",
            "blocked",
            issue_code="REVIEW_UNKNOWN",
            template_id="REVIEW_UNAVAILABLE",
        )
    else:
        await set_stage(
            ctx,
            "S04",
            "blocked",
            issue_code="REVIEW_REJECTED",
            template_id="ACTION_UNAVAILABLE",
        )


async def _resume_s02_review(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    request_raw = str(checkpoint.get("request_json") or "")
    request = _json_dict(request_raw)
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    targets = payload.get("review_targets") or []
    target = targets[0] if targets and isinstance(targets[0], dict) else {}
    target_id = str(target.get("target_id") or "")
    artifact = await latest_artifact(ctx, "reference_report")
    if artifact is None or artifact.id != target_id or artifact.status != "candidate":
        raise _stale()
    try:
        asset, _data, image = await s02._load_reference(ctx)
    except Exception:
        raise conflict("REFERENCE_NOT_FOUND", templates.render("REFERENCE_INVALID")) from None
    image_evidence = s02.image_evidence_id(asset.id)
    try:
        review = await run_role_request(
            ctx,
            "output_reviewer",
            "S02",
            request_raw,
            images=(image,),
            capability="VISION",
            attempt=int(checkpoint.get("attempt") or 1),
        )
        decision, checks = s02._validate_review(review, target_id, image_evidence)
    except ApiError as exc:
        if exc.code == "CAPABILITY_NOT_READY":
            await set_stage(
                ctx,
                "S02",
                "failed",
                issue_code="CAPABILITY_NOT_READY",
                template_id="CAPABILITY_NOT_READY",
            )
            raise _capability_error("VISION", "未配置") from None
        if exc.code in _REVIEW_PROTOCOL_CODES:
            await set_stage(ctx, "S02", "blocked", issue_code=exc.code, template_id=exc.code)
            raise
        await set_stage(
            ctx,
            "S02",
            "failed",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise _fixed_review() from None
    if (
        ctx.project.revision != checkpoint.get("project_revision")
        or ctx.run.requirement_revision != checkpoint.get("requirement_revision")
        or ctx.project.reference_asset_id != checkpoint.get("reference_asset_id")
    ):
        await set_artifact_status(ctx, artifact.id, "superseded")
        raise _stale()
    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision=decision,
        checks=checks,
        target_kind="artifact",
        raw={"results": review.data.get("results") if review.data else []},
    )
    if decision == "pass":
        await s02._advance_after_usage(ctx)
    else:
        await set_stage(
            ctx,
            "S02",
            "blocked",
            issue_code="G02_" + decision.upper(),
            template_id="REVIEW_UNAVAILABLE",
        )


async def _resume_s05_review(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    request_raw = str(checkpoint.get("request_json") or "")
    request = _json_dict(request_raw)
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    targets = payload.get("review_targets") or []
    target = targets[0] if targets and isinstance(targets[0], dict) else {}
    target_id = str(target.get("target_id") or "")
    artifact = await _artifact_by_id(ctx, target_id)
    if artifact is None or artifact.status != "candidate":
        raise _stale()
    required_rules = tuple(payload.get("task", {}).get("required_rule_ids") or [])
    allowed = set(payload.get("task", {}).get("allowed_evidence_ids") or []) | {artifact.id}
    try:
        output = await run_role_request(
            ctx,
            "output_reviewer",
            "S05",
            request_raw,
            capability="TEXT",
            attempt=int(checkpoint.get("attempt") or 1),
        )
        decision, checks = s05._validate_review(output, artifact.id, required_rules, allowed)
        dependencies = target.get("dependencies") or []
        if dependencies:
            await s05._verify_dependencies(
                ctx, dependencies, int(checkpoint.get("project_revision") or ctx.project.revision)
            )
    except ApiError as exc:
        if exc.code in _REVIEW_PROTOCOL_CODES:
            await set_stage(ctx, "S05", "blocked", issue_code=exc.code, template_id=exc.code)
            raise
        await set_stage(
            ctx,
            "S05",
            "blocked",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise _fixed_review() from None
    if ctx.project.revision != checkpoint.get("project_revision"):
        await set_artifact_status(ctx, artifact.id, "superseded")
        raise _stale()
    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision=decision,
        checks={"ruleset_version": RULESET_VERSION, "checks": checks},
        raw={"results": output.data.get("results") if output.data else []},
    )
    if decision == "pass":
        if artifact.kind == "final_brief":
            await set_stage(ctx, "S06", "active")
        else:
            await set_stage(ctx, "S05", "active")
    else:
        await set_stage(
            ctx,
            "S05",
            "blocked",
            issue_code="REVIEW_" + decision.upper(),
            template_id="REVIEW_UNAVAILABLE",
        )


async def _s09_recovery_inputs(ctx: WorkflowContext) -> dict[str, Any]:
    candidate = await s09._load_candidate(ctx)
    snapshot, snapshot_digest, execution_snapshot = await s09._confirmed_snapshot(ctx)
    (
        reference,
        reference_usage,
        reference_bytes,
    ) = await s09._reference_context(ctx, snapshot)
    if reference_usage != "none" and (reference is None or reference_bytes is None):
        await s09._block_stage(ctx, "IMAGE_NOT_APPROVED", "UNREADABLE_IMAGE")
        raise _fixed_review()
    size = str(
        snapshot.get("size")
        or ctx.project.image_size
        or ctx.settings.visual().image_size
        or "unknown"
    )
    rules = s09._rule_specs(
        reference is not None and reference_usage == "product", execution_snapshot
    )
    requirement_text = str(
        snapshot.get("product_description")
        or snapshot.get("purpose")
        or "按确认需求展示商品主体"
    )
    forbidden = s09._forbidden_requirement(snapshot)
    image_target = f"image:{candidate.asset.id}"
    quality_target = f"quality_report:{candidate.asset.id}"
    allowed = {
        image_target,
        f"requirement:{snapshot_digest}",
        f"technical:{candidate.asset.id}",
    }
    if reference is not None:
        allowed.add(f"reference:{reference.id}")
    images = [OrganizeImage(data=candidate.data, mime_type=candidate.mime_type)]
    if reference_bytes is not None and reference is not None:
        images.append(
            OrganizeImage(data=reference_bytes, mime_type=reference.mime_type)
        )
    return {
        "candidate": candidate,
        "snapshot": snapshot,
        "execution_snapshot": execution_snapshot,
        "reference": reference,
        "reference_usage": reference_usage,
        "rules": rules,
        "size": size,
        "requirement_text": requirement_text,
        "forbidden": forbidden,
        "quality_target": quality_target,
        "image_target": image_target,
        "allowed": allowed,
        "images": images,
        "rule_payload": s09._rule_payload(
            rules,
            snapshot,
            requirement_text,
            forbidden,
            size,
            execution_snapshot,
        ),
    }


def _request_parts(
    checkpoint: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    request_raw = str(checkpoint.get("request_json") or "")
    request = _json_dict(request_raw)
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    return request_raw, request, payload


async def _resume_s09_producer(
    ctx: WorkflowContext, checkpoint: dict[str, Any]
) -> None:
    request_raw, _, payload = _request_parts(checkpoint)
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    input_data = (
        payload.get("input_data") if isinstance(payload.get("input_data"), dict) else {}
    )
    technical = (
        input_data.get("technical")
        if isinstance(input_data.get("technical"), dict)
        else {}
    )
    if task.get("role") != "quality_compliance" or not request_raw:
        raise _stale()
    inputs = await _s09_recovery_inputs(ctx)
    candidate = inputs["candidate"]
    reference = inputs["reference"]
    if input_data.get("asset_id") != candidate.asset.id:
        raise _stale()
    if technical.get("sha256") != candidate.sha256:
        raise _stale()
    expected_reference = reference.id if reference is not None else None
    if input_data.get("reference_asset_id") != expected_reference:
        raise _stale()
    try:
        quality = await run_role_request(
            ctx,
            "quality_compliance",
            STAGE,
            request_raw,
            images=tuple(inputs["images"]),
            capability=str(checkpoint.get("capability") or "VISION"),
            attempt=int(checkpoint.get("attempt") or 1),
        )
    except ApiError:
        await s09._fail_stage(ctx, "SERVICE_UNAVAILABLE", "SERVICE_UNAVAILABLE")
        raise _fixed_service() from None
    if quality.status == "blocked":
        await s09._set_asset(
            ctx,
            candidate.asset,
            "quarantined",
            "quality blocked",
            candidate.sha256,
        )
        await s09._set_job(ctx, candidate.job, "quarantined")
        await s09._block_stage(
            ctx, "IMAGE_NOT_APPROVED", s09._issue_code(quality, "UNREADABLE_IMAGE")
        )
        return
    try:
        quality_data = s09._validate_quality(
            quality,
            asset=candidate.asset,
            rules=inputs["rules"],
            allowed=inputs["allowed"],
        )
    except ApiError:
        await s09._set_asset(
            ctx,
            candidate.asset,
            "quarantined",
            "quality contract invalid",
            candidate.sha256,
        )
        await s09._fail_stage(ctx, "SERVICE_UNAVAILABLE", "CONTRACT_ERROR")
        raise _fixed_service() from None
    quality_data = s09._with_local_technical_result(
        quality_data,
        candidate=candidate,
        size=inputs["size"],
        technical_evidence=f"technical:{candidate.asset.id}",
    )
    existing = await latest_artifact(ctx, "quality_report")
    if existing is not None and existing.status != "candidate":
        existing = None
    await s09._review_and_apply(
        ctx,
        candidate=candidate,
        quality_data=quality_data,
        rules=inputs["rules"],
        quality_target=inputs["quality_target"],
        image_target=inputs["image_target"],
        quality_allowed=inputs["allowed"],
        snapshot=inputs["snapshot"],
        size=inputs["size"],
        rule_payload=inputs["rule_payload"],
        existing_artifact=existing,
    )


async def _resume_s09_review(
    ctx: WorkflowContext, checkpoint: dict[str, Any]
) -> None:
    request_raw, _, payload = _request_parts(checkpoint)
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    targets = payload.get("review_targets") or []
    if task.get("role") != "output_reviewer" or not request_raw or not targets:
        raise _stale()
    quality_target: dict[str, Any] | None = None
    image_target: dict[str, Any] | None = None
    for raw_target in targets:
        if not isinstance(raw_target, dict):
            continue
        if raw_target.get("target_kind") == "quality_report":
            quality_target = raw_target
        elif raw_target.get("target_kind") == "image":
            image_target = raw_target
    if quality_target is None or image_target is None:
        raise _stale()
    artifact = await _artifact_by_id(ctx, str(quality_target.get("artifact_id") or ""))
    if (
        artifact is None
        or artifact.kind != "quality_report"
        or artifact.status not in {"candidate", "approved"}
    ):
        raise _stale()
    inputs = await _s09_recovery_inputs(ctx)
    candidate = inputs["candidate"]
    if artifact.digest != quality_target.get("artifact_digest"):
        raise _stale()
    if image_target.get("asset_id") != candidate.asset.id:
        raise _stale()
    if image_target.get("sha256") != candidate.sha256:
        raise _stale()
    quality_data = _json_dict(artifact.payload_json)
    if not quality_data:
        raise _stale()
    try:
        review = await run_role_request(
            ctx,
            "output_reviewer",
            STAGE,
            request_raw,
            images=tuple(inputs["images"]),
            capability=str(checkpoint.get("capability") or "VISION"),
            attempt=int(checkpoint.get("attempt") or 1),
        )
    except ApiError:
        await s09._fail_stage(ctx, "REVIEW_UNAVAILABLE", "REVIEW_UNAVAILABLE")
        raise _fixed_review() from None
    await s09._apply_review(
        ctx,
        artifact=artifact,
        candidate=candidate,
        quality_data=quality_data,
        rules=inputs["rules"],
        quality_target=inputs["quality_target"],
        image_target=inputs["image_target"],
        quality_allowed=set(inputs["allowed"]) | {f"report:{artifact.id}"},
        review=review,
    )


async def _resume_s10_review(
    ctx: WorkflowContext, checkpoint: dict[str, Any]
) -> None:
    """Replay the persisted manifest review without regenerating its artifact."""
    request_raw, _, payload = _request_parts(checkpoint)
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    targets = payload.get("review_targets") or []
    target = targets[0] if targets and isinstance(targets[0], dict) else {}
    target_id = str(target.get("target_id") or "")
    prefix = "delivery_manifest:"
    if (
        task.get("role") != "output_reviewer"
        or not request_raw
        or not target_id.startswith(prefix)
    ):
        raise _stale()
    artifact = await _artifact_by_id(ctx, target_id[len(prefix) :])
    latest = await latest_artifact(ctx, "delivery_manifest")
    if (
        artifact is None
        or artifact.kind != "delivery_manifest"
        or latest is None
        or latest.id != artifact.id
        or artifact.status != "candidate"
    ):
        if artifact is not None and artifact.status == "candidate":
            await set_artifact_status(ctx, artifact.id, "superseded")
        raise _stale()
    try:
        job, asset = await s10._load_job_and_asset(ctx)
        if (
            target.get("asset_id") != asset.id
            or target.get("requirement_revision") != ctx.run.requirement_revision
        ):
            raise _stale()
        await s10._require_image_approval(ctx, asset, job)
        data, _info = await s10._verify_image_file(ctx, asset)
    except ApiError as exc:
        await set_stage(
            ctx,
            "S10",
            "blocked" if exc.status_code == 409 else "failed",
            issue_code=exc.code,
            template_id=exc.code,
        )
        raise
    allowed = set(task.get("allowed_evidence_ids") or []) | {target_id}
    try:
        review = await run_role_request(
            ctx,
            "output_reviewer",
            "S10",
            request_raw,
            images=(OrganizeImage(data=data, mime_type=asset.mime_type),),
            capability="TEXT",
            attempt=int(checkpoint.get("attempt") or 1),
        )
    except ApiError:
        await set_stage(
            ctx,
            "S10",
            "failed",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise _fixed_review() from None
    try:
        adjudication = s10._validate_review(review, target_id, allowed)
    except ApiError as exc:
        await _review_error(ctx, "S10", exc)
        raise
    if ctx.project.revision != checkpoint.get(
        "project_revision"
    ) or ctx.run.requirement_revision != checkpoint.get("requirement_revision"):
        await set_artifact_status(ctx, artifact.id, "superseded")
        raise _stale()
    await s10._accept_manifest_review(
        ctx,
        artifact=artifact,
        asset=asset,
        job=job,
        target_id=target_id,
        review=review,
        adjudication=adjudication,
    )


async def _resume_s07_producer(
    ctx: WorkflowContext, checkpoint: dict[str, Any]
) -> None:
    """Replay the exact persisted prompt producer request without a new budget claim."""
    request_raw, _, payload = _request_parts(checkpoint)
    task = payload.get("task") if isinstance(payload.get("task"), dict) else {}
    if task.get("role") != "generation_engineer" or not request_raw:
        raise _stale()
    try:
        await s07._compile(ctx, None, recovery=checkpoint)
    except ApiError as exc:
        if exc.code == "PROMPT_INVALID":
            await set_stage(ctx, "S07", "active")
        elif exc.code == "PROMPT_REVIEW_INVALID":
            await set_stage(
                ctx,
                "S07",
                "blocked",
                issue_code="REVIEW_INVALID",
                template_id="REVIEW_INVALID",
            )
        elif exc.code == "PROMPT_REVIEW_REJECTED":
            await set_stage(
                ctx,
                "S07",
                "blocked",
                issue_code="REVIEW_REJECTED",
                template_id="REVIEW_REJECTED",
            )
        raise


async def _resume_review(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    stage = str(checkpoint.get("stage"))
    if stage == "S01":
        await _resume_s01_review(ctx, checkpoint)
    elif stage == "S03":
        await _resume_s03_review(ctx, checkpoint)
    elif stage == "S04":
        await _resume_s04_review(ctx, checkpoint)
    elif stage == "S02":
        await _resume_s02_review(ctx, checkpoint)
    elif stage == "S05":
        await _resume_s05_review(ctx, checkpoint)
    elif stage == "S07":
        await _resume_s07_review(ctx, checkpoint)
    elif stage == "S09":
        await _resume_s09_review(ctx, checkpoint)
    elif stage == "S10":
        await _resume_s10_review(ctx, checkpoint)
    else:
        raise _unavailable()


async def _resume_producer(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    stage = str(checkpoint.get("stage"))
    if stage == "S01":
        await _resume_s01_producer(ctx, checkpoint)
        return
    if stage == "S03":
        await s03._compose(ctx, "compose_brief", None)
        return
    if stage == "S04":
        await s04._plan(ctx)
        return
    if stage == "S05":
        await s05._plan_visual(ctx, {})
        return
    if stage == "S02":
        await s02.analyze(ctx)
        return
    if stage == "S07":
        await _resume_s07_producer(ctx, checkpoint)
        return
    if stage == "S09":
        await _resume_s09_producer(ctx, checkpoint)
        return
    raise _unavailable()


async def _guard_after_recovery(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    async with read_session(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        run = await session.get(WorkflowRun, ctx.run_id)
    if project is None or run is None:
        raise _stale()
    if project.revision != checkpoint.get(
        "project_revision"
    ) or run.requirement_revision != checkpoint.get("requirement_revision"):
        kind = _ARTIFACT_KIND_BY_STAGE.get(str(checkpoint.get("stage")))
        if kind:
            latest = await latest_artifact(ctx, kind)
            if latest is not None and latest.status in {"candidate", "approved"}:
                await set_artifact_status(ctx, latest.id, "superseded")
        await set_stage(
            ctx,
            str(checkpoint.get("stage")),
            "blocked",
            issue_code="RUN_SUPERSEDED",
            template_id="RUN_SUPERSEDED",
        )
        raise _stale()


async def _record_resumed(ctx: WorkflowContext, checkpoint: dict[str, Any]) -> None:
    await record_diagnostic(
        ctx,
        "recovery_resumed",
        {
            "operation_id": checkpoint.get("operation_id"),
            "run_id": ctx.run_id,
            "stage": checkpoint.get("stage"),
            "role": checkpoint.get("role"),
            "phase": checkpoint.get("phase"),
        },
    )


async def _continue(ctx: WorkflowContext) -> None:
    if ctx.run is None:
        raise _unavailable()
    checkpoint = await _latest_checkpoint(ctx)
    if checkpoint is None:
        raise _unavailable()
    # A repeated click or HTTP resend after a successful manual continue is a
    # no-op returning the current view; it never invokes a model again.
    if await _is_resumed(ctx, checkpoint) and ctx.run.status != "failed":
        return
    if str(checkpoint.get("stage")) != str(ctx.run.stage):
        raise _stale()
    if ctx.run.status not in {"failed", "blocked"}:
        raise _unavailable()
    if not _is_recoverable_checkpoint(ctx, checkpoint):
        raise _unavailable()
    _verify_checkpoint(ctx, checkpoint)
    if str(checkpoint.get("stage")) == "S09":
        # Expired or missing candidate bytes are never re-created, re-fetched
        # or guessed around. _load_candidate raises the fixed public 409.
        await s09._load_candidate(ctx)
    await _ensure_budget(ctx, checkpoint)
    capability = str(checkpoint.get("capability") or "TEXT")
    _ensure_capability(ctx, capability)
    operation_id = str(checkpoint.get("operation_id") or "")
    await claim_recovery(ctx, operation_id, stage=str(checkpoint.get("stage")))
    try:
        if checkpoint.get("phase") == "review":
            await _resume_review(ctx, checkpoint)
        else:
            await _resume_producer(ctx, checkpoint)
        await _guard_after_recovery(ctx, checkpoint)
        await _record_resumed(ctx, checkpoint)
    finally:
        await release_recovery(ctx, operation_id)


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if action != "continue_workflow":
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    await _continue(ctx)


__all__ = ["ACTIONS", "STAGE", "available", "handle"]

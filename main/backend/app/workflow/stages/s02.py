from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import select

from app.adapters.base import OrganizeImage
from app.db import read_session
from app.errors import ApiError, conflict, validation_error
from app.models import Asset, Message
from app.services.assets import (
    ImageValidationError,
    inspect_image,
    resolve_asset_file,
)
from app.workflow import templates
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import (
    WorkflowContext,
    add_review,
    approved_artifact,
    create_run,
    invalidate_requirements,
    record_diagnostic,
    set_artifact_status,
    set_stage,
    start_artifact,
)
from app.workflow.tables import Artifact, DiagnosticEvent

STAGE = "S02"
ACTIONS = [
    "analyze_reference",
    "confirm_reference_usage",
]

REFERENCE_USAGES = ("product", "style", "composition")
DEFAULT_REFERENCE_USAGE = "product"

#: Rules the independent visual reviewer must attest for every reference report.
#: A pass that omits, duplicates or invents a rule never opens G02.
REQUIRED_REVIEW_RULES = (
    "G02_IMAGE_VISIBLE",
    "G02_OBSERVATION_GROUNDED",
    "G02_TEXT_IS_DATA",
    "G02_NO_UNSUPPORTED_INFERENCE",
)

_RETRYABLE_ISSUES = {
    "CAPABILITY_NOT_READY",
    "SERVICE_UNAVAILABLE",
    "REVIEW_UNAVAILABLE",
}

_REPORT_KIND = "reference_report"


def image_evidence_id(asset_id: str) -> str:
    return "reference:" + asset_id


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _payload(artifact: Any) -> dict[str, Any]:
    return _json_dict(getattr(artifact, "payload_json", None))


def _evidence_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for bucket in ("observations", "texts"):
        items = payload.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            values = item.get("evidence_ids")
            if isinstance(values, list):
                ids.extend(value for value in values if isinstance(value, str))
    return ids


async def _has_user_text(ctx: WorkflowContext) -> bool:
    async with read_session(ctx.session_factory) as session:
        found = await session.scalar(
            select(Message.id)
            .where(Message.project_id == ctx.project_id, Message.role == "user")
            .limit(1)
        )
    return found is not None


async def approved_report_for_current_asset(ctx: WorkflowContext) -> Artifact | None:
    """Return the approved report only when it was produced for the current file.

    A report binds itself to the original image through the server-issued
    evidence id reference:asset_id. Every observation and every image text item
    must cite that exact id. Replacing the image therefore cannot leave an older
    report usable for the new file.
    """
    asset_id = ctx.project.reference_asset_id
    if not asset_id:
        return None
    artifact = await approved_artifact(ctx, _REPORT_KIND)
    if artifact is None:
        return None
    payload = _payload(artifact)
    if payload.get("reference_usage") not in REFERENCE_USAGES:
        return None
    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        return None
    expected = image_evidence_id(asset_id)
    evidence = _evidence_ids(payload)
    if not evidence:
        return None
    if any(item != expected for item in evidence):
        return None
    return artifact


async def current_reference_usage(ctx: WorkflowContext) -> str:
    artifact = await approved_report_for_current_asset(ctx)
    if artifact is not None:
        usage = _payload(artifact).get("reference_usage")
        if usage in REFERENCE_USAGES:
            return str(usage)
    # A usage change can fail before the new report exists. The selected usage
    # is still a durable requirement choice and must survive a manual retry.
    async with read_session(ctx.session_factory) as session:
        event = await session.scalar(
            select(DiagnosticEvent)
            .where(
                DiagnosticEvent.project_id == ctx.project_id,
                DiagnosticEvent.event == "reference_usage_selected",
            )
            .order_by(DiagnosticEvent.created_at.desc())
            .limit(1)
        )
    if event is not None:
        usage = _json_dict(event.detail_json).get("usage")
        if usage in REFERENCE_USAGES:
            return str(usage)
    return DEFAULT_REFERENCE_USAGE


def _validate_usage(value: object) -> str:
    if not isinstance(value, str) or value not in REFERENCE_USAGES:
        raise validation_error("参考图用途不合法")
    return value


async def _resolve_usage(ctx: WorkflowContext, value: object) -> str:
    if value is None:
        return await current_reference_usage(ctx)
    return _validate_usage(value)


class _ReferenceUnusable(Exception):
    pass


async def _load_reference(ctx: WorkflowContext) -> tuple[Asset, bytes, OrganizeImage]:
    asset_id = ctx.project.reference_asset_id
    if not asset_id:
        raise _ReferenceUnusable()
    async with read_session(ctx.session_factory) as session:
        asset = await session.get(Asset, asset_id)
    if asset is None or asset.project_id != ctx.project_id or asset.kind != "reference":
        raise _ReferenceUnusable()
    path = resolve_asset_file(ctx.settings, asset)
    if path is None:
        raise _ReferenceUnusable()
    try:
        data = await asyncio.to_thread(path.read_bytes)
        info = await asyncio.to_thread(inspect_image, data)
    except (OSError, ImageValidationError):
        raise _ReferenceUnusable() from None
    return asset, data, OrganizeImage(data=data, mime_type=info.mime_type)


async def _supersede_reports(ctx: WorkflowContext) -> None:
    async with read_session(ctx.session_factory) as session:
        artifacts = (
            await session.scalars(
                select(Artifact).where(
                    Artifact.run_id == ctx.run_id,
                    Artifact.kind == _REPORT_KIND,
                )
            )
        ).all()
    for artifact in artifacts:
        if artifact.status != "superseded":
            await set_artifact_status(ctx, artifact.id, "superseded")


async def _set_failure(
    ctx: WorkflowContext,
    *,
    phase: str,
    error: ApiError,
    status: str = "failed",
) -> None:
    if error.code == "CAPABILITY_NOT_READY":
        template_id = "CAPABILITY_NOT_READY"
        params = {"capability_label": templates.capability_label("VISION") or "视觉理解"}
    elif error.code == "INVALID_REFERENCE_IMAGE":
        template_id = "REFERENCE_INVALID"
        params = {}
        status = "blocked"
    elif phase == "review":
        template_id = "REVIEW_UNAVAILABLE"
        params = {}
    else:
        template_id = "SERVICE_UNAVAILABLE"
        params = {}
    await set_stage(
        ctx,
        STAGE,
        status,
        issue_code=error.code,
        template_id=template_id,
        params=params,
    )


def _invalid_service() -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE"))


def _invalid_review() -> ApiError:
    return ApiError(503, "REVIEW_UNAVAILABLE", templates.render("REVIEW_UNAVAILABLE"))


def _validate_report(
    output: RoleOutput,
    usage: str,
    expected_evidence: str,
) -> dict[str, Any]:
    if output.status != "ready" or output.data is None:
        raise _invalid_service()
    data = output.data
    if data.get("reference_usage") != usage:
        raise _invalid_service()
    observations = data.get("observations")
    if not isinstance(observations, list) or not observations:
        raise _invalid_service()
    texts = data.get("texts")
    if not isinstance(texts, list):
        raise _invalid_service()
    for item in [*observations, *texts]:
        if not isinstance(item, dict):
            raise _invalid_service()
        ids = item.get("evidence_ids")
        if not isinstance(ids, list) or not ids:
            raise _invalid_service()
        if any(not isinstance(value, str) or value != expected_evidence for value in ids):
            raise _invalid_service()
    if any(
        isinstance(text, dict) and text.get("legibility") == "unclear" for text in texts
    ) and not data.get("uncertainties"):
        raise _invalid_service()
    return data


def _validate_review(
    output: RoleOutput,
    artifact_id: str,
    image_evidence: str,
) -> tuple[str, list[dict[str, Any]]]:
    if output.status != "ready" or output.data is None:
        raise _invalid_review()
    results = output.data.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise _invalid_review()
    result = results[0]
    if not isinstance(result, dict) or result.get("target_id") != artifact_id:
        raise _invalid_review()
    decision = result.get("decision")
    if decision not in {"pass", "revise", "reject", "unknown"}:
        raise _invalid_review()
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks:
        raise _invalid_review()
    allowed_evidence = {artifact_id, image_evidence}
    seen: list[str] = []
    cites_image = False
    for check in checks:
        if not isinstance(check, dict):
            raise _invalid_review()
        rule_id = check.get("rule_id")
        if rule_id not in REQUIRED_REVIEW_RULES:
            raise _invalid_review()
        outcome = check.get("outcome")
        if outcome not in {"pass", "fail", "unknown", "not_applicable"}:
            raise _invalid_review()
        if decision == "pass" and outcome != "pass":
            raise _invalid_review()
        evidence = check.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence:
            raise _invalid_review()
        if any(not isinstance(value, str) or value not in allowed_evidence for value in evidence):
            raise _invalid_review()
        if image_evidence in evidence:
            cites_image = True
        note = check.get("note")
        if isinstance(note, str) and "://" in note.lower():
            raise _invalid_review()
        seen.append(str(rule_id))
    if set(seen) != set(REQUIRED_REVIEW_RULES) or len(seen) != len(set(seen)):
        raise _invalid_review()
    if not cites_image:
        # The reviewer must have looked at the actual original image, not only
        # at the producer's text claims.
        raise _invalid_review()
    return str(decision), checks


async def _advance_after_usage(ctx: WorkflowContext) -> None:
    if ctx.run is not None and ctx.run.intent_route == "image_request" and await _has_user_text(ctx):
        await set_stage(ctx, "S03", "active")
        return
    await set_stage(
        ctx,
        "S01",
        "needs_input",
        issue_code="MISSING_INPUT",
        template_id="INTENT_UNCLEAR",
    )


async def analyze(ctx: WorkflowContext, usage: object = None) -> None:
    """Run the reference analyst and its independent visual reviewer once.

    Missing capability, unreadable files, technical failures and non-pass review
    decisions never fall back to a text-only interpretation and are never
    retried automatically.
    """
    if ctx.run is None:
        await create_run(ctx, STAGE)
    elif ctx.run.stage != STAGE:
        await set_stage(ctx, STAGE, "active")
    if ctx.run is not None and ctx.run.status == "blocked":
        raise conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))

    resolved_usage = await _resolve_usage(ctx, usage)
    try:
        asset, data, image = await _load_reference(ctx)
    except _ReferenceUnusable:
        error = ApiError(
            422,
            "INVALID_REFERENCE_IMAGE",
            templates.render("REFERENCE_INVALID"),
        )
        await _set_failure(ctx, phase="analysis", error=error)
        raise error from None

    expected_evidence = image_evidence_id(asset.id)
    existing = await approved_report_for_current_asset(ctx)
    if existing is not None and _payload(existing).get("reference_usage") == resolved_usage:
        await set_stage(
            ctx,
            STAGE,
            "needs_input",
            issue_code="MISSING_INPUT",
            template_id="INTENT_UNCLEAR",
        )
        return

    await _supersede_reports(ctx)
    phase = "analysis"
    try:
        report = await run_role(
            ctx,
            "reference_analyst",
            STAGE,
            {
                "reference_asset_id": asset.id,
                "reference_usage": resolved_usage,
            },
            approved_inputs={},
            images=(image,),
            capability="VISION",
            attempt=1,
            required_rule_ids=("G02_REFERENCE_USAGE", "G02_OBSERVATION_GROUNDED"),
            allowed_evidence_ids=(expected_evidence,),
        )
        report_data = _validate_report(report, resolved_usage, expected_evidence)
        artifact = await start_artifact(ctx, _REPORT_KIND, report_data)

        phase = "review"
        review = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": "reference_analyst",
                "target_kind": _REPORT_KIND,
                "reference_asset_id": asset.id,
                "reference_usage": resolved_usage,
            },
            review_targets=[
                {
                    "target_id": artifact.id,
                    "target_kind": _REPORT_KIND,
                    "reference_asset_id": asset.id,
                    "reference_usage": resolved_usage,
                    "report": report_data,
                }
            ],
            images=(image,),
            capability="VISION",
            attempt=1,
            required_rule_ids=REQUIRED_REVIEW_RULES,
            allowed_evidence_ids=(artifact.id, expected_evidence),
        )
        decision, checks = _validate_review(review, artifact.id, expected_evidence)
    except ApiError as error:
        await _set_failure(ctx, phase=phase, error=error)
        raise

    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision=decision,
        checks=checks,
        target_kind="artifact",
        raw={"results": review.data.get("results") if review.data else []},
    )
    if decision != "pass":
        await set_stage(
            ctx,
            STAGE,
            "blocked",
            issue_code="G02_" + decision.upper(),
            template_id="REVIEW_UNAVAILABLE",
        )
        return

    if ctx.run is not None and ctx.run.intent_route == "image_request" and await _has_user_text(ctx):
        # Text plus image: S01 already classified the request, and product is the
        # default reference usage. Continue to requirements without an extra
        # click. The independent upload path instead asks for the necessary use.
        await set_stage(ctx, "S03", "active")
        return

    await set_stage(
        ctx,
        STAGE,
        "needs_input",
        issue_code="MISSING_INPUT",
        template_id="INTENT_UNCLEAR",
    )


async def prepare_after_reference_change(ctx: WorkflowContext) -> None:
    """Invalidate prior reports and dependent confirmations after a new upload."""
    if ctx.run is None:
        return
    await record_diagnostic(
        ctx,
        "reference_usage_selected",
        {"usage": DEFAULT_REFERENCE_USAGE, "asset_id": ctx.project.reference_asset_id},
    )
    await invalidate_requirements(ctx, "reference_changed")
    await _supersede_reports(ctx)
    await set_stage(ctx, STAGE, "active")


async def prepare_after_reference_removal(ctx: WorkflowContext) -> None:
    """Only an explicit user removal creates the no-reference new version."""
    if ctx.run is None:
        return
    await invalidate_requirements(ctx, "reference_removed")
    await _supersede_reports(ctx)
    await set_stage(ctx, "S01", "active")


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    if ctx.run is None:
        return ["analyze_reference"] if ctx.project.reference_asset_id else []
    if ctx.run.stage != STAGE:
        return []
    if ctx.run.status == "needs_input" and ctx.run.template_id == "INTENT_UNCLEAR":
        return ["confirm_reference_usage"]
    if ctx.run.status == "failed" and ctx.run.issue_code in _RETRYABLE_ISSUES:
        return ["analyze_reference"]
    return []


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if action == "analyze_reference":
        if ctx.run is not None and ctx.run.status == "blocked":
            raise conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
        await analyze(ctx, payload.get("usage"))
        return

    if action == "confirm_reference_usage":
        usage = _validate_usage(payload.get("usage"))
        report = await approved_report_for_current_asset(ctx)
        if report is None:
            raise conflict(
                "REFERENCE_REPORT_NOT_APPROVED",
                templates.render("ACTION_UNAVAILABLE"),
            )
        current = _payload(report).get("reference_usage")
        if current != usage:
            await record_diagnostic(
                ctx,
                "reference_usage_selected",
                {"usage": usage, "asset_id": ctx.project.reference_asset_id},
            )
            await invalidate_requirements(ctx, "reference_usage_changed")
            await _supersede_reports(ctx)
            await analyze(ctx, usage=usage)
        await _advance_after_usage(ctx)
        return

    raise conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))


__all__ = [
    "STAGE",
    "ACTIONS",
    "REFERENCE_USAGES",
    "DEFAULT_REFERENCE_USAGE",
    "REQUIRED_REVIEW_RULES",
    "image_evidence_id",
    "approved_report_for_current_asset",
    "current_reference_usage",
    "analyze",
    "prepare_after_reference_change",
    "prepare_after_reference_removal",
    "available",
    "handle",
]

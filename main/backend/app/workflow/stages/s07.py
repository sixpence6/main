from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from app.acceptance import abandon_intent, latest_intent
from app.db import read_session
from app.errors import ApiError, conflict
from app.models import Asset
from app.prompt_guard import prompt_drift_errors
from app.workflow import state, templates
from app.workflow.adjudication import (
    RULESET_VERSION,
    ProtocolIssue,
    RuleSet,
    adjudicate,
    s07_rules,
)
from app.workflow.contracts import ContractError, validate_data
from app.workflow.reasons import (
    CandidateDefect,
    CandidateDefectCode,
    ReasonCode,
    classify_api_error,
    classify_candidate_defects,
    classify_issue_code,
    classify_review_decision,
    is_auto_repairable,
    repair_codes_from_defects,
)
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import WorkflowContext
from app.workflow.tables import ArtifactReview, GenerationJob, RevisionBudget

STAGE = "S07"
ACTIONS = ["compile_prompt", "revise_prompt"]
# Natural-language prompt writing may add connective words around a confirmed
# requirement. Compare normalized, ordered characters instead of requiring a
# brittle byte-for-byte substring while still rejecting missing content.
_COVERAGE_STRIP = re.compile(r"[\s，,。；;：:、\-—()（）\[\]【】“”\"'!?！？/]+")

_CONFIRM_MESSAGE = "请先确认当前需求卡，再明确提交生成。"
_DRIFT_MESSAGE = "确认需求已变化，请重新确认需求版本后再编制提示词。"
_INVALID_MESSAGE = "提示词未覆盖全部已确认硬要求或参数不一致，已阻止进入生成。"
_REVIEW_REJECTED_MESSAGE = "提示词未通过独立审查，已阻止进入生成。"
_REVIEW_INVALID_MESSAGE = "独立审查结果不完整，提示词未放行。"
_RECONFIRM_MESSAGE = (
    "提示词引入了尚未确认的卖点、文字或弱化了已确认要求，未提交生图。"
    "请修改需求并重新确认后再生成。"
)


def available(ctx: WorkflowContext) -> list[str]:
    if ctx.run is None or ctx.run.stage != STAGE or ctx.run.status != "active":
        return []
    return list(ACTIONS)


def _confirmed_error() -> ApiError:
    return ApiError(409, "CONFIRM_REQUIRED", _CONFIRM_MESSAGE)


def _drift_error() -> ApiError:
    return ApiError(409, "REQUIREMENT_DRIFT", _DRIFT_MESSAGE)


def _invalid_prompt() -> ApiError:
    return ApiError(409, "PROMPT_INVALID", _INVALID_MESSAGE)


def _invalid_review() -> ApiError:
    return ApiError(409, "PROMPT_REVIEW_INVALID", _REVIEW_INVALID_MESSAGE)


async def _mark_failed(ctx: WorkflowContext, template_id: str) -> None:
    await state.set_stage(
        ctx,
        STAGE,
        "failed",
        issue_code=template_id,
        template_id=template_id,
    )


async def _ensure_budget(ctx: WorkflowContext) -> None:
    async with read_session(ctx.session_factory) as session:
        budget = await session.get(RevisionBudget, (ctx.run_id, "prompt_plan"))
    if budget is not None and budget.used >= budget.max_allowed:
        raise conflict(
            "REVISION_BUDGET_EXHAUSTED",
            "该产物已达到最多三次候选的上限，请保留当前结果或重新开始流程",
        )


async def _require_intent(ctx: WorkflowContext, artifact: Any) -> Any:
    """An owned v3 project must compile under a durable accepted intent."""
    if ctx.project.owner_account_id is None:
        return None
    intent = await latest_intent(ctx.session_factory, project_id=ctx.project_id, run_id=ctx.run_id)
    if intent is None:
        # A v2 confirmation created before the v3 acceptance seam has no
        # durable intent. It keeps working for inherited v2 fixtures and
        # legacy unowned projects; once an intent exists it is authoritative.
        return None
    if intent.artifact_digest != artifact.digest:
        raise _drift_error()
    if intent.requirement_revision != ctx.run.requirement_revision:
        raise _drift_error()
    if intent.status in {"accepted", "executing"}:
        return intent
    if (
        intent.status == "closed"
        and ctx.run.status == "blocked"
        and ctx.run.issue_code == "LOCAL_TRACKING_CLOSED"
    ):
        # A risk-confirmed new task is a fresh authorization; the previous
        # closed intent remains evidence for the original task only.
        return intent
    if (
        intent.status == "failed"
        and ctx.run.status == "blocked"
        and ctx.run.issue_code == "GENERATION_FAILED"
    ):
        # A proven terminal failure keeps its counted authorization as evidence;
        # the explicit retry action creates a fresh intent before submitting.
        return intent
    if (
        intent.status == "failed"
        and ctx.run.status == "blocked"
        and ctx.run.issue_code == "IMAGE_NOT_APPROVED"
    ):
        # A quality-rejected image is also a proven terminal attempt. The
        # explicit retry action creates a fresh intent before submitting again.
        return intent
    raise _confirmed_error()


async def _require_confirmed(ctx: WorkflowContext) -> tuple[Any, dict[str, Any]]:
    final_artifact = await state.approved_artifact(ctx, "final_brief")
    approval = await state.latest_approval(ctx, "requirements_card")
    if final_artifact is None or approval is None:
        raise _confirmed_error()
    if ctx.project.brief_status != "confirmed":
        raise _confirmed_error()
    if ctx.project.confirmed_revision != ctx.project.revision:
        raise _drift_error()
    if ctx.run is None or ctx.run.requirement_revision != ctx.project.revision:
        raise _drift_error()
    if approval.artifact_digest != final_artifact.digest:
        raise _confirmed_error()
    if approval.requirement_snapshot_digest != final_artifact.digest:
        raise _confirmed_error()
    try:
        brief_model = validate_data("final_brief", json.loads(final_artifact.payload_json))
    except (ContractError, json.JSONDecodeError):
        raise _drift_error() from None
    brief = brief_model.model_dump(mode="json")
    if brief.get("unresolved"):
        raise _drift_error()
    configured_size = ctx.project.image_size or ctx.settings.visual().image_size
    if not configured_size or brief.get("size") != configured_size:
        raise _drift_error()
    if brief.get("reference_asset_id") != ctx.project.reference_asset_id:
        raise _drift_error()
    if brief.get("reference_asset_id") and brief.get("reference_usage") == "none":
        raise _drift_error()
    if not brief.get("reference_asset_id") and brief.get("reference_usage") != "none":
        raise _drift_error()
    reference_id = brief.get("reference_asset_id")
    if reference_id:
        async with read_session(ctx.session_factory) as session:
            asset = await session.get(Asset, reference_id)
        if asset is None or asset.project_id != ctx.project_id or asset.kind != "reference":
            raise _drift_error()
    await _require_intent(ctx, final_artifact)
    return final_artifact, brief


def _hard_requirements(brief: dict[str, Any]) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []

    def add(rule_id: str, requirement: str, binding: str) -> None:
        requirements.append(
            {
                "rule_id": rule_id,
                "requirement": requirement,
                "allowed_bindings": [binding],
            }
        )

    if brief.get("product_description"):
        add("hard:subject", brief["product_description"], "positive_prompt")
    if brief.get("purpose"):
        add("hard:purpose", brief["purpose"], "positive_prompt")
    if brief.get("creative_direction"):
        add("hard:creative", brief["creative_direction"], "positive_prompt")
    if brief.get("style_keywords"):
        add("hard:style", "、".join(brief["style_keywords"]), "positive_prompt")
    if brief.get("composition"):
        add("hard:composition", brief["composition"], "positive_prompt")
    if brief.get("lighting"):
        add("hard:lighting", brief["lighting"], "positive_prompt")
    for index, text in enumerate(brief.get("texts") or []):
        add(f"hard:text:{index}", text, "positive_prompt")
    for index, fact in enumerate(brief.get("facts") or []):
        add(f"hard:fact:{index}", fact["text"], "positive_prompt")
    for index, value in enumerate(brief.get("must_include") or []):
        add(f"hard:must_include:{index}", value, "positive_prompt")
    for index, value in enumerate(brief.get("must_preserve") or []):
        add(f"hard:must_preserve:{index}", value, "positive_prompt")
    for index, value in enumerate(brief.get("forbidden") or []):
        add(f"hard:forbidden:{index}", value, "negative_prompt")
    add("hard:size", brief["size"], "size")
    add("hard:count", "1", "count")
    if brief.get("reference_asset_id"):
        add("hard:reference_asset_id", brief["reference_asset_id"], "reference_asset_id")
    return requirements


def _coverage_text(text: str) -> str:
    return _COVERAGE_STRIP.sub("", text)


def _covers_requirement(requirement: str, field_value: str) -> bool:
    """True when every requirement clause is kept in order in a prompt clause.

    A requirement may contain multiple clauses joined by semicolons. Each
    requirement clause must be covered by one prompt clause, so characters
    cannot be assembled across unrelated prompt clauses.
    """
    expected_clauses = [
        _coverage_text(clause)
        for clause in re.split(r"[；;。\n]+", requirement)
    ]
    expected_clauses = [clause for clause in expected_clauses if clause]
    if not expected_clauses:
        return False
    actual_clauses = [
        _coverage_text(clause)
        for clause in re.split(r"[；;。\n]+", field_value)
    ]
    for expected in expected_clauses:
        if not any(_keeps_characters_in_order(expected, actual) for actual in actual_clauses):
            return False
    return True


def _keeps_characters_in_order(expected: str, actual: str) -> bool:
    position = 0
    for character in actual:
        if position < len(expected) and character == expected[position]:
            position += 1
    return position == len(expected)


def _candidate_defects(
    payload: dict[str, Any],
    requirements: list[dict[str, Any]],
    brief: dict[str, Any],
    configured_size: str,
) -> list[CandidateDefect]:
    defects: list[CandidateDefect] = []
    expected = {item["rule_id"]: item for item in requirements}
    coverage = payload.get("requirement_coverage") or []
    seen: set[str] = set()
    for item in coverage:
        rule_id = item.get("rule_id")
        if rule_id in seen:
            defects.append(
                CandidateDefect(
                    CandidateDefectCode.DUPLICATE_COVERAGE,
                    f"重复覆盖：{rule_id}",
                    rule_id=str(rule_id) if rule_id is not None else None,
                )
            )
            continue
        seen.add(str(rule_id))
        requirement = expected.get(rule_id)
        if requirement is None:
            defects.append(
                CandidateDefect(
                    CandidateDefectCode.UNKNOWN_COVERAGE,
                    f"未知覆盖项：{rule_id}",
                    rule_id=str(rule_id) if rule_id is not None else None,
                )
            )
            continue
        binding = item.get("binding")
        if binding not in requirement["allowed_bindings"]:
            defects.append(
                CandidateDefect(
                    CandidateDefectCode.BINDING_MISMATCH,
                    f"覆盖绑定不匹配：{rule_id}",
                    rule_id=rule_id,
                )
            )
            continue
        value = item.get("value")
        if binding in {"positive_prompt", "negative_prompt"}:
            field_value = payload.get(binding) or ""
            if (
                not value
                or value != requirement["requirement"]
                or not _covers_requirement(value, field_value)
            ):
                defects.append(
                    CandidateDefect(
                        CandidateDefectCode.COVERAGE_MISMATCH,
                        f"提示词覆盖值与已确认要求不一致：{rule_id}",
                        rule_id=rule_id,
                        content_intact=bool(value)
                        and _covers_requirement(requirement["requirement"], field_value),
                    )
                )
        elif binding == "size":
            if value != configured_size:
                defects.append(
                    CandidateDefect(
                        CandidateDefectCode.SIZE_MISMATCH,
                        "尺寸覆盖值不一致",
                        rule_id=rule_id,
                        content_intact=False,
                    )
                )
        elif binding == "count":
            if value != "1":
                defects.append(
                    CandidateDefect(
                        CandidateDefectCode.COUNT_MISMATCH,
                        "数量覆盖值不一致",
                        rule_id=rule_id,
                        content_intact=False,
                    )
                )
        elif binding == "reference_asset_id":
            expected_reference = brief.get("reference_asset_id") or "null"
            if value != expected_reference:
                defects.append(
                    CandidateDefect(
                        CandidateDefectCode.REFERENCE_MISMATCH,
                        "参考图覆盖值不一致",
                        rule_id=rule_id,
                        content_intact=False,
                    )
                )
    for rule_id, requirement in expected.items():
        if rule_id not in seen:
            binding = requirement["allowed_bindings"][0]
            if binding in {"positive_prompt", "negative_prompt"}:
                intact = _covers_requirement(
                    requirement["requirement"], payload.get(binding) or ""
                )
            elif binding == "size":
                intact = payload.get("size") == configured_size
            elif binding == "count":
                intact = payload.get("count") == 1
            else:
                intact = payload.get("reference_asset_id") == brief.get("reference_asset_id")
            defects.append(
                CandidateDefect(
                    CandidateDefectCode.MISSING_COVERAGE,
                    f"缺少覆盖：{rule_id}",
                    rule_id=rule_id,
                    content_intact=intact,
                )
            )
    if payload.get("size") != configured_size:
        defects.append(
            CandidateDefect(
                CandidateDefectCode.SIZE_MISMATCH,
                "提交尺寸与已配置尺寸不一致",
                content_intact=False,
            )
        )
    if payload.get("count") != 1:
        defects.append(
            CandidateDefect(
                CandidateDefectCode.COUNT_MISMATCH,
                "数量必须为 1",
                content_intact=False,
            )
        )
    if payload.get("reference_asset_id") != brief.get("reference_asset_id"):
        defects.append(
            CandidateDefect(
                CandidateDefectCode.REFERENCE_MISMATCH,
                "参考图 ID 与确认快照不一致",
                content_intact=False,
            )
        )
    return defects


def _candidate_errors(
    payload: dict[str, Any],
    requirements: list[dict[str, Any]],
    brief: dict[str, Any],
    configured_size: str,
) -> list[str]:
    return [
        defect.message
        for defect in _candidate_defects(payload, requirements, brief, configured_size)
    ]


def _validated_payload(
    data: dict[str, Any],
    requirements: list[dict[str, Any]],
    brief: dict[str, Any],
    configured_size: str,
) -> tuple[dict[str, Any], list[CandidateDefect]]:
    try:
        payload = validate_data("prompt_plan", data).model_dump(mode="json")
    except ContractError:
        defect = CandidateDefect(
            CandidateDefectCode.CONTRACT_INVALID, _INVALID_MESSAGE, content_intact=False
        )
        return {}, [defect]
    return payload, _candidate_defects(payload, requirements, brief, configured_size)


def _validate_candidate(
    data: dict[str, Any],
    requirements: list[dict[str, Any]],
    brief: dict[str, Any],
    configured_size: str,
) -> dict[str, Any]:
    payload, defects = _validated_payload(data, requirements, brief, configured_size)
    if defects:
        raise _invalid_prompt()
    return payload


def _validate_review(
    output: RoleOutput,
    *,
    target_id: str,
    allowed_evidence: tuple[str, ...],
    ruleset: RuleSet | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Adjudicate one reviewer result against the server-owned S07 catalog.

    The catalog decides which rules are required and whether the reported
    outcomes are legal. A contract defect is a typed protocol rejection, never a
    service outage, and it can never open the gate.
    """
    if output.status != "ready" or not isinstance(output.data, dict):
        raise _invalid_review()
    results = output.data.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise _invalid_review()
    result = results[0]
    if not isinstance(result, dict):
        raise _invalid_review()
    checks = result.get("checks")
    if not isinstance(checks, list):
        raise _invalid_review()
    try:
        adjudication = adjudicate(
            ruleset or s07_rules(),
            target_id=target_id,
            result_target_id=result.get("target_id"),
            declared_decision=result.get("decision"),
            checks=checks,
            allowed_evidence=allowed_evidence,
        )
    except ProtocolIssue as exc:
        raise ApiError(409, "PROMPT_REVIEW_INVALID", exc.message) from None
    return target_id, adjudication.decision, adjudication.check_payloads()


async def _record_transition(
    ctx: WorkflowContext,
    reason: ReasonCode,
    action: str,
    *,
    attempt: int | None = None,
    intent_id: str | None = None,
    rule_ids: tuple[str, ...] = (),
    detail: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "reason": str(reason),
        "action": action,
        "ruleset_version": RULESET_VERSION,
    }
    if attempt is not None:
        payload["attempt"] = attempt
    if intent_id is not None:
        payload["intent_id"] = intent_id
    if rule_ids:
        payload["rule_ids"] = list(rule_ids)
    if detail:
        payload.update(detail)
    await state.record_diagnostic(ctx, "prompt_transition", payload)


async def _ensure_image_unsubmitted(
    ctx: WorkflowContext, intent: Any, *, allow_known_rejection: bool = False
) -> None:
    """Refuse automatic text repair unless the image is provably not submitted."""
    async with read_session(ctx.session_factory) as session:
        jobs = (
            await session.scalars(
                select(GenerationJob)
                .where(GenerationJob.run_id == ctx.run_id)
                .order_by(GenerationJob.created_at.desc())
            )
        ).all()
    if not jobs:
        return
    job = jobs[0]
    proven_unsubmitted = (
        job.submit_attempted == 0
        and job.status in {"created", "submit_pending"}
        and not job.remote_task_id
    )
    if allow_known_rejection and job.status in {"rejected", "failed"}:
        return
    if proven_unsubmitted:
        return
    await _record_transition(
        ctx,
        ReasonCode.REMOTE_UNKNOWN,
        "stop_auto_advance",
        intent_id=intent.id,
        detail={
            "job_id": job.id,
            "job_status": job.status,
            "submit_attempted": job.submit_attempted,
        },
    )
    await state.set_stage(
        ctx,
        STAGE,
        "blocked",
        issue_code="GENERATION_UNKNOWN",
        template_id="GENERATION_UNKNOWN",
    )
    raise ApiError(409, "GENERATION_UNKNOWN", templates.render("GENERATION_UNKNOWN"))


async def _previous_repair_codes(ctx: WorkflowContext) -> tuple[str, ...]:
    artifact = await state.latest_artifact(ctx, "prompt_plan")
    if artifact is None:
        return ()
    async with read_session(ctx.session_factory) as session:
        review = await session.scalar(
            select(ArtifactReview)
            .where(ArtifactReview.artifact_id == artifact.id)
            .order_by(ArtifactReview.created_at.desc())
        )
    if review is None:
        return ()
    try:
        stored = json.loads(review.checks_json or "{}")
    except json.JSONDecodeError:
        return ()
    checks = stored.get("checks") if isinstance(stored, dict) else None
    if not isinstance(checks, list):
        return ()
    codes: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        rule_id = check.get("rule_id")
        if isinstance(rule_id, str) and check.get("outcome") != "pass" and rule_id not in codes:
            codes.append(rule_id)
    return tuple(codes)


async def _reconfirm_prompt(
    ctx: WorkflowContext,
    intent: Any,
    reasons: list[str],
    *,
    reason: ReasonCode = ReasonCode.TRUE_DRIFT,
) -> None:
    await _record_transition(
        ctx,
        reason,
        "reconfirm_requirements",
        intent_id=intent.id,
        detail={"reasons": reasons[:10]},
    )
    abandoned = await abandon_intent(
        ctx.session_factory,
        intent_id=intent.id,
        reason="prompt_requires_reconfirmation",
    )
    await state.set_stage(
        ctx,
        STAGE,
        "blocked",
        issue_code="PROMPT_REQUIRES_RECONFIRMATION",
        template_id="PROMPT_REQUIRES_RECONFIRMATION",
    )
    await state.record_diagnostic(
        ctx,
        "prompt_requires_reconfirmation",
        {
            "intent_id": intent.id,
            "abandoned": abandoned,
            "reason_code": str(reason),
            "reasons": reasons[:10],
        },
    )
    raise ApiError(409, "PROMPT_REQUIRES_RECONFIRMATION", _RECONFIRM_MESSAGE)


async def _compile(
    ctx: WorkflowContext,
    revision_note: str | None,
    *,
    advance: bool = True,
    allow_known_rejection: bool = False,
) -> None:
    intent = None
    if ctx.project.owner_account_id is not None:
        intent = await latest_intent(
            ctx.session_factory, project_id=ctx.project_id, run_id=ctx.run_id
        )
    await _ensure_budget(ctx)
    repair_codes = await _previous_repair_codes(ctx) if revision_note else ()
    while True:
        # Re-read the original snapshot, confirmation and dependencies before
        # every text attempt. A change here is drift, never an auto repair.
        ctx = await state.reload_context(ctx)
        try:
            final_artifact, brief = await _require_confirmed(ctx)
        except ApiError as exc:
            await _record_transition(
                ctx,
                classify_api_error(exc.code),
                "precondition_failed",
                intent_id=intent.id if intent is not None else None,
                detail={"error_code": exc.code},
            )
            raise
        if ctx.project.owner_account_id is not None:
            intent = await latest_intent(
                ctx.session_factory, project_id=ctx.project_id, run_id=ctx.run_id
            )
        if intent is not None:
            await _ensure_image_unsubmitted(
                ctx, intent, allow_known_rejection=allow_known_rejection
            )
        requirements = _hard_requirements(brief)
        configured_size = (
            ctx.project.image_size or ctx.settings.visual().image_size or ""
        )
        reservation = await state.reserve_artifact_revision(ctx, "prompt_plan")
        await state.record_diagnostic(
            ctx,
            "prompt_candidate_reserved",
            {
                "attempt": reservation.revision_no,
                "budget_used": reservation.used,
                "budget_max": reservation.max_allowed,
                "ruleset_version": RULESET_VERSION,
                "intent_id": intent.id if intent is not None else None,
                "repair_codes": list(repair_codes),
            },
        )
        input_data = {
            "final_brief_artifact_id": final_artifact.id,
            "confirmed_revision": ctx.project.revision,
            "hard_requirements": requirements,
            "revision_note": revision_note or "",
        }
        try:
            output = await run_role(
                ctx,
                "generation_engineer",
                STAGE,
                input_data,
                approved_inputs={
                    "final_brief": brief,
                    "final_brief_artifact_id": final_artifact.id,
                    "reference_asset_id": brief.get("reference_asset_id"),
                },
                repair_codes=list(repair_codes),
                attempt=reservation.revision_no,
                required_rule_ids=tuple(item["rule_id"] for item in requirements),
                allowed_evidence_ids=(final_artifact.id,),
            )
        except ApiError as exc:
            reason = classify_api_error(exc.code)
            await _record_transition(
                ctx,
                reason,
                "producer_failed",
                attempt=reservation.revision_no,
                intent_id=intent.id if intent is not None else None,
                detail={"error_code": exc.code},
            )
            if exc.code in {"SERVICE_UNAVAILABLE", "CAPABILITY_NOT_READY"}:
                await _mark_failed(
                    ctx,
                    "SERVICE_UNAVAILABLE"
                    if exc.code == "SERVICE_UNAVAILABLE"
                    else "CAPABILITY_NOT_READY",
                )
            raise

        if output.status == "blocked":
            issue_code = "PROMPT_BLOCKED"
            for issue in output.issues:
                if isinstance(issue, dict) and issue.get("code"):
                    issue_code = str(issue["code"])
                    break
            reason = classify_issue_code(issue_code)
            await _record_transition(
                ctx,
                reason,
                "producer_blocked",
                attempt=reservation.revision_no,
                intent_id=intent.id if intent is not None else None,
                detail={"issue_code": issue_code},
            )
            await state.set_stage(
                ctx,
                STAGE,
                "blocked",
                issue_code=issue_code,
                template_id="CONFIRM_REQUIRED",
            )
            raise ApiError(409, "PROMPT_BLOCKED", _CONFIRM_MESSAGE)
        if output.status == "needs_input":
            issue_codes = {
                str(issue.get("code"))
                for issue in output.issues
                if isinstance(issue, dict)
            }
            reason = classify_issue_code(
                "REQUIREMENT_DRIFT" if "REQUIREMENT_DRIFT" in issue_codes else None
            )
            await _record_transition(
                ctx,
                reason,
                "producer_needs_input",
                attempt=reservation.revision_no,
                intent_id=intent.id if intent is not None else None,
                detail={"issue_codes": sorted(issue_codes)},
            )
            if "REQUIREMENT_DRIFT" in issue_codes:
                await state.invalidate_requirements(ctx, "提示词编制发现需求变化")
                await state.set_stage(
                    ctx,
                    STAGE,
                    "needs_input",
                    issue_code="REQUIREMENT_DRIFT",
                    template_id="RUN_SUPERSEDED",
                )
            raise _drift_error()
        if output.status != "ready" or output.data is None:
            await _record_transition(
                ctx,
                ReasonCode.PROTOCOL_ERROR,
                "producer_invalid_status",
                attempt=reservation.revision_no,
                intent_id=intent.id if intent is not None else None,
                detail={"status": output.status},
            )
            raise _invalid_prompt()

        data = output.data if isinstance(output.data, dict) else {}
        payload, defects = _validated_payload(
            data, requirements, brief, configured_size
        )
        if defects:
            reason = classify_candidate_defects(defects)
            rule_ids = repair_codes_from_defects(defects)
            await _record_transition(
                ctx,
                reason,
                "candidate_invalid",
                attempt=reservation.revision_no,
                intent_id=intent.id if intent is not None else None,
                rule_ids=rule_ids,
                detail={
                    "defects": [
                        {
                            "code": str(defect.code),
                            "rule_id": defect.rule_id,
                            "content_intact": defect.content_intact,
                        }
                        for defect in defects
                    ]
                },
            )
            if (
                reason == ReasonCode.REPAIRABLE_COMPOSITION
                and is_auto_repairable(defects)
                and reservation.revision_no < reservation.max_allowed
            ):
                repair_codes = rule_ids
                await _record_transition(
                    ctx,
                    ReasonCode.REPAIRABLE_COMPOSITION,
                    "auto_repair",
                    attempt=reservation.revision_no,
                    intent_id=intent.id if intent is not None else None,
                    rule_ids=rule_ids,
                )
                continue
            if reason == ReasonCode.TRUE_DRIFT and intent is not None:
                await _reconfirm_prompt(
                    ctx,
                    intent,
                    [defect.message for defect in defects],
                    reason=reason,
                )
            raise _invalid_prompt()

        if intent is not None:
            drift = prompt_drift_errors(brief, payload)
            if drift:
                await _reconfirm_prompt(ctx, intent, drift, reason=ReasonCode.TRUE_DRIFT)

        previous = await state.latest_artifact(ctx, "prompt_plan")
        if previous is not None and previous.status == "candidate":
            await state.set_artifact_status(ctx, previous.id, "superseded")
        artifact = await state.start_artifact(
            ctx, "prompt_plan", payload, revision_no=reservation.revision_no
        )
        await state.record_diagnostic(
            ctx,
            "prompt_candidate_persisted",
            {
                "artifact_id": artifact.id,
                "attempt": artifact.revision_no,
                "budget_used": reservation.used,
                "budget_max": reservation.max_allowed,
                "ruleset_version": RULESET_VERSION,
                "intent_id": intent.id if intent is not None else None,
            },
        )

        ruleset = s07_rules(
            echo_rules=tuple(item["rule_id"] for item in requirements)
        )
        try:
            review_output = await run_role(
                ctx,
                "output_reviewer",
                STAGE,
                {
                    "reviewed_role": "generation_engineer",
                    "target_kind": "prompt_plan",
                    "hard_requirements": requirements,
                    "ruleset_version": ruleset.version,
                    "rules": ruleset.describe(),
                },
                approved_inputs={"final_brief": brief},
                review_targets=[
                    {
                        "target_id": artifact.id,
                        "target_kind": "prompt_plan",
                        "payload": payload,
                    }
                ],
                required_rule_ids=tuple(rule.rule_id for rule in ruleset.blocking()),
                allowed_evidence_ids=(artifact.id, final_artifact.id),
            )
        except ApiError as exc:
            await _record_transition(
                ctx,
                ReasonCode.TECHNICAL_FAILURE,
                "reviewer_failed",
                attempt=artifact.revision_no,
                intent_id=intent.id if intent is not None else None,
                detail={"error_code": exc.code},
            )
            await _mark_failed(ctx, "REVIEW_UNAVAILABLE")
            raise

        # A reviewer contract defect is a typed protocol rejection: the run is
        # not marked failed, the candidate stays invisible and the gate stays closed.
        try:
            _target_id, decision, checks = _validate_review(
                review_output,
                target_id=artifact.id,
                allowed_evidence=(artifact.id, final_artifact.id),
                ruleset=ruleset,
            )
        except ApiError as exc:
            await _record_transition(
                ctx,
                ReasonCode.PROTOCOL_ERROR,
                "review_invalid",
                attempt=artifact.revision_no,
                intent_id=intent.id if intent is not None else None,
                detail={"error_code": exc.code},
            )
            raise

        review_checks = {"ruleset_version": RULESET_VERSION, "checks": checks}
        await state.add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision=decision,
            checks=review_checks,
            raw=review_output.data,
        )
        if decision != "pass":
            reason = classify_review_decision(decision) or ReasonCode.PROTOCOL_ERROR
            await _record_transition(
                ctx,
                reason,
                "review_not_passed",
                attempt=artifact.revision_no,
                intent_id=intent.id if intent is not None else None,
            )
            raise ApiError(409, "PROMPT_REVIEW_REJECTED", _REVIEW_REJECTED_MESSAGE)

        approved = await state.latest_artifact(
            ctx, "prompt_plan", status="approved"
        )
        if approved is None:
            raise _invalid_review()
        await state.add_approval(
            ctx,
            approval_type="generation_prompt",
            artifact=approved,
            approved_by="output_reviewer",
            checks=review_checks,
            model_fingerprint=review_output.model_fingerprint,
        )
        await state.record_diagnostic(
            ctx,
            "prompt_candidate_approved",
            {
                "artifact_id": artifact.id,
                "attempt": artifact.revision_no,
                "repair_codes": list(repair_codes),
                "ruleset_version": RULESET_VERSION,
                "intent_id": intent.id if intent is not None else None,
            },
        )
        if advance:
            await state.set_stage(ctx, "S08", "active")
        return


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if action == "revise_prompt":
        note = payload.get("text")
        if not isinstance(note, str) or not note.strip():
            raise ApiError(422, "VALIDATION_ERROR", "请说明提示词调整内容")
        note = note.strip()[:20000]
    else:
        note = None
    await _compile(ctx, note)


__all__ = ["STAGE", "ACTIONS", "available", "handle"]

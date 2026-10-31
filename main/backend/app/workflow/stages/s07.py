from __future__ import annotations

import json
from typing import Any

from app.acceptance import abandon_intent, latest_intent
from app.db import read_session
from app.errors import ApiError, conflict
from app.models import Asset
from app.prompt_guard import prompt_drift_errors
from app.workflow import state, templates
from app.workflow.contracts import ContractError, validate_data
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import WorkflowContext
from app.workflow.tables import RevisionBudget

STAGE = "S07"
ACTIONS = ["compile_prompt", "revise_prompt"]
_REVIEW_RULES = ("G03_CONFIRMATION", "G05_EXECUTION_BOUNDARY")

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
    raise _confirmed_error()


async def _require_confirmed(ctx: WorkflowContext) -> tuple[Any, dict[str, Any]]:
    final_artifact = await state.approved_artifact(ctx, "final_brief")
    approval = await state.latest_approval(ctx, "requirements_card")
    if final_artifact is None or approval is None:
        raise _confirmed_error()
    if ctx.project.brief_status != "confirmed":
        raise _confirmed_error()
    if ctx.project.confirmed_revision != ctx.project.revision:
        raise _confirmed_error()
    if ctx.run is None or ctx.run.requirement_revision != ctx.project.revision:
        raise _confirmed_error()
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


def _candidate_errors(
    payload: dict[str, Any],
    requirements: list[dict[str, Any]],
    brief: dict[str, Any],
    configured_size: str,
) -> list[str]:
    errors: list[str] = []
    expected = {item["rule_id"]: item for item in requirements}
    coverage = payload.get("requirement_coverage") or []
    seen: set[str] = set()
    for item in coverage:
        rule_id = item.get("rule_id")
        if rule_id in seen:
            errors.append(f"重复覆盖：{rule_id}")
            continue
        seen.add(rule_id)
        requirement = expected.get(rule_id)
        if requirement is None:
            errors.append(f"未知覆盖项：{rule_id}")
            continue
        binding = item.get("binding")
        if binding not in requirement["allowed_bindings"]:
            errors.append(f"覆盖绑定不匹配：{rule_id}")
            continue
        value = item.get("value")
        if binding in {"positive_prompt", "negative_prompt"}:
            field_value = payload.get(binding) or ""
            if not value or value != requirement["requirement"] or value not in field_value:
                errors.append(f"提示词覆盖值与已确认要求不一致：{rule_id}")
        elif binding == "size":
            if value != configured_size:
                errors.append("尺寸覆盖值不一致")
        elif binding == "count":
            if value != "1":
                errors.append("数量覆盖值不一致")
        elif binding == "reference_asset_id":
            expected_reference = brief.get("reference_asset_id") or "null"
            if value != expected_reference:
                errors.append("参考图覆盖值不一致")
    for rule_id in expected:
        if rule_id not in seen:
            errors.append(f"缺少覆盖：{rule_id}")
    if payload.get("size") != configured_size:
        errors.append("提交尺寸与已配置尺寸不一致")
    if payload.get("count") != 1:
        errors.append("数量必须为 1")
    if payload.get("reference_asset_id") != brief.get("reference_asset_id"):
        errors.append("参考图 ID 与确认快照不一致")
    return errors


def _validate_candidate(
    data: dict[str, Any],
    requirements: list[dict[str, Any]],
    brief: dict[str, Any],
    configured_size: str,
) -> dict[str, Any]:
    try:
        payload = validate_data("prompt_plan", data).model_dump(mode="json")
    except ContractError:
        raise _invalid_prompt() from None
    errors = _candidate_errors(payload, requirements, brief, configured_size)
    if errors:
        raise _invalid_prompt()
    return payload


def _validate_review(
    output: RoleOutput,
    *,
    target_id: str,
    allowed_evidence: tuple[str, ...],
) -> tuple[str, str, list[dict[str, Any]]]:
    if output.status != "ready" or not isinstance(output.data, dict):
        raise _invalid_review()
    results = output.data.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise _invalid_review()
    result = results[0]
    if not isinstance(result, dict) or result.get("target_id") != target_id:
        raise _invalid_review()
    decision = result.get("decision")
    if decision not in {"pass", "revise", "reject", "unknown"}:
        raise _invalid_review()
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks:
        raise _invalid_review()
    seen: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            raise _invalid_review()
        rule_id = check.get("rule_id")
        if rule_id not in _REVIEW_RULES or rule_id in seen:
            raise _invalid_review()
        seen.add(rule_id)
        evidence = check.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence:
            raise _invalid_review()
        if any(item not in allowed_evidence for item in evidence):
            raise _invalid_review()
        note = check.get("note") or ""
        if "://" in note:
            raise _invalid_review()
    if seen != set(_REVIEW_RULES):
        raise _invalid_review()
    if decision == "pass" and any(check.get("outcome") != "pass" for check in checks):
        raise _invalid_review()
    return target_id, decision, checks


async def _reconfirm_prompt(
    ctx: WorkflowContext, intent: Any, reasons: list[str]
) -> None:
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
        {"intent_id": intent.id, "abandoned": abandoned, "reasons": reasons[:10]},
    )
    raise ApiError(409, "PROMPT_REQUIRES_RECONFIRMATION", _RECONFIRM_MESSAGE)


async def _compile(ctx: WorkflowContext, revision_note: str | None) -> None:
    final_artifact, brief = await _require_confirmed(ctx)
    intent = None
    if ctx.project.owner_account_id is not None:
        intent = await latest_intent(
            ctx.session_factory, project_id=ctx.project_id, run_id=ctx.run_id
        )
    await _ensure_budget(ctx)
    requirements = _hard_requirements(brief)
    configured_size = ctx.project.image_size or ctx.settings.visual().image_size or ""
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
            required_rule_ids=tuple(item["rule_id"] for item in requirements),
            allowed_evidence_ids=(final_artifact.id,),
        )
    except ApiError as exc:
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
        await state.set_stage(
            ctx, STAGE, "blocked", issue_code=issue_code, template_id="CONFIRM_REQUIRED"
        )
        raise ApiError(409, "PROMPT_BLOCKED", _CONFIRM_MESSAGE)
    if output.status == "needs_input":
        issue_codes = {
            str(issue.get("code"))
            for issue in output.issues
            if isinstance(issue, dict)
        }
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
        raise _invalid_prompt()

    try:
        payload = _validate_candidate(output.data, requirements, brief, configured_size)
    except ApiError as exc:
        if intent is None or exc.code != "PROMPT_INVALID" or not isinstance(output.data, dict):
            raise
        try:
            candidate_errors = _candidate_errors(
                output.data, requirements, brief, configured_size
            )
        except Exception:
            raise exc from None
        if not any(
            "覆盖值" in reason or "缺少字面片段" in reason
            for reason in candidate_errors
        ):
            raise
        await _reconfirm_prompt(ctx, intent, candidate_errors)
    if intent is not None:
        drift = prompt_drift_errors(brief, payload)
        if drift:
            await _reconfirm_prompt(ctx, intent, drift)
    previous = await state.latest_artifact(ctx, "prompt_plan")
    if previous is not None and previous.status == "candidate":
        await state.set_artifact_status(ctx, previous.id, "superseded")
    artifact = await state.start_artifact(ctx, "prompt_plan", payload)

    try:
        review_output = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": "generation_engineer",
                "target_kind": "prompt_plan",
                "hard_requirements": requirements,
            },
            approved_inputs={"final_brief": brief},
            review_targets=[
                {
                    "target_id": artifact.id,
                    "target_kind": "prompt_plan",
                    "payload": payload,
                }
            ],
            required_rule_ids=_REVIEW_RULES,
            allowed_evidence_ids=(artifact.id, final_artifact.id),
        )
    except ApiError:
        await _mark_failed(ctx, "REVIEW_UNAVAILABLE")
        raise

    try:
        _target_id, decision, checks = _validate_review(
            review_output,
            target_id=artifact.id,
            allowed_evidence=(artifact.id, final_artifact.id),
        )
    except ApiError:
        await _mark_failed(ctx, "REVIEW_UNAVAILABLE")
        raise

    await state.add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision=decision,
        checks={"checks": checks},
        raw=review_output.data,
    )
    if decision != "pass":
        raise ApiError(409, "PROMPT_REVIEW_REJECTED", _REVIEW_REJECTED_MESSAGE)

    approved = await state.latest_artifact(ctx, "prompt_plan", status="approved")
    if approved is None:
        raise _invalid_review()
    await state.add_approval(
        ctx,
        approval_type="generation_prompt",
        artifact=approved,
        approved_by="output_reviewer",
        checks={"checks": checks},
        model_fingerprint=review_output.model_fingerprint,
    )
    await state.set_stage(ctx, "S08", "active")


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

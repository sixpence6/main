from __future__ import annotations

import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.db import read_session, write_transaction
from app.errors import ApiError, conflict, validation_error
from app.models import Message, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
from app.workflow.contracts import ContractError, digest_payload, validate_data
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import (
    WorkflowContext,
    add_review,
    approved_artifact,
    latest_artifact,
    set_artifact_status,
    set_stage,
    start_artifact,
)
from app.workflow.tables import Artifact, ArtifactReview, RevisionBudget

STAGE = "S04"
NEXT_STAGE = "S05"
FALLBACK_STAGE = "S03"

ACTIONS = [
    "plan_creative",
    "revise_creative",
]

# The reviewer must attest every program-owned creative rule. A pass with a
# missing, duplicate, extra, failed or unknown check never approves the
# candidate and never opens S05.
REQUIRED_RULES = (
    "G03_SOURCE",
    "G03_COPY",
    "G03_REQUIREMENTS",
    "G03_SINGLE_DIRECTION",
)

# Shared reviewer prompts also emit general governance checks. These are
# advisory only: the program-owned REQUIRED_RULES above still decide whether a
# candidate can be approved.
_ADVISORY_RULE_RE = re.compile(
    r"^(?:G\d{2}_[A-Z0-9_]+|review:[a-z][a-z0-9_]{0,63}|"
    r"[A-Z][A-Z0-9_]{1,63})$"
)

_SAFE_HANDOFF_TEMPLATE = "ACTION_UNAVAILABLE"
_SCOPE_CODES = {"OUT_OF_SCOPE", "MIXED_SCOPE", "UNSAFE_REQUEST"}

# Claims that would turn a creative proposal into an unsourced product fact.
# They are allowed only when the approved brief itself already contains the
# exact token; otherwise the candidate must go back to the requirements stage.
_UNSUPPORTED_CLAIM_PATTERNS = (
    re.compile(r"销量"),
    re.compile(r"认证"),
    re.compile(r"获奖"),
    re.compile(r"国家级"),
    re.compile(r"第一品牌"),
    re.compile(r"最低价"),
    re.compile(r"特价"),
    re.compile(r"限时"),
    re.compile(r"包邮"),
    re.compile(r"赠品"),
    re.compile(r"\d+(?:\.\d+)?\s*(?:元|折|%)"),
)


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    """Program-owned action menu for the creative stage.

    The model never decides which action is legal. After a review requests a
    bounded revision the only possible action is revise_creative; after a
    hard reject or an unknown review the candidate path stays closed.
    """
    if ctx.run is None or ctx.run.stage != STAGE:
        return []
    if ctx.run.status in {"active", "failed"}:
        return ["plan_creative"]
    if ctx.run.status in {"needs_input", "blocked"} and ctx.run.issue_code in {
        "REVIEW_REVISE",
        "REVIEW_UNKNOWN",
    }:
        return ["revise_creative"]
    return []


def validate_text(payload: dict[str, Any]) -> str:
    value = payload.get("text")
    if not isinstance(value, str) or not value.strip():
        raise validation_error("修改说明不能为空")
    if len(value) > 20_000:
        raise validation_error("修改说明过长")
    return value


def _fixed_service(error: ApiError | None = None) -> ApiError:
    if error is not None and error.code == "CAPABILITY_NOT_READY":
        return error
    return ApiError(503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE"))


def _fixed_review() -> ApiError:
    return ApiError(503, "REVIEW_UNAVAILABLE", templates.render("REVIEW_UNAVAILABLE"))


def _business_reject() -> ApiError:
    return conflict(
        "REQUIREMENT_DRIFT",
        "创意方向与已批准需求不一致，未通过业务校验",
    )


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for item in value.values():
            result.extend(_flatten_strings(item))
        return result
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_flatten_strings(item))
        return result
    return []


def _brief_corpus(brief: dict[str, Any]) -> str:
    return _normalize("\n".join(_flatten_strings(brief)))


def _fact_evidence(brief: dict[str, Any]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for fact in brief.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        text = fact.get("text")
        if not isinstance(text, str):
            continue
        for evidence_id in fact.get("evidence_ids") or []:
            if isinstance(evidence_id, str) and evidence_id:
                mapping.setdefault(evidence_id, []).append(text)
    return mapping


def _copy_creation_authorized(brief: dict[str, Any]) -> bool:
    """Explicit user authorization recorded by the approved requirements draft.

    The requirements consultant records an explicit authorization in
    assumptions when the user asked for new copy without supplying the exact
    words. Without such an approved sentence the safe default is no screen
    text at all; the creative role may not invent copy on its own.
    """
    for item in brief.get("assumptions") or []:
        if not isinstance(item, str):
            continue
        if "文案" in item and ("授权" in item or "创作" in item):
            return True
    return False


def _reject_external_content(text: str) -> None:
    lowered = text.casefold()
    if any(marker in lowered for marker in ("http://", "https://", "www.", "data:", "<script")):
        raise _business_reject()


def _validate_creative(brief: dict[str, Any], data: dict[str, Any]) -> None:
    """Service-side source, copy and hard-fact checks for creative data."""
    direction = data.get("direction")
    selling_points = data.get("selling_points")
    copy_lines = data.get("copy_lines")
    if not isinstance(direction, str) or not direction.strip():
        raise _business_reject()
    if not isinstance(selling_points, list) or not isinstance(copy_lines, list):
        raise _business_reject()

    _reject_external_content(direction)
    for line in copy_lines:
        if not isinstance(line, str) or not line.strip():
            raise _business_reject()
        _reject_external_content(line)

    # Copy policy: exact requested text is preserved verbatim; no copy when
    # the approved draft has no text authorization; new copy only when the
    # approved draft records an explicit creation authorization.
    approved_texts = [item for item in (brief.get("texts") or []) if isinstance(item, str)]
    if approved_texts:
        if copy_lines != approved_texts:
            raise _business_reject()
    elif copy_lines and not _copy_creation_authorized(brief):
        raise _business_reject()

    evidence = _fact_evidence(brief)
    for point in selling_points:
        if not isinstance(point, dict):
            raise _business_reject()
        text = point.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _business_reject()
        _reject_external_content(text)
        ids = point.get("evidence_ids")
        if not isinstance(ids, list) or not ids:
            raise _business_reject()
        cited: list[str] = []
        for evidence_id in ids:
            if not isinstance(evidence_id, str) or evidence_id not in evidence:
                raise _business_reject()
            cited.extend(evidence[evidence_id])
        normalized_claim = _normalize(text)
        if not any(normalized_claim in _normalize(fact_text) for fact_text in cited):
            raise _business_reject()

    corpus = _brief_corpus(brief)
    claims = [direction]
    claims.extend(copy_lines)
    claims.extend(point["text"] for point in selling_points)
    for text in claims:
        for pattern in _UNSUPPORTED_CLAIM_PATTERNS:
            for match in pattern.finditer(text):
                if _normalize(match.group(0)) not in corpus:
                    raise _business_reject()


async def _require_approved_brief(ctx: WorkflowContext) -> tuple[Artifact, dict[str, Any]]:
    """Return the current approved requirements draft or a safe conflict.

    An approved older revision never overrides a newer draft: the latest
    brief_draft artifact of the run must be the approved one. A passing
    independent review and a matching content digest are both required.
    """
    artifact = await approved_artifact(ctx, "brief_draft")
    if artifact is None:
        raise conflict("ARTIFACT_NOT_APPROVED", "所需产物尚未通过审查")
    latest = await latest_artifact(ctx, "brief_draft")
    if latest is None or latest.id != artifact.id:
        raise conflict("ARTIFACT_STALE", "需求草稿已有新版本，旧版本不能继续进入创意")
    try:
        data = validate_data("brief_draft", _json_dict(artifact.payload_json)).model_dump(
            mode="json"
        )
    except ContractError:
        raise conflict("ARTIFACT_STALE", "需求草稿内容无效，请重新整理") from None
    if digest_payload(data) != artifact.digest:
        raise conflict("ARTIFACT_STALE", "需求草稿内容与批准摘要不一致")
    async with read_session(ctx.session_factory) as session:
        review = await session.scalar(
            select(ArtifactReview)
            .where(
                ArtifactReview.artifact_id == artifact.id,
                ArtifactReview.reviewer_role == "output_reviewer",
                ArtifactReview.decision == "pass",
            )
            .order_by(ArtifactReview.created_at.desc())
            .limit(1)
        )
    if review is None:
        raise conflict("ARTIFACT_NOT_APPROVED", "需求草稿尚未通过独立审查")
    return artifact, data


async def _ensure_budget(ctx: WorkflowContext) -> None:
    async with read_session(ctx.session_factory) as session:
        budget = await session.get(RevisionBudget, (ctx.run_id, "creative_direction"))
    if budget is not None and budget.used >= budget.max_allowed:
        raise conflict(
            "REVISION_BUDGET_EXHAUSTED",
            "该产物已达到最多三次候选的上限，请保留当前结果或重新开始流程",
        )


async def _latest_review(ctx: WorkflowContext, artifact_id: str) -> ArtifactReview | None:
    async with read_session(ctx.session_factory) as session:
        return await session.scalar(
            select(ArtifactReview)
            .where(ArtifactReview.artifact_id == artifact_id)
            .order_by(ArtifactReview.created_at.desc())
            .limit(1)
        )


async def _previous_candidate(ctx: WorkflowContext) -> Artifact | None:
    return await latest_artifact(ctx, "creative_direction")


async def _mark_superseded(ctx: WorkflowContext, artifact: Artifact | None) -> None:
    if artifact is None or artifact.status != "candidate":
        return
    await set_artifact_status(ctx, artifact.id, "superseded")


async def _record_revision_text(ctx: WorkflowContext, text: str) -> None:
    """Persist the exact user revision note and advance the project revision."""
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        if project is None:
            raise conflict("PROJECT_NOT_FOUND", "项目不存在")
        project.revision += 1
        project.updated_at = now
        highest = await session.scalar(
            select(func.max(Message.sequence_no)).where(Message.project_id == ctx.project_id)
        )
        session.add(
            Message(
                id=str(uuid4()),
                project_id=ctx.project_id,
                sequence_no=(highest or 0) + 1,
                role="user",
                content=text,
                project_revision=project.revision,
                brief_snapshot_json=None,
                operation_id=None,
                created_at=now,
            )
        )
        await session.flush()
    ctx.project.revision = project.revision


def _creative_input(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "approved_brief": brief,
        "copy_policy": {
            "approved_texts": list(brief.get("texts") or []),
            "copy_creation_authorized": _copy_creation_authorized(brief),
        },
        "hard_constraints": {
            "must_include": list(brief.get("must_include") or []),
            "must_preserve": list(brief.get("must_preserve") or []),
            "forbidden": list(brief.get("forbidden") or []),
            "reference_usage": brief.get("reference_usage"),
        },
    }


def _review_input_data() -> dict[str, Any]:
    return {"reviewed_role": "creative_planner", "target_kind": "creative_direction"}


def _review_targets(
    artifact: Artifact, brief: dict[str, Any], data: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        {
            "target_id": artifact.id,
            "target_kind": "creative_direction",
            "artifact_kind": "creative_direction",
            "candidate": data,
            "approved_brief": brief,
        }
    ]


def _review_evidence_ids(brief: dict[str, Any], artifact: Artifact) -> tuple[str, ...]:
    return tuple(_fact_evidence(brief)) + (artifact.id,)


def _validate_review_shape(
    output: RoleOutput, target_id: str, allowed_evidence: set[str]
) -> tuple[str, list[dict[str, Any]]]:
    if output.status != "ready" or output.data is None:
        raise _fixed_review()
    results = output.data.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise _fixed_review()
    result = results[0]
    if not isinstance(result, dict) or result.get("target_id") != target_id:
        raise _fixed_review()
    decision = result.get("decision")
    if decision not in {"pass", "revise", "reject", "unknown"}:
        raise _fixed_review()
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks:
        raise _fixed_review()

    required_seen: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            raise _fixed_review()
        rule_id = check.get("rule_id")
        if not isinstance(rule_id, str):
            raise _fixed_review()
        evidence_ids = check.get("evidence_ids")
        if not isinstance(evidence_ids, list):
            raise _fixed_review()
        if any(not isinstance(item, str) or item not in allowed_evidence for item in evidence_ids):
            raise _fixed_review()
        note = check.get("note")
        if isinstance(note, str) and "://" in note.casefold():
            raise _fixed_review()
        outcome = check.get("outcome")
        if rule_id in REQUIRED_RULES:
            if outcome not in {"pass", "fail", "unknown"}:
                raise _fixed_review()
            if not evidence_ids:
                raise _fixed_review()
            required_seen.add(rule_id)
        else:
            if not _ADVISORY_RULE_RE.fullmatch(rule_id):
                raise _fixed_review()
            if outcome not in {"pass", "fail", "unknown", "not_applicable"}:
                raise _fixed_review()

    if decision == "pass":
        if required_seen != set(REQUIRED_RULES):
            raise _fixed_review()
        for check in checks:
            rule_id = str(check.get("rule_id"))
            outcome = check.get("outcome")
            if rule_id in REQUIRED_RULES and outcome != "pass":
                raise _fixed_review()
            if rule_id not in REQUIRED_RULES and outcome in {"fail", "unknown"}:
                raise _fixed_review()
    return str(decision), checks


async def _review_creative(
    ctx: WorkflowContext,
    brief: dict[str, Any],
    artifact: Artifact,
    data: dict[str, Any],
) -> None:
    allowed_evidence = set(_review_evidence_ids(brief, artifact))
    try:
        review = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            _review_input_data(),
            approved_inputs={"brief_draft": brief},
            review_targets=_review_targets(artifact, brief, data),
            required_rule_ids=REQUIRED_RULES,
            allowed_evidence_ids=tuple(sorted(allowed_evidence)),
        )
        decision, checks = _validate_review_shape(review, artifact.id, allowed_evidence)
    except ApiError:
        await set_stage(
            ctx,
            STAGE,
            "blocked",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise _fixed_review() from None

    # An older candidate must never overwrite a newer creative revision that
    # arrived while this review was running.
    latest = await latest_artifact(ctx, "creative_direction")
    if latest is None or latest.id != artifact.id:
        await _mark_superseded(ctx, artifact)
        raise conflict("ARTIFACT_STALE", "创意方向已有新版本，旧候选不再放行")

    if decision == "pass":
        await add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision="pass",
            checks={"checks": checks, "rule_ids": list(REQUIRED_RULES)},
        )
        await set_stage(ctx, NEXT_STAGE, "active")
        return

    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision=decision,
        checks={"checks": checks},
    )
    if decision == "revise":
        await set_stage(
            ctx,
            STAGE,
            "blocked",
            issue_code="REVIEW_REVISE",
            template_id=_SAFE_HANDOFF_TEMPLATE,
        )
    elif decision == "unknown":
        await set_stage(
            ctx,
            STAGE,
            "blocked",
            issue_code="REVIEW_UNKNOWN",
            template_id="REVIEW_UNAVAILABLE",
        )
    else:
        await set_stage(
            ctx,
            STAGE,
            "blocked",
            issue_code="REVIEW_REJECTED",
            template_id=_SAFE_HANDOFF_TEMPLATE,
        )


async def _handle_non_ready(ctx: WorkflowContext, output: RoleOutput) -> None:
    codes = [
        str(issue.get("code"))
        for issue in output.issues
        if isinstance(issue, dict) and issue.get("code")
    ]
    code = codes[0] if codes else "CONTRACT_ERROR"
    if output.status == "needs_input":
        # Only the requirements consultant may ask the user free questions.
        # The creative role hands the enumerated issue back to S03.
        await set_stage(
            ctx,
            FALLBACK_STAGE,
            "needs_input",
            issue_code=code,
            template_id=_SAFE_HANDOFF_TEMPLATE,
        )
        return
    template_id = "SCOPE_REFUSAL" if code in _SCOPE_CODES else _SAFE_HANDOFF_TEMPLATE
    await set_stage(ctx, STAGE, "blocked", issue_code=code, template_id=template_id)


async def _run_creative(
    ctx: WorkflowContext,
    brief: dict[str, Any],
    *,
    revision_note: str | None = None,
    previous: Artifact | None = None,
    repair_codes: list[str] | None = None,
) -> None:
    await _ensure_budget(ctx)
    input_data = _creative_input(brief)
    if revision_note is not None:
        input_data["revision_note"] = revision_note
    if previous is not None:
        input_data["previous_candidate"] = _json_dict(previous.payload_json)

    try:
        output = await run_role(
            ctx,
            "creative_planner",
            STAGE,
            input_data,
            approved_inputs={"brief_draft": brief},
            repair_codes=repair_codes or [],
            required_rule_ids=REQUIRED_RULES,
            allowed_evidence_ids=tuple(_fact_evidence(brief)),
        )
    except ApiError as error:
        if error.code == "CAPABILITY_NOT_READY":
            template_id = "CAPABILITY_NOT_READY"
        else:
            template_id = "SERVICE_UNAVAILABLE"
        await set_stage(
            ctx,
            STAGE,
            "failed",
            issue_code=template_id,
            template_id=template_id,
        )
        raise _fixed_service(error) from None

    if output.status != "ready":
        await _handle_non_ready(ctx, output)
        return

    if output.data is None:
        raise _fixed_service()
    try:
        _validate_creative(brief, output.data)
    except ApiError:
        # The model produced a contract-valid but business-invalid candidate.
        # Nothing is persisted and the candidate is never shown.
        await set_stage(
            ctx,
            STAGE,
            "blocked",
            issue_code="REQUIREMENT_DRIFT",
            template_id=_SAFE_HANDOFF_TEMPLATE,
        )
        raise

    artifact = await start_artifact(ctx, "creative_direction", output.data)
    await _mark_superseded(ctx, previous)
    await _review_creative(ctx, brief, artifact, output.data)


async def _plan(ctx: WorkflowContext) -> None:
    _brief_artifact, brief = await _require_approved_brief(ctx)
    existing = await approved_artifact(ctx, "creative_direction")
    if existing is not None:
        # Idempotent continuation: never generate a second unconfirmed
        # direction when one is already approved.
        await set_stage(ctx, NEXT_STAGE, "active")
        return
    previous = await _previous_candidate(ctx)
    await _run_creative(ctx, brief, previous=previous)


async def _revise(ctx: WorkflowContext, text: str) -> None:
    _brief_artifact, brief = await _require_approved_brief(ctx)
    previous = await _previous_candidate(ctx)
    if previous is None:
        raise conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    review = await _latest_review(ctx, previous.id)
    if review is None or review.decision not in {"revise", "unknown"}:
        raise conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    repair_codes: list[str] = []
    if review.checks_json:
        try:
            payload = json.loads(review.checks_json)
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            for check in payload.get("checks") or []:
                if isinstance(check, dict) and check.get("outcome") != "pass":
                    rule_id = check.get("rule_id")
                    if isinstance(rule_id, str):
                        repair_codes.append(rule_id)
    await _record_revision_text(ctx, text)
    await _run_creative(
        ctx,
        brief,
        revision_note=text,
        previous=previous,
        repair_codes=repair_codes,
    )


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if action == "plan_creative":
        await _plan(ctx)
        return
    text = validate_text(payload)
    await _revise(ctx, text)


__all__ = ["STAGE", "ACTIONS", "available", "handle", "validate_text"]

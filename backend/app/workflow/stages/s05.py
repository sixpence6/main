from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from app.db import read_session
from app.errors import ApiError, conflict, validation_error
from app.models import Project
from app.workflow import templates
from app.workflow.adjudication import (
    RULESET_VERSION,
    ProtocolIssue,
    gate_mode_for_target,
    governance_ruleset,
    review_output,
)
from app.workflow.contracts import ContractError, digest_payload, validate_data
from app.workflow.roles import RoleOutput, run_role
from app.workflow.snapshot import default_field_strengths
from app.workflow.state import (
    WorkflowContext,
    add_review,
    latest_artifact,
    set_artifact_status,
    set_stage,
    start_artifact,
)
from app.workflow.tables import Artifact, ArtifactReview, RevisionBudget

STAGE = "S05"
ACTIONS = [
    "plan_visual",
    "revise_visual",
    "finalize_requirements",
]

# G03 review rules owned by this stage. The reviewer must attest every rule
# exactly once; a model pass with a missing, duplicate, foreign or unsupported
# check never approves the artifact.
REQUIRED_VISUAL_RULES = (
    "S05_STYLE",
    "S05_COMPOSITION",
    "S05_BACKGROUND",
    "S05_PALETTE",
    "S05_LIGHTING",
    "S05_TEXT_LAYOUT",
    "S05_REFERENCE_TREATMENT",
    "S05_PRESERVATION",
)
REQUIRED_CARD_RULES = (
    "S06_PRODUCT_SOURCE",
    "S06_PURPOSE",
    "S06_CREATIVE",
    "S06_STYLE",
    "S06_COMPOSITION_LIGHTING",
    "S06_REFERENCE",
    "S06_TEXT",
    "S06_CONSTRAINTS",
    "S06_SIZE",
    "S06_BLOCKERS",
)

# Shared reviewer prompts also emit general governance checks. They are
# advisory in the server catalogue; REQUIRED_*_RULES still decide whether an
# artifact can pass.
RULESET_VISUAL = governance_ruleset(
    "visual_plan",
    tuple((rule_id, "视觉方案核验") for rule_id in REQUIRED_VISUAL_RULES),
    evidence_types=("artifact", "reference"),
)
RULESET_CARD = governance_ruleset(
    "final_card",
    tuple((rule_id, "最终需求卡核验") for rule_id in REQUIRED_CARD_RULES),
    evidence_types=("artifact", "reference"),
)


def _ruleset_for(required_rules: tuple[str, ...]) -> Any:
    if tuple(required_rules) == REQUIRED_CARD_RULES:
        return RULESET_CARD
    return RULESET_VISUAL

_VISUAL_KINDS = ("brief_draft", "creative_direction")
_CARD_KINDS = ("brief_draft", "creative_direction", "visual_plan")
_URL_MARKERS = ("://", "http:", "https:", "data:")
# When no on-image copy is approved, a plan may still quote the requirement
# itself (for example the user's words "no text"). Such meta references are
# not layout copy and must not be mistaken for invented screen text.
_TEXT_LAYOUT_META_MARKERS = (
    "文字",
    "文案",
    "标题",
    "副标题",
    "字幕",
    "水印",
    "标识",
    "logo",
)


def available(ctx: WorkflowContext) -> list[str]:
    if ctx.run is None or ctx.run.stage != STAGE:
        return []
    visual_status = ctx.latest_artifact_statuses.get("visual_plan")
    if visual_status == "approved":
        return ["revise_visual", "finalize_requirements"]
    if visual_status is not None:
        return ["revise_visual"]
    return ["plan_visual"]


def _payload(artifact: Artifact) -> dict[str, Any]:
    try:
        value = json.loads(artifact.payload_json)
    except ValueError as exc:  # pragma: no cover - persisted rows are validated
        raise ApiError(409, "ARTIFACT_INVALID", templates.render("ACTION_UNAVAILABLE")) from exc
    if not isinstance(value, dict):  # pragma: no cover - defensive
        raise ApiError(409, "ARTIFACT_INVALID", templates.render("ACTION_UNAVAILABLE"))
    return value


def _dependency(artifact: Artifact) -> dict[str, Any]:
    return {
        "kind": artifact.kind,
        "artifact_id": artifact.id,
        "revision_no": artifact.revision_no,
        "digest": artifact.digest,
    }


def _sorted_dependencies(dependencies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        dependencies,
        key=lambda item: (str(item.get("kind")), int(item.get("revision_no", 0))),
    )


def _same_dependencies(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return _sorted_dependencies(left) == _sorted_dependencies(right)


async def _latest_review(ctx: WorkflowContext, artifact_id: str) -> ArtifactReview | None:
    async with read_session(ctx.session_factory) as session:
        return await session.scalar(
            select(ArtifactReview)
            .where(ArtifactReview.artifact_id == artifact_id)
            .order_by(ArtifactReview.created_at.desc())
            .limit(1)
        )


def _parse_json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _recorded_dependencies(
    ctx: WorkflowContext, artifact_id: str
) -> list[dict[str, Any]] | None:
    review = await _latest_review(ctx, artifact_id)
    if review is None:
        return None
    for raw in (review.checks_json, review.raw_json):
        parsed = _parse_json_dict(raw)
        dependencies = parsed.get("dependencies")
        if isinstance(dependencies, list) and all(isinstance(item, dict) for item in dependencies):
            return dependencies
    return None


def _stale() -> ApiError:
    return conflict("RUN_SUPERSEDED", templates.render("RUN_SUPERSEDED"))


async def _verify_dependencies(
    ctx: WorkflowContext,
    dependencies: list[dict[str, Any]],
    expected_revision: int,
) -> None:
    """Recheck the project revision and every approved upstream dependency."""
    async with read_session(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        if project is None or project.revision != expected_revision:
            raise _stale()
        for dependency in dependencies:
            artifact = await session.get(Artifact, str(dependency["artifact_id"]))
            if (
                artifact is None
                or artifact.status != "approved"
                or artifact.digest != dependency["digest"]
                or artifact.revision_no != dependency["revision_no"]
                or artifact.kind != dependency["kind"]
            ):
                raise _stale()
            latest = await session.scalar(
                select(Artifact)
                .where(
                    Artifact.run_id == ctx.run_id,
                    Artifact.kind == artifact.kind,
                    Artifact.status == "approved",
                )
                .order_by(Artifact.revision_no.desc())
                .limit(1)
            )
            if latest is None or latest.id != artifact.id:
                raise _stale()


async def _require_review_pass(ctx: WorkflowContext, artifact: Artifact) -> None:
    if ctx.direct:
        return
    review = await _latest_review(ctx, artifact.id)
    if review is None or review.decision != "pass":
        raise conflict("ARTIFACT_NOT_APPROVED", "产物尚未通过独立审查")


async def _current_dependencies(
    ctx: WorkflowContext,
    kinds: tuple[str, ...],
    *,
    include_reference: bool,
) -> list[dict[str, Any]]:
    dependencies: list[dict[str, Any]] = []
    for kind in kinds:
        artifact = await latest_artifact(ctx, kind, status="approved")
        if artifact is None:
            raise conflict("ARTIFACT_NOT_APPROVED", "所需产物尚未通过审查")
        latest = await latest_artifact(ctx, kind)
        if latest is None or latest.id != artifact.id:
            raise conflict("ARTIFACT_STALE", "该产物已有新版本，旧版本不能继续")
        await _require_review_pass(ctx, artifact)
        dependencies.append(_dependency(artifact))
    if include_reference:
        artifact = await latest_artifact(ctx, "reference_report", status="approved")
        if artifact is None:
            raise conflict("MISSING_EVIDENCE", templates.render("ACTION_UNAVAILABLE"))
        latest = await latest_artifact(ctx, "reference_report")
        if latest is None or latest.id != artifact.id:
            raise conflict("ARTIFACT_STALE", "参考报告已有新版本，旧版本不能继续")
        await _require_review_pass(ctx, artifact)
        dependencies.append(_dependency(artifact))
    return dependencies


async def _approved_payload(ctx: WorkflowContext, kind: str) -> tuple[Artifact, dict[str, Any]]:
    artifact = await latest_artifact(ctx, kind, status="approved")
    if artifact is None:
        raise conflict("ARTIFACT_NOT_APPROVED", "所需产物尚未通过审查")
    latest = await latest_artifact(ctx, kind)
    if latest is None or latest.id != artifact.id:
        raise conflict("ARTIFACT_STALE", "该产物已有新版本，旧版本不能继续")
    await _require_review_pass(ctx, artifact)
    data = _payload(artifact)
    if digest_payload(data) != artifact.digest:
        raise conflict("ARTIFACT_STALE", "产物内容与批准摘要不一致")
    return artifact, data


async def _budget_used(ctx: WorkflowContext, kind: str) -> tuple[int, int]:
    async with read_session(ctx.session_factory) as session:
        budget = await session.get(RevisionBudget, (ctx.run_id, kind))
    if budget is None:
        return 0, 3
    return budget.used, budget.max_allowed


async def _ensure_budget(ctx: WorkflowContext, kind: str) -> None:
    used, maximum = await _budget_used(ctx, kind)
    if used >= maximum:
        raise conflict(
            "REVISION_BUDGET_EXHAUSTED",
            "该产物已达到最多三次候选的上限，请保留当前结果或重新开始流程",
        )


async def _supersede_previous(ctx: WorkflowContext, kind: str, keep_id: str) -> None:
    async with read_session(ctx.session_factory) as session:
        previous = (
            await session.scalars(
                select(Artifact).where(
                    Artifact.run_id == ctx.run_id,
                    Artifact.kind == kind,
                    Artifact.id != keep_id,
                    Artifact.status.in_(("candidate", "approved")),
                )
            )
        ).all()
    for artifact in previous:
        await set_artifact_status(ctx, artifact.id, "superseded")


def _reject_extra_fields(payload: dict[str, Any], allowed: tuple[str, ...] = ()) -> None:
    if set(payload) - set(allowed):
        raise validation_error("请求参数不合法")


def _feedback_text(payload: dict[str, Any]) -> str:
    value = payload.get("text")
    if not isinstance(value, str) or not value.strip():
        raise validation_error("请说明视觉调整要求")
    if len(value) > 20_000:
        raise validation_error("视觉调整说明过长")
    return value


def _issue_code(output: RoleOutput) -> str:
    for issue in output.issues:
        if isinstance(issue, dict):
            code = issue.get("code")
            if isinstance(code, str) and code:
                return code
    return "MISSING_INPUT"


async def _return_to_requirements(ctx: WorkflowContext, output: RoleOutput) -> None:
    status = "needs_input" if output.status == "needs_input" else "blocked"
    await set_stage(
        ctx,
        "S03",
        status,
        issue_code=_issue_code(output),
        template_id="ACTION_UNAVAILABLE",
    )


def _contains_url(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in _URL_MARKERS)


def _drift() -> ApiError:
    return conflict("REQUIREMENT_DRIFT", templates.render("ACTION_UNAVAILABLE"))


def _is_text_control_quote(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in _TEXT_LAYOUT_META_MARKERS)


def _check_text_layout(
    visual: dict[str, Any], brief: dict[str, Any], creative: dict[str, Any]
) -> None:
    layout = str(visual["text_layout"])
    expected_texts = [str(item) for item in brief["texts"]]
    expected_texts.extend(str(item) for item in creative["copy_lines"])
    quoted = re.findall(
        r"[\u300c\u300e\u201c\"']([^\u300d\u300f\u201d\"']+)[\u300d\u300f\u201d\"']",
        layout,
    )
    if not expected_texts:
        lowered = layout.lower()
        says_no_text = (
            ("无" in layout and "文字" in layout)
            or any(
                token in layout
                for token in ("不添加文字", "不含文字", "不加文字", "没有文字", "不出现文字")
            )
            or "no text" in lowered
            or "without text" in lowered
            or "no on-image text" in lowered
            or "text-free" in lowered
        )
        if not says_no_text:
            raise _drift()
        if any(not _is_text_control_quote(item) for item in quoted):
            raise _drift()
        return
    for text in expected_texts:
        if text not in layout:
            raise _drift()
    for item in quoted:
        if item not in expected_texts:
            raise _drift()


def _check_reference_treatment(visual: dict[str, Any], brief: dict[str, Any]) -> None:
    treatment = str(visual["reference_treatment"])
    usage = str(brief["reference_usage"])
    lowered = treatment.lower()
    if usage == "none":
        if not any(
            token in treatment for token in ("无参考", "不适用", "没有参考", "不使用参考")
        ) and not any(token in lowered for token in ("none", "n/a", "no reference")):
            raise _drift()
        return
    if "参考" not in treatment and "reference" not in lowered:
        raise _drift()
    if usage == "product" and "商品" not in treatment and "product" not in lowered:
        raise _drift()
    if usage == "style" and "风格" not in treatment and "style" not in lowered:
        raise _drift()
    if (
        usage == "composition"
        and not any(token in treatment for token in ("构图", "布局"))
        and not any(token in lowered for token in ("composition", "layout"))
    ):
        raise _drift()
    if usage == "style":
        if any(token in treatment for token in ("替换商品", "作为商品身份")):
            raise _drift()
        if "改变商品外观" in treatment and "不改变商品外观" not in treatment:
            raise _drift()


def _validate_visual_plan(
    visual: dict[str, Any], brief: dict[str, Any], creative: dict[str, Any]
) -> None:
    """Semantic checks that a schema-valid plan still satisfies the brief."""
    if not visual["style_keywords"]:
        raise _drift()
    if not visual["palette"]:
        raise _drift()
    for field in (
        "composition",
        "lighting",
        "background",
        "text_layout",
        "reference_treatment",
    ):
        if _contains_url(str(visual[field])):
            raise ApiError(409, "UNSAFE_REQUEST", templates.render("ACTION_UNAVAILABLE"))
    _check_text_layout(visual, brief, creative)
    _check_reference_treatment(visual, brief)

    constraints = [str(item) for item in brief["must_preserve"]]
    constraints.extend(str(item) for item in brief["must_include"])
    rules = [str(item) for item in visual["preservation_rules"]]
    if constraints and not rules:
        raise _drift()


async def _validate_sources(ctx: WorkflowContext, brief: dict[str, Any]) -> dict[str, Any] | None:
    reference = None
    if brief["reference_asset_id"] is None:
        if brief["reference_usage"] != "none":
            raise _drift()
    else:
        report = await latest_artifact(ctx, "reference_report", status="approved")
        if report is None:
            raise conflict("MISSING_EVIDENCE", templates.render("ACTION_UNAVAILABLE"))
        reference = _payload(report)
        if reference.get("reference_usage") != brief["reference_usage"]:
            raise _drift()

    observed_evidence: set[str] = set()
    if reference is not None:
        report_artifact = await latest_artifact(ctx, "reference_report", status="approved")
        if report_artifact is not None:
            observed_evidence.add(report_artifact.id)
        for observation in reference.get("observations", []):
            observed_evidence.update(str(item) for item in observation.get("evidence_ids", []))
        for text in reference.get("texts", []):
            observed_evidence.update(str(item) for item in text.get("evidence_ids", []))
    for fact in brief["facts"]:
        if fact["basis"] == "image_observed":
            if reference is None:
                raise conflict("MISSING_EVIDENCE", templates.render("ACTION_UNAVAILABLE"))
            if not set(str(item) for item in fact["evidence_ids"]) <= observed_evidence:
                raise conflict("MISSING_EVIDENCE", templates.render("ACTION_UNAVAILABLE"))
    return reference


def _configured_size(ctx: WorkflowContext) -> str | None:
    return ctx.project.image_size or ctx.settings.visual().image_size


def _compose_final_brief(
    ctx: WorkflowContext,
    brief: dict[str, Any],
    creative: dict[str, Any],
    visual: dict[str, Any],
) -> dict[str, Any]:
    product = brief["product_description"]
    purpose = brief["purpose"]
    if not isinstance(product, str) or not product.strip():
        raise conflict("MISSING_INPUT", templates.render("ACTION_UNAVAILABLE"))
    if not isinstance(purpose, str) or not purpose.strip():
        raise conflict("MISSING_INPUT", templates.render("ACTION_UNAVAILABLE"))
    if brief["unresolved"]:
        raise conflict("MISSING_INPUT", templates.render("ACTION_UNAVAILABLE"))
    configured = _configured_size(ctx)
    if configured is not None and brief["size"] != configured:
        raise _drift()
    direction = str(creative["direction"])
    if len(direction) > 2000:
        raise _drift()
    composition = str(visual["composition"])
    lighting = str(visual["lighting"])
    if len(composition) > 2000 or len(lighting) > 2000:
        raise _drift()

    texts: list[str] = []
    for item in [*brief["texts"], *creative["copy_lines"]]:
        text = str(item)
        if text not in texts:
            texts.append(text)
    if len(texts) > 64 or any(len(text) > 2000 for text in texts):
        raise _drift()

    must_preserve: list[str] = [str(item) for item in brief["must_preserve"]]
    already_declared = set(must_preserve)
    already_declared.update(str(item) for item in brief["must_include"])
    already_declared.update(str(item) for item in brief["forbidden"])
    for item in visual["preservation_rules"]:
        text = str(item)
        if text not in already_declared and text not in must_preserve:
            must_preserve.append(text)
    if len(must_preserve) > 128 or any(len(item) > 2000 for item in must_preserve):
        raise _drift()

    style_keywords = [str(item) for item in visual["style_keywords"]]
    if not style_keywords:
        style_keywords = [str(item) for item in brief["style_keywords"]]
    if not style_keywords:
        raise _drift()

    field_sources = {
        "product_description": "brief_draft",
        "purpose": "brief_draft",
        "facts": "brief_draft",
        "creative_direction": "creative_direction",
        "style_keywords": "visual_plan" if visual["style_keywords"] else "brief_draft",
        "composition": "visual_plan",
        "lighting": "visual_plan",
        "background": "visual_plan",
        "palette": "visual_plan",
        "text_layout": "visual_plan",
        "reference_treatment": "visual_plan",
        "texts": "creative_direction",
        "must_include": "brief_draft",
        "must_preserve": (
            "brief_draft+visual_plan" if visual["preservation_rules"] else "brief_draft"
        ),
        "preservation_rules": "visual_plan",
        "forbidden": "brief_draft",
        "size": "brief_draft",
        "reference_asset_id": "brief_draft",
        "reference_usage": "brief_draft",
        "assumptions": "brief_draft",
        "unresolved": "brief_draft",
        "selling_points": "creative_direction",
        "copy_lines": "creative_direction",
    }
    payload = {
        "product_description": product,
        "purpose": purpose,
        "facts": [dict(item) for item in brief["facts"]],
        "creative_direction": direction,
        "style_keywords": style_keywords,
        "composition": composition,
        "lighting": lighting,
        "background": str(visual["background"]),
        "palette": [str(item) for item in visual["palette"]],
        "text_layout": str(visual["text_layout"]),
        "reference_treatment": str(visual["reference_treatment"]),
        "texts": texts,
        "must_include": [str(item) for item in brief["must_include"]],
        "must_preserve": must_preserve,
        "preservation_rules": [str(item) for item in visual["preservation_rules"]],
        "forbidden": [str(item) for item in brief["forbidden"]],
        "size": brief["size"],
        "reference_asset_id": brief["reference_asset_id"],
        "reference_usage": brief["reference_usage"],
        "assumptions": [str(item) for item in brief["assumptions"]],
        "unresolved": [str(item) for item in brief["unresolved"]],
        "selling_points": [dict(item) for item in creative["selling_points"]],
        "copy_lines": [str(item) for item in creative["copy_lines"]],
        "field_sources": field_sources,
        "user_locks": dict(brief.get("user_locks") or {}),
    }
    payload["field_strengths"] = default_field_strengths(payload)
    try:
        validate_data("final_brief", payload)
    except ContractError as exc:
        raise _drift() from exc
    return payload


def _review_unavailable() -> ApiError:
    return ApiError(503, "REVIEW_UNAVAILABLE", templates.render("REVIEW_UNAVAILABLE"))


def _review_invalid(message: str) -> ApiError:
    return ApiError(409, "REVIEW_INVALID", message)


def _validate_review(
    output: RoleOutput,
    target_id: str,
    required_rules: tuple[str, ...],
    allowed_evidence: set[str],
) -> tuple[str, list[dict[str, Any]]]:
    if output.status != "ready" or output.data is None:
        raise _review_invalid(templates.render("REVIEW_INVALID"))
    try:
        adjudication = review_output(
            output.data,
            target_id=target_id,
            ruleset=_ruleset_for(required_rules),
            allowed_evidence=allowed_evidence,
        )
    except ProtocolIssue as exc:
        raise _review_invalid(exc.message) from None
    for check in adjudication.checks:
        if _contains_url(check.note):
            raise _review_invalid("审查说明不得包含外部链接，未放行")
    return adjudication.decision, adjudication.check_payloads()


def _collect_evidence(
    brief: dict[str, Any],
    creative: dict[str, Any],
    reference: dict[str, Any] | None,
) -> set[str]:
    evidence: set[str] = set()
    for fact in brief.get("facts", []):
        evidence.update(str(item) for item in fact.get("evidence_ids", []))
    for point in creative.get("selling_points", []):
        evidence.update(str(item) for item in point.get("evidence_ids", []))
    if reference is not None:
        for observation in reference.get("observations", []):
            evidence.update(str(item) for item in observation.get("evidence_ids", []))
        for text in reference.get("texts", []):
            evidence.update(str(item) for item in text.get("evidence_ids", []))
    return evidence


async def _review_artifact(
    ctx: WorkflowContext,
    *,
    artifact: Artifact,
    target_kind: str,
    target_payload: dict[str, Any],
    dependencies: list[dict[str, Any]],
    required_rules: tuple[str, ...],
    approved_inputs: dict[str, Any],
    attempt: int,
    expected_revision: int,
    extra_evidence: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    allowed = {artifact.id}
    allowed.update(str(item["artifact_id"]) for item in dependencies)
    allowed.update(extra_evidence or set())
    ruleset = _ruleset_for(required_rules)
    try:
        if gate_mode_for_target(target_kind) == "off":
            # Gate disabled: keep dependency/staleness safety checks, but do
            # not spend a reviewer model call on this target.
            await _verify_dependencies(ctx, dependencies, expected_revision)
            latest_target = await latest_artifact(ctx, artifact.kind)
            if latest_target is None or latest_target.id != artifact.id:
                await set_artifact_status(ctx, artifact.id, "superseded")
                raise _stale()
            return "pass", []
        output = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": ("visual_director" if target_kind == "visual_plan" else "backend"),
                "target_kind": target_kind,
                "ruleset_version": ruleset.version,
                "rules": ruleset.describe(),
            },
            approved_inputs=approved_inputs,
            review_targets=[
                {
                    "target_id": artifact.id,
                    "target_kind": target_kind,
                    "target": target_payload,
                    "dependencies": dependencies,
                }
            ],
            required_rule_ids=required_rules,
            allowed_evidence_ids=tuple(sorted(allowed)),
            attempt=attempt,
        )
        decision, checks = _validate_review(output, artifact.id, required_rules, allowed)
        await _verify_dependencies(ctx, dependencies, expected_revision)
        latest_target = await latest_artifact(ctx, artifact.kind)
        if latest_target is None or latest_target.id != artifact.id:
            await set_artifact_status(ctx, artifact.id, "superseded")
            raise _stale()
    except ApiError as exc:
        if exc.code == "RUN_SUPERSEDED":
            await set_artifact_status(ctx, artifact.id, "superseded")
            await set_stage(
                ctx,
                STAGE,
                "blocked",
                issue_code="RUN_SUPERSEDED",
                template_id="RUN_SUPERSEDED",
            )
        elif exc.code in {"SERVICE_UNAVAILABLE", "REVIEW_UNAVAILABLE"}:
            await set_stage(
                ctx,
                STAGE,
                "blocked",
                issue_code="REVIEW_UNAVAILABLE",
                template_id="REVIEW_UNAVAILABLE",
            )
        elif exc.code in {"REVIEW_INVALID", "REVIEW_REJECTED"}:
            await set_stage(
                ctx,
                STAGE,
                "blocked",
                issue_code=exc.code,
                template_id=exc.code,
            )
        raise
    return decision, checks


async def _settle_review(
    ctx: WorkflowContext,
    artifact: Artifact,
    *,
    target_kind: str,
    decision: str,
    checks: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
) -> None:
    if decision == "pass":
        await add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision="pass",
            checks={
                "ruleset_version": RULESET_VERSION,
                "dependencies": dependencies,
                "checks": checks,
            },
            raw={
                "reviewed_role": ("visual_director" if target_kind == "visual_plan" else "backend"),
                "target_kind": target_kind,
                "dependencies": dependencies,
            },
        )
        await set_stage(ctx, STAGE, "active")
        return

    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision=decision,
        checks={
            "ruleset_version": RULESET_VERSION,
            "dependencies": dependencies,
            "checks": checks,
        },
        raw={
            "reviewed_role": ("visual_director" if target_kind == "visual_plan" else "backend"),
            "target_kind": target_kind,
            "dependencies": dependencies,
        },
    )
    template_id = "ACTION_UNAVAILABLE" if decision == "revise" else "REVIEW_UNAVAILABLE"
    await set_stage(
        ctx,
        STAGE,
        "blocked",
        issue_code=f"REVIEW_{decision.upper()}",
        template_id=template_id,
    )


async def _produce_visual(
    ctx: WorkflowContext,
    *,
    brief: dict[str, Any],
    creative: dict[str, Any],
    reference: dict[str, Any] | None,
    dependencies: list[dict[str, Any]],
    input_data: dict[str, Any],
    attempt: int,
    expected_revision: int,
    repair_codes: list[str] | None = None,
) -> None:
    approved_inputs = {
        "brief_draft": brief,
        "creative_direction": creative,
    }
    if reference is not None:
        approved_inputs["reference_report"] = reference
    try:
        output = await run_role(
            ctx,
            "visual_director",
            STAGE,
            input_data,
            approved_inputs=approved_inputs,
            repair_codes=repair_codes,
            attempt=attempt,
        )
    except ApiError:
        await set_stage(
            ctx,
            STAGE,
            "failed",
            issue_code="SERVICE_UNAVAILABLE",
            template_id="SERVICE_UNAVAILABLE",
        )
        raise

    if output.status != "ready":
        await _return_to_requirements(ctx, output)
        return
    data = output.require_data()
    try:
        _validate_visual_plan(data, brief, creative)
        await _verify_dependencies(ctx, dependencies, expected_revision)
        artifact = await start_artifact(ctx, "visual_plan", data)
    except ApiError as exc:
        if exc.code == "RUN_SUPERSEDED":
            await set_stage(
                ctx,
                STAGE,
                "blocked",
                issue_code="RUN_SUPERSEDED",
                template_id="RUN_SUPERSEDED",
            )
        raise

    await _supersede_previous(ctx, "visual_plan", artifact.id)
    decision, checks = await _review_artifact(
        ctx,
        artifact=artifact,
        target_kind="visual_plan",
        target_payload=data,
        dependencies=dependencies,
        required_rules=REQUIRED_VISUAL_RULES,
        approved_inputs=approved_inputs,
        attempt=attempt,
        expected_revision=expected_revision,
        extra_evidence=_collect_evidence(brief, creative, reference),
    )
    await _settle_review(
        ctx,
        artifact,
        target_kind="visual_plan",
        decision=decision,
        checks=checks,
        dependencies=dependencies,
    )


async def _plan_visual(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    _reject_extra_fields(payload)
    expected_revision = ctx.project.revision
    _, brief = await _approved_payload(ctx, "brief_draft")
    _, creative = await _approved_payload(ctx, "creative_direction")
    reference = await _validate_sources(ctx, brief)
    deps = await _current_dependencies(ctx, _VISUAL_KINDS, include_reference=reference is not None)

    existing = await latest_artifact(ctx, "visual_plan", status="approved")
    if existing is not None:
        recorded = await _recorded_dependencies(ctx, existing.id)
        if recorded is not None and _same_dependencies(recorded, deps):
            return

    await _ensure_budget(ctx, "visual_plan")
    used, _ = await _budget_used(ctx, "visual_plan")
    await _produce_visual(
        ctx,
        brief=brief,
        creative=creative,
        reference=reference,
        dependencies=deps,
        input_data={
            "brief_draft": brief,
            "creative_direction": creative,
            "reference_report": reference,
            "existing_visual_plan": None,
        },
        attempt=used + 1,
        expected_revision=expected_revision,
        repair_codes=None,
    )


async def _revise_visual(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    _reject_extra_fields(payload, ("text",))
    feedback = _feedback_text(payload)
    expected_revision = ctx.project.revision
    _, brief = await _approved_payload(ctx, "brief_draft")
    _, creative = await _approved_payload(ctx, "creative_direction")
    reference = await _validate_sources(ctx, brief)
    previous = await latest_artifact(ctx, "visual_plan")
    if previous is None:
        raise conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    deps = await _current_dependencies(ctx, _VISUAL_KINDS, include_reference=reference is not None)
    await _ensure_budget(ctx, "visual_plan")
    used, _ = await _budget_used(ctx, "visual_plan")

    previous_review = await _latest_review(ctx, previous.id)
    repair_codes: list[str] = []
    if previous_review is not None:
        parsed = _parse_json_dict(previous_review.checks_json)
        for check in parsed.get("checks", []):
            if isinstance(check, dict) and check.get("outcome") != "pass":
                rule_id = check.get("rule_id")
                if isinstance(rule_id, str):
                    repair_codes.append(rule_id)
    await _produce_visual(
        ctx,
        brief=brief,
        creative=creative,
        reference=reference,
        dependencies=deps,
        input_data={
            "brief_draft": brief,
            "creative_direction": creative,
            "reference_report": reference,
            "feedback": feedback,
            "previous_visual_plan": _payload(previous),
        },
        attempt=used + 1,
        expected_revision=expected_revision,
        repair_codes=repair_codes,
    )


async def _finalize(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    _reject_extra_fields(payload)
    expected_revision = ctx.project.revision
    _, brief = await _approved_payload(ctx, "brief_draft")
    reference = await _validate_sources(ctx, brief)
    _, creative = await _approved_payload(ctx, "creative_direction")
    visual_artifact, visual = await _approved_payload(ctx, "visual_plan")

    deps = await _current_dependencies(ctx, _CARD_KINDS, include_reference=reference is not None)
    visual_dependencies = [item for item in deps if item["kind"] != "visual_plan"]
    recorded = await _recorded_dependencies(ctx, visual_artifact.id)
    if recorded is not None and not _same_dependencies(recorded, visual_dependencies):
        raise _stale()

    existing = await latest_artifact(ctx, "final_brief", status="approved")
    if existing is not None:
        existing_deps = await _recorded_dependencies(ctx, existing.id)
        if existing_deps is not None and _same_dependencies(existing_deps, deps):
            return

    card = _compose_final_brief(ctx, brief, creative, visual)
    if not ctx.direct:
        await _ensure_budget(ctx, "final_brief")
    used, _ = await _budget_used(ctx, "final_brief")
    try:
        artifact = await start_artifact(ctx, "final_brief", card)
    except ContractError as exc:  # pragma: no cover - guarded by validate_data above
        raise _drift() from exc

    await _supersede_previous(ctx, "final_brief", artifact.id)
    approved_inputs = {
        "brief_draft": brief,
        "creative_direction": creative,
        "visual_plan": visual,
    }
    if reference is not None:
        approved_inputs["reference_report"] = reference
    decision, checks = await _review_artifact(
        ctx,
        artifact=artifact,
        target_kind="final_brief",
        target_payload=card,
        dependencies=deps,
        required_rules=REQUIRED_CARD_RULES,
        approved_inputs=approved_inputs,
        attempt=used + 1,
        expected_revision=expected_revision,
        extra_evidence=_collect_evidence(brief, creative, reference),
    )
    await _settle_review(
        ctx,
        artifact,
        target_kind="final_brief",
        decision=decision,
        checks=checks,
        dependencies=deps,
    )
    if decision == "pass":
        await set_stage(ctx, "S06", "active")
        if ctx.direct:
            from app.workflow.collaboration import accept_generation

            await accept_generation(ctx)


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if ctx.consolidated and action in {"plan_visual", "revise_visual"}:
        from app.workflow.consolidated import plan_visual

        feedback = _feedback_text(payload) if action == "revise_visual" else None
        await plan_visual(ctx, feedback=feedback)
        return
    if action == "plan_visual":
        await _plan_visual(ctx, payload)
    elif action == "revise_visual":
        await _revise_visual(ctx, payload)
    elif action == "finalize_requirements":
        await _finalize(ctx, payload)


__all__ = [
    "STAGE",
    "ACTIONS",
    "REQUIRED_VISUAL_RULES",
    "REQUIRED_CARD_RULES",
    "available",
    "handle",
]

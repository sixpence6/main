from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.acceptance import GenerationIntent, release_slots_in_session
from app.adapters.base import OrganizeImage
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.models import Asset, Generation
from app.services.assets import inspect_image, resolve_asset_file
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
from app.workflow.adjudication import (
    ADVISORY_GOVERNANCE_RULES,
    RULESET_VERSION,
    ProtocolIssue,
    Rule,
    RuleSet,
    adjudicate,
    governance_ruleset,
    review_results,
)
from app.workflow.roles import RoleOutput, run_role
from app.workflow.snapshot import (
    SnapshotCompatibilityError,
    execution_snapshot_from_checks,
)
from app.workflow.state import (
    WorkflowContext,
    add_approval,
    add_review,
    approved_artifact,
    latest_approval,
    latest_artifact,
    latest_model_fingerprint,
    requirement_snapshot_digest,
    set_stage,
    start_artifact,
)
from app.workflow.tables import Artifact, ArtifactReview, GenerationJob

STAGE = "S09"
ACTIONS = [
    "review_candidate",
    "recheck_candidate",
    "retry_rejected_generation",
    "revise_prompt_and_retry",
]

# Every hard requirement is fixed here by the program, not by the model. A
# rule can be conditional (product consistency only applies to product
# references), but its evidence type and applicability are server-owned.
_RULES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("hard:subject", "主体、关键外观、结构与颜色符合确认需求", ("image", "requirement"), True),
    (
        "hard:product_consistency",
        "商品关键外观、结构、颜色、数量及明确 Logo 与商品参考一致",
        ("image", "reference", "requirement"),
        False,
    ),
    ("hard:quantity", "画面中的商品数量符合确认需求", ("image", "requirement"), True),
    (
        "hard:text_logo",
        "画面文字及 Logo 与确认的 Unicode 字符逐字一致，无缺字、多字或不可辨字符",
        ("image", "requirement"),
        True,
    ),
    ("hard:forbidden", "未出现确认的禁止项", ("image", "requirement"), True),
    ("hard:composition", "构图符合确认的明确约束", ("image", "requirement"), True),
    (
        "hard:size_format",
        "尺寸与图片格式符合本地技术校验",
        ("technical", "requirement", "image"),
        True,
    ),
)
_RULE_ORDER = tuple(rule[0] for rule in _RULES)
_DEFAULT_REVIEW_RULE = "review:quality_report"


# A quality or reviewer role can fail after the candidate bytes are already
# durable. Those failures are safe to recheck without generating another image:
# the fixed candidate is the input, and the original budget remains exhausted.
_RECHECK_RECOVERABLE_ISSUES = frozenset(
    {"CONTRACT_ERROR", "REVIEW_UNAVAILABLE", "SERVICE_UNAVAILABLE"}
)


@dataclass(frozen=True)
class _RuleSpec:
    rule_id: str
    label: str
    evidence_types: tuple[str, ...]
    applicable: bool


@dataclass
class _Candidate:
    job: GenerationJob | None
    asset: Asset
    data: bytes
    sha256: str
    width: int
    height: int
    mime_type: str


def _can_recheck(ctx: WorkflowContext) -> bool:
    if ctx.run is None or ctx.run.stage != STAGE:
        return False
    if ctx.run.status == "active":
        return True
    if ctx.run.status == "blocked":
        return ctx.run.issue_code == "IMAGE_NOT_APPROVED"
    return ctx.run.status == "failed" and ctx.run.issue_code in _RECHECK_RECOVERABLE_ISSUES


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    if ctx.run is None or ctx.run.stage != STAGE:
        return []
    if ctx.run.status == "failed":
        return ["recheck_candidate"] if _can_recheck(ctx) else []
    if ctx.run.status not in {"active", "needs_input", "blocked"}:
        return []
    if ctx.run.status == "blocked" and ctx.run.issue_code == "IMAGE_NOT_APPROVED":
        # A rejected candidate cannot become approved by reviewing it again.
        # Offer an explicit, separately counted replacement generation instead.
        # A prompt revision is the only way to change concrete visual guidance
        # before spending another counted attempt. Recheck is for a corrected
        # derivative of the same attempt (for example, removing text artifacts
        # from the generated pixels) and is recorded as its own artifact kind so
        # it does not consume the three-candidate quality-report budget.
        return [
            "recheck_candidate",
            "revise_prompt_and_retry",
            "retry_rejected_generation",
        ]
    # Availability is intentionally cheap and fail-closed. The real action
    # repeats every check under the project lock.
    return ["review_candidate"]


def _candidate_expired(generation: Generation, *, now: datetime | None = None) -> bool:
    """Derive the 24h candidate deadline from immutable completed_at.

    There is no expires_at column: the terminal timestamp is written once and
    must never be reset by a restart or manual continue.
    """
    completed = generation.completed_at
    if completed is None:
        # Without the immutable terminal timestamp the candidate cannot prove
        # that it is still inside its retention window. Fail closed instead of
        # allowing an old candidate to be reviewed forever.
        return True
    if isinstance(completed, str):
        try:
            completed = datetime.fromisoformat(completed.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return True
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) >= completed + timedelta(hours=24)


async def _load_candidate(
    ctx: WorkflowContext,
    *,
    asset_id: str | None = None,
    allow_rejected_job: bool = False,
) -> _Candidate:
    async with read_session(ctx.session_factory) as session:
        job = await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.run_id == ctx.run_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        if job is None:
            # The run must own a generation job that points at exactly one
            # image. Falling back to any project asset could review an
            # unrelated image under this run.
            raise ApiError(
                409, "CANDIDATE_UNAVAILABLE", templates.render("CANDIDATE_UNAVAILABLE")
            )
        if not job.result_asset_id or not job.generation_id:
            raise ApiError(
                409, "CANDIDATE_UNAVAILABLE", templates.render("CANDIDATE_UNAVAILABLE")
            )
        asset = await session.get(Asset, asset_id or job.result_asset_id)
        generation = await session.get(Generation, job.generation_id)
        if generation is None:
            raise ApiError(
                409, "CANDIDATE_UNAVAILABLE", templates.render("CANDIDATE_UNAVAILABLE")
            )
    if _candidate_expired(generation):
        # completed_at is the immutable terminal timestamp. Never reset or
        # rewrite it to extend a candidate's 24-hour retention window.
        raise ApiError(
            409, "CANDIDATE_EXPIRED", templates.render("CANDIDATE_EXPIRED")
        )
    if asset is None or asset.kind != "generated":
        raise ApiError(
            409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
        )
    if asset.project_id != ctx.project_id or asset.review_status in {
        "approved",
        "rejected",
        "legacy_unreviewed",
    }:
        raise ApiError(
            409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
        )
    if job is not None and job.status == "rejected" and not allow_rejected_job:
        raise ApiError(
            409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
        )
    path = resolve_asset_file(ctx.settings, asset)
    if path is None:
        raise ApiError(
            409, "CANDIDATE_UNAVAILABLE", templates.render("CANDIDATE_UNAVAILABLE")
        )
    try:
        data = path.read_bytes()
        info = inspect_image(data)
    except Exception:
        raise ApiError(
            503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE")
        ) from None
    digest = hashlib.sha256(data).hexdigest()
    if asset.sha256 and asset.sha256 != digest:
        raise ApiError(
            503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE")
        )
    # Persist the measured digest before any reviewer can cite it. A failed
    # check must never leave an approval pointing at an unmeasured file.
    async with write_transaction(ctx.session_factory) as session:
        stored = await session.get(Asset, asset.id)
        if stored is not None:
            stored.sha256 = digest
            stored.mime_type = info.mime_type or stored.mime_type
            stored.width = info.width
            stored.height = info.height
            await session.flush()
    asset.sha256 = digest
    asset.mime_type = info.mime_type or asset.mime_type
    asset.width = info.width
    asset.height = info.height
    return _Candidate(
        job=job,
        asset=asset,
        data=data,
        sha256=digest,
        width=info.width,
        height=info.height,
        mime_type=info.mime_type,
    )


def _json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


async def _confirmed_snapshot(
    ctx: WorkflowContext,
) -> tuple[dict[str, Any], str, dict[str, Any] | None]:
    """Read the confirmation snapshot that governs this quality review.

    The canonical execution snapshot stored on the active requirements_card
    approval is authoritative. A legacy approval without the canonical keys
    falls back explicitly to the approved final_brief artifact so old projects
    keep their original semantics. A malformed canonical snapshot is never
    silently reinterpreted from project columns.
    """
    artifact = await approved_artifact(ctx, "final_brief")
    if artifact is None:
        artifact = await approved_artifact(ctx, "brief_draft")
    if artifact is not None:
        approval = await latest_approval(ctx, "requirements_card")
        execution_snapshot: dict[str, Any] | None = None
        if approval is not None and approval.artifact_id == artifact.id:
            checks = _json_dict(approval.checks_json)
            try:
                execution_snapshot = execution_snapshot_from_checks(checks)
            except SnapshotCompatibilityError as exc:
                raise ApiError(
                    409, "SNAPSHOT_INCOMPATIBLE", "确认快照版本不兼容，请重新确认需求"
                ) from exc
            if execution_snapshot is not None:
                if (
                    execution_snapshot.get("artifact_id") != artifact.id
                    or execution_snapshot.get("artifact_digest") != artifact.digest
                ):
                    raise ApiError(
                        409, "SNAPSHOT_INCOMPATIBLE", "确认快照与已批准需求不一致"
                    )
                payload = execution_snapshot.get("brief")
                if not isinstance(payload, dict) or not payload:
                    raise ApiError(
                        409, "SNAPSHOT_INCOMPATIBLE", "确认快照缺少需求内容"
                    )
                return payload, artifact.digest, execution_snapshot
        payload = _json_dict(artifact.payload_json)
        if payload:
            return payload, artifact.digest, None
    payload = {
        "product_description": ctx.project.product_description or "",
        "purpose": ctx.project.purpose or "",
        "style": ctx.project.style or "",
        "composition": ctx.project.composition or "",
        "texts": [ctx.project.text_content] if ctx.project.text_content else [],
        "must_include": [],
        "must_preserve": [],
        "forbidden": [],
        "size": ctx.project.image_size,
        "reference_asset_id": ctx.project.reference_asset_id,
        "reference_usage": "none",
    }
    return payload, await requirement_snapshot_digest(ctx), None


async def _reference_context(
    ctx: WorkflowContext, snapshot: dict[str, Any]
) -> tuple[Asset | None, str, bytes | None]:
    report = await approved_artifact(ctx, "reference_report")
    report_payload = _json_dict(report.payload_json) if report is not None else {}
    usage = report_payload.get("reference_usage")
    if usage not in {"none", "product", "style", "composition"}:
        usage = snapshot.get("reference_usage")
    if usage not in {"none", "product", "style", "composition"}:
        usage = "none"
    if usage == "none":
        # The confirmed snapshot may still carry a stale reference id from an
        # earlier revision. A usage of none means it is not evidence.
        return None, "none", None
    reference_id = (
        snapshot.get("reference_asset_id")
        or ctx.project.reference_asset_id
    )
    if not reference_id:
        return None, str(usage), None
    async with read_session(ctx.session_factory) as session:
        reference = await session.get(Asset, str(reference_id))
    if reference is None or reference.kind != "reference":
        return None, str(usage), None
    path = resolve_asset_file(ctx.settings, reference)
    if path is None:
        return reference, str(usage), None
    try:
        data = path.read_bytes()
        info = inspect_image(data)
    except Exception:
        return reference, str(usage), None
    if not data:
        return reference, str(usage), None
    reference.mime_type = info.mime_type or reference.mime_type
    return reference, str(usage), data


def _text_requirement(snapshot: dict[str, Any]) -> str:
    texts = _brief_value(snapshot, "texts")
    if isinstance(texts, list) and texts:
        return "\uff1b".join(str(item) for item in texts if isinstance(item, str))
    value = _brief_value(snapshot, "text_content")
    if isinstance(value, str) and value:
        return value
    return (
        "无确认画面文字，也不得出现额外文字或 "
        "Logo"
    )


def _forbidden_requirement(snapshot: dict[str, Any]) -> str:
    forbidden = _brief_value(snapshot, "forbidden")
    if isinstance(forbidden, list) and forbidden:
        return "\uff1b".join(str(item) for item in forbidden if isinstance(item, str))
    return "\u65e0\u786e\u8ba4\u7981\u6b62\u9879"


_VISUAL_PATHS = (
    "composition",
    "style_keywords",
    "lighting",
    "background",
    "palette",
    "text_layout",
    "reference_treatment",
)


def _constraint_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    constraints = snapshot.get("constraints")
    if not isinstance(constraints, list):
        return None
    result: dict[str, dict[str, Any]] = {}
    for item in constraints:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            result[item["path"]] = item
    return result


def _brief_value(snapshot: dict[str, Any], path: str) -> Any:
    brief = snapshot.get("brief")
    if isinstance(brief, dict) and path in brief:
        return brief.get(path)
    return snapshot.get(path)


def _rule_specs(
    has_product_reference: bool,
    execution_snapshot: dict[str, Any] | None = None,
) -> list[_RuleSpec]:
    if execution_snapshot is None:
        # Legacy approvals have no per-field strength metadata. Their old
        # seven-rule catalogue and all-hard semantics must remain readable.
        result: list[_RuleSpec] = []
        for rule_id, label, evidence_types, applicable in _RULES:
            if rule_id == "hard:product_consistency":
                applicable = has_product_reference
            result.append(
                _RuleSpec(
                    rule_id=rule_id,
                    label=label,
                    evidence_types=evidence_types,
                    applicable=applicable,
                )
            )
        return result

    constraints = _constraint_map(execution_snapshot) or {}
    hard = {
        path
        for path, item in constraints.items()
        if item.get("strength") == "hard"
    }
    applicable_by_id = {
        "hard:subject": "product_description" in hard,
        "hard:product_consistency": has_product_reference,
        "hard:quantity": "count" in hard,
        # The absence of unconfirmed text/logo is itself a hard output rule.
        "hard:text_logo": True,
        "hard:forbidden": "forbidden" in hard,
        "hard:composition": any(path in hard for path in _VISUAL_PATHS),
        "hard:size_format": "size" in hard,
    }
    result = []
    for rule_id, label, evidence_types, _applicable in _RULES:
        result.append(
            _RuleSpec(
                rule_id=rule_id,
                label=label,
                evidence_types=evidence_types,
                applicable=applicable_by_id.get(rule_id, False),
            )
        )
    return result


def _subject_requirement(snapshot: dict[str, Any]) -> str:
    parts: list[str] = []
    product = _brief_value(snapshot, "product_description")
    if isinstance(product, str) and product:
        parts.append(product)
    facts = _brief_value(snapshot, "facts")
    if isinstance(facts, list):
        for fact in facts:
            value = fact.get("text") if isinstance(fact, dict) else fact
            if isinstance(value, str) and value:
                parts.append(value)
    for key in ("must_include", "must_preserve"):
        values = _brief_value(snapshot, key)
        if isinstance(values, list):
            parts.extend(str(item) for item in values if isinstance(item, str) and item)
    return "\uff1b".join(parts)


def _composition_requirement(snapshot: dict[str, Any], fallback: str) -> str:
    constraints = _constraint_map(snapshot)
    if constraints is None:
        # A plain legacy dict has no strength metadata. Only the explicit
        # composition value is a composition requirement; never merge model
        # suggestions into a hidden hard fallback.
        composition = _brief_value(snapshot, "composition")
        if isinstance(composition, str) and composition:
            return f"\u6784\u56fe\uff1a{composition}"
        return fallback

    labels = {
        "composition": "\u6784\u56fe\uff1a",
        "style_keywords": "\u98ce\u683c\uff1a",
        "lighting": "\u5149\u5f71\uff1a",
        "background": "\u80cc\u666f\uff1a",
        "palette": "\u914d\u8272\uff1a",
        "text_layout": "\u6587\u5b57\u5e03\u5c40\uff1a",
        "reference_treatment": "\u53c2\u8003\u5904\u7406\uff1a",
    }
    parts: list[str] = []
    for path in _VISUAL_PATHS:
        item = constraints.get(path)
        if not item or item.get("strength") != "hard":
            continue
        value = item.get("normalized_value")
        if value in (None, "", []):
            value = _brief_value(snapshot, path)
        if isinstance(value, list):
            rendered = "\u3001".join(str(entry) for entry in value if isinstance(entry, str))
        elif isinstance(value, str):
            rendered = value
        else:
            rendered = ""
        if rendered:
            parts.append(f"{labels[path]}{rendered}")
    return "\uff1b".join(parts) or fallback


def _rule_requirement(
    rule_id: str,
    snapshot: dict[str, Any],
    requirement_text: str,
    forbidden: str,
    size: str,
) -> str:
    if rule_id == "hard:forbidden":
        return forbidden
    if rule_id == "hard:size_format":
        return size
    if rule_id == "hard:text_logo":
        return _text_requirement(snapshot)
    if rule_id == "hard:composition":
        return _composition_requirement(snapshot, requirement_text)
    if rule_id == "hard:quantity":
        count = _brief_value(snapshot, "count")
        return str(count) if count not in (None, "") else "1"
    if rule_id == "hard:subject":
        return _subject_requirement(snapshot) or requirement_text
    return requirement_text


def _rule_payload(
    rules: list[_RuleSpec],
    snapshot: dict[str, Any],
    requirement_text: str,
    forbidden: str,
    size: str,
) -> list[dict[str, Any]]:
    return [
        {
            "rule_id": rule.rule_id,
            "label": rule.label,
            "applicability": "applicable" if rule.applicable else "not_applicable",
            "evidence_types": list(rule.evidence_types),
            "requirement": _rule_requirement(
                rule.rule_id, snapshot, requirement_text, forbidden, size
            ),
        }
        for rule in rules
    ]


def _evidence_type(evidence_id: str) -> str | None:
    prefix, separator, rest = evidence_id.partition(":")
    if not separator or not rest:
        return None
    if prefix not in {"image", "reference", "requirement", "technical", "report"}:
        return None
    return prefix


def _safe_summary(value: str) -> bool:
    lowered = value.lower()
    return not any(
        marker in lowered
        for marker in ("http://", "https://", "data:", "file:", "\\", "../")
    )


def _invalid_contract() -> ApiError:
    return ApiError(502, "CONTRACT_ERROR", templates.render("SERVICE_UNAVAILABLE"))


def _invalid_review(message: str | None = None) -> ApiError:
    return ApiError(409, "REVIEW_INVALID", message or templates.render("REVIEW_INVALID"))


def _check_evidence(
    check: dict[str, Any], expected_types: tuple[str, ...], allowed: set[str]
) -> bool:
    evidence = check.get("evidence_ids")
    if not isinstance(evidence, list) or not evidence:
        return False
    for evidence_id in evidence:
        if not isinstance(evidence_id, str) or evidence_id not in allowed:
            return False
        evidence_type = _evidence_type(evidence_id)
        if evidence_type is None or evidence_type not in expected_types:
            return False
    present_types = {_evidence_type(str(item)) for item in evidence}
    # A visual pass must be grounded in the actual image. Requirement or
    # reference evidence is optional when the image itself proves the rule;
    # technical rules instead require their technical evidence. Every cited
    # ID still has to belong to the allowed set and the rule's declared types.
    primary = "technical" if "technical" in expected_types else "image"
    if primary not in present_types and "image" in expected_types:
        # 尺寸规则可由独立审查者直接引用实际成图；技术校验仍由程序在
        # quality_data 中独立执行，不因引用类型不同而把合法审查判为坏契约。
        primary = "image"
    return primary in present_types


def _validate_quality(
    output: RoleOutput,
    *,
    asset: Asset,
    rules: list[_RuleSpec],
    allowed: set[str],
) -> dict[str, Any]:
    if output.status != "ready" or output.data is None:
        raise _invalid_contract()
    data = output.data
    expected_asset = {asset.id, f"image:{asset.id}"}
    if data.get("asset_id") not in expected_asset:
        raise _invalid_contract()
    decision = data.get("decision")
    if decision not in {"pass", "fail", "unknown"}:
        raise _invalid_contract()
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        raise _invalid_contract()
    by_rule: dict[str, dict[str, Any]] = {}
    for check in checks:
        if not isinstance(check, dict):
            raise _invalid_contract()
        rule_id = check.get("rule_id")
        if not isinstance(rule_id, str) or rule_id not in _RULE_ORDER:
            raise _invalid_contract()
        if rule_id in by_rule:
            raise _invalid_contract()
        by_rule[rule_id] = check
    for rule in rules:
        check = by_rule.get(rule.rule_id)
        if not rule.applicable:
            # A non-applicable rule may be omitted, but it must never be
            # reported as a pass. If present it has to be not_applicable.
            if check is not None and check.get("outcome") != "not_applicable":
                raise _invalid_contract()
            continue
        if check is None:
            raise _invalid_contract()
        outcome = check.get("outcome")
        if outcome not in {"pass", "fail", "unknown"}:
            raise _invalid_contract()
        if outcome == "pass" and not _check_evidence(
            check, rule.evidence_types, allowed
        ):
            raise _invalid_contract()
        if outcome != "pass":
            evidence = check.get("evidence_ids")
            if not isinstance(evidence, list):
                raise _invalid_contract()
            for evidence_id in evidence:
                if not isinstance(evidence_id, str) or evidence_id not in allowed:
                    raise _invalid_contract()
                evidence_type = _evidence_type(evidence_id)
                if evidence_type is None or evidence_type not in rule.evidence_types:
                    raise _invalid_contract()
    applicable = [by_rule[rule.rule_id] for rule in rules if rule.applicable]
    failures = [check for check in applicable if check.get("outcome") == "fail"]
    unknowns = [check for check in applicable if check.get("outcome") == "unknown"]
    if decision == "pass":
        if failures or unknowns or any(
            check.get("outcome") != "pass" for check in applicable
        ):
            raise _invalid_contract()
    elif decision == "fail":
        if not failures:
            raise _invalid_contract()
    elif decision == "unknown" and (failures or not unknowns):
        raise _invalid_contract()
    summary = data.get("user_summary")
    if not isinstance(summary, str) or not _safe_summary(summary):
        raise _invalid_contract()
    # Keep the exact validated dict; start_artifact re-validates the contract.
    return data


def _review_targets(
    *,
    artifact: Artifact,
    asset: Asset,
    quality_target: str,
    image_target: str,
    rules: list[_RuleSpec],
    quality_data: dict[str, Any],
    size: str,
    sha256: str,
    rule_payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "target_id": quality_target,
            "target_kind": "quality_report",
            "artifact_id": artifact.id,
            "artifact_digest": artifact.digest,
            "required_rule_ids": [_DEFAULT_REVIEW_RULE],
            "summary": quality_data.get("user_summary"),
        },
        {
            "target_id": image_target,
            "target_kind": "image",
            "asset_id": asset.id,
            "asset_ref": f"image:{asset.id}",
            "sha256": sha256,
            "width": asset.width,
            "height": asset.height,
            "mime_type": asset.mime_type,
            "size": size,
            "required_rule_ids": [
                rule.rule_id for rule in rules if rule.applicable
            ],
            "rules": rule_payload,
        },
    ]


def _quality_ruleset() -> RuleSet:
    return governance_ruleset(
        "quality_report",
        ((_DEFAULT_REVIEW_RULE, "质检报告独立核验"),),
        evidence_types=("artifact", "report"),
    )


def _image_ruleset(rules: list[_RuleSpec]) -> RuleSet:
    blocking = tuple(
        Rule(
            rule.rule_id,
            rule.label,
            evidence_types=rule.evidence_types,
            applicable=rule.applicable,
            reason=(
                "当前确认快照没有商品参考图，商品一致性规则不适用"
                if not rule.applicable
                else ""
            ),
        )
        for rule in rules
    )
    known = {rule.rule_id for rule in rules} | {_DEFAULT_REVIEW_RULE}
    evidence_types = tuple(
        dict.fromkeys(
            [item for rule in rules for item in rule.evidence_types]
            + ["artifact", "report"]
        )
    )
    advisories = tuple(
        Rule(
            rule_id,
            "治理观察项（不改变放行结论）",
            kind="advisory",
            evidence_types=evidence_types,
        )
        for rule_id in sorted(ADVISORY_GOVERNANCE_RULES - known)
    )
    return RuleSet("image", blocking + advisories)


def _validate_review(
    output: RoleOutput,
    *,
    quality_target: str,
    image_target: str,
    rules: list[_RuleSpec],
    allowed: set[str],
) -> tuple[str, list[dict[str, Any]], str, list[dict[str, Any]]]:
    if output.status != "ready" or output.data is None:
        raise _invalid_review()
    try:
        by_target = review_results(
            output.data, target_ids=(quality_target, image_target)
        )
        report = by_target[quality_target]
        image = by_target[image_target]
        quality = adjudicate(
            _quality_ruleset(),
            target_id=quality_target,
            result_target_id=report.get("target_id"),
            declared_decision=report.get("decision"),
            checks=report.get("checks"),
            allowed_evidence=allowed,
        )
        picture = adjudicate(
            _image_ruleset(rules),
            target_id=image_target,
            result_target_id=image.get("target_id"),
            declared_decision=image.get("decision"),
            checks=image.get("checks"),
            allowed_evidence=allowed,
        )
    except ProtocolIssue as exc:
        raise _invalid_review(exc.message) from None
    return (
        quality.decision,
        quality.check_payloads(),
        picture.decision,
        picture.check_payloads(),
    )

async def _set_asset(
    ctx: WorkflowContext, asset: Asset, status: str, note: str, sha256: str
) -> None:
    async with write_transaction(ctx.session_factory) as session:
        stored = await session.get(Asset, asset.id)
        if stored is None:
            return
        stored.review_status = status
        stored.review_note = note[:255]
        stored.sha256 = sha256
        await session.flush()


async def _set_job(
    ctx: WorkflowContext,
    job: GenerationJob | None,
    status: str,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    result_asset_id: str | None = None,
) -> None:
    if job is None:
        return
    async with write_transaction(ctx.session_factory) as session:
        stored = await session.get(GenerationJob, job.id)
        if stored is None:
            return
        stored.status = status
        stored.error_code = error_code
        stored.error_message = error_message[:512] if error_message else None
        if result_asset_id is not None:
            stored.result_asset_id = result_asset_id
        stored.updated_at = format_timestamp(utc_now())
        if status in {"rejected", "failed"} and stored.intent_id:
            intent = await session.get(GenerationIntent, stored.intent_id)
            if intent is not None:
                intent.status = "failed"
                intent.updated_at = stored.updated_at
            await release_slots_in_session(
                session,
                intent_id=stored.intent_id,
                reason="generation_terminal",
            )
        await session.flush()


async def _add_image_review(
    ctx: WorkflowContext,
    *,
    artifact_id: str,
    decision: str,
    checks: list[dict[str, Any]],
    raw: dict[str, Any],
    target_id: str,
) -> None:
    if decision not in {"pass", "revise", "reject", "unknown"}:
        decision = "unknown"
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        session.add(
            ArtifactReview(
                id=str(uuid4()),
                artifact_id=artifact_id,
                project_id=ctx.project_id,
                reviewer_role="output_reviewer",
                target_kind="image",
                decision=decision,
                checks_json=json.dumps(
                    {
                        "target_id": target_id,
                        "ruleset_version": RULESET_VERSION,
                        "checks": checks,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                raw_json=json.dumps(raw, ensure_ascii=False, sort_keys=True),
                created_at=now,
            )
        )
        await session.flush()


def _issue_code(output: RoleOutput, fallback: str) -> str:
    if output.issues:
        code = output.issues[0].get("code")
        if isinstance(code, str):
            return code
    return fallback


async def _fail_stage(
    ctx: WorkflowContext, template_id: str, issue_code: str
) -> None:
    await set_stage(
        ctx,
        STAGE,
        "failed",
        issue_code=issue_code,
        template_id=template_id,
    )


async def _block_stage(
    ctx: WorkflowContext, template_id: str, issue_code: str
) -> None:
    await set_stage(
        ctx,
        STAGE,
        "blocked",
        issue_code=issue_code,
        template_id=template_id,
    )


def _parse_expected_size(value: str) -> tuple[int, int] | None:
    parts = value.lower().replace(" ", "").split("x")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    width = int(parts[0])
    height = int(parts[1])
    if width <= 0 or height <= 0:
        return None
    return width, height


def _local_technical_ok(candidate: _Candidate, size: str) -> bool:
    if candidate.mime_type not in {"image/png", "image/jpeg", "image/webp"}:
        return False
    expected = _parse_expected_size(size)
    # An unknown or malformed size is not a technical pass. It must not become
    # a generated image approval merely because no comparison was possible.
    return expected is not None and expected == (candidate.width, candidate.height)


def _with_local_technical_result(
    data: dict[str, Any],
    *,
    candidate: _Candidate,
    size: str,
    technical_evidence: str,
) -> dict[str, Any]:
    if _local_technical_ok(candidate, size):
        return data
    checks: list[dict[str, Any]] = []
    found = False
    for raw_check in data.get("checks", []):
        if not isinstance(raw_check, dict):
            continue
        check = dict(raw_check)
        if check.get("rule_id") == "hard:size_format":
            check["outcome"] = "fail"
            check["evidence_ids"] = [technical_evidence]
            check["note"] = "本地技术校验未通过"
            found = True
        checks.append(check)
    if not found:
        checks.append(
            {
                "rule_id": "hard:size_format",
                "outcome": "fail",
                "evidence_ids": [technical_evidence],
                "note": "本地技术校验未通过",
            }
        )
    result = dict(data)
    result["decision"] = "fail"
    result["checks"] = checks
    result["user_summary"] = "图片尺寸或格式不符合确认要求，本次未放行。"
    return result


async def _review_and_apply(
    ctx: WorkflowContext,
    *,
    candidate: _Candidate,
    quality_data: dict[str, Any],
    rules: list[_RuleSpec],
    quality_target: str,
    image_target: str,
    quality_allowed: set[str],
    snapshot: dict[str, Any],
    execution_snapshot: dict[str, Any] | None,
    size: str,
    rule_payload: list[dict[str, Any]],
    existing_artifact: Artifact | None = None,
    artifact_kind: str = "quality_report",
) -> None:
    artifact = existing_artifact
    if artifact is None:
        artifact = await start_artifact(ctx, artifact_kind, quality_data)
    review_allowed = set(quality_allowed)
    review_allowed.add(f"report:{artifact.id}")
    review_targets = _review_targets(
        artifact=artifact,
        asset=candidate.asset,
        quality_target=quality_target,
        image_target=image_target,
        rules=rules,
        quality_data=quality_data,
        size=size,
        sha256=candidate.sha256,
        rule_payload=rule_payload,
    )
    images = [OrganizeImage(data=candidate.data, mime_type=candidate.mime_type)]
    reference, reference_usage, reference_bytes = await _reference_context(ctx, snapshot)
    if reference_bytes is not None and reference is not None:
        images.append(OrganizeImage(data=reference_bytes, mime_type=reference.mime_type))
    try:
        review = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": "quality_compliance",
                "quality_target": quality_target,
                "image_target": image_target,
                "ruleset_version": RULESET_VERSION,
                "rules": rule_payload,
            },
            approved_inputs={
                "confirmed_snapshot": snapshot,
                "final_brief": snapshot,
                "execution_snapshot": execution_snapshot,
                "reference_usage": reference_usage,
                "reference_asset_id": reference.id if reference is not None else None,
            },
            review_targets=review_targets,
            images=tuple(images),
            required_rule_ids=tuple(
                [rule.rule_id for rule in rules if rule.applicable]
                + [_DEFAULT_REVIEW_RULE]
            ),
            allowed_evidence_ids=tuple(sorted(review_allowed)),
        )
    except ApiError:
        await _fail_stage(ctx, "REVIEW_UNAVAILABLE", "REVIEW_UNAVAILABLE")
        raise
    await _apply_review(
        ctx,
        artifact=artifact,
        candidate=candidate,
        quality_data=quality_data,
        rules=rules,
        quality_target=quality_target,
        image_target=image_target,
        quality_allowed=quality_allowed,
        review=review,
    )


async def _apply_review(
    ctx: WorkflowContext,
    *,
    artifact: Artifact,
    candidate: _Candidate,
    quality_data: dict[str, Any],
    rules: list[_RuleSpec],
    quality_target: str,
    image_target: str,
    quality_allowed: set[str],
    review: RoleOutput,
) -> None:
    review_allowed = set(quality_allowed)
    review_allowed.add(f"report:{artifact.id}")
    if review.status == "blocked":
        # A valid blocked reviewer is a technical/review availability result,
        # not a malformed contract. Never turn it into a pass or a 502.
        await _set_asset(
            ctx,
            candidate.asset,
            "quarantined",
            "independent review unavailable",
            candidate.sha256,
        )
        await _set_job(ctx, candidate.job, "quarantined")
        await _block_stage(
            ctx, "REVIEW_UNAVAILABLE", _issue_code(review, "TECHNICAL_UNAVAILABLE")
        )
        return
    try:
        report_decision, report_result, image_decision, image_result = _validate_review(
            review,
            quality_target=quality_target,
            image_target=image_target,
            rules=rules,
            allowed=review_allowed,
        )
    except ApiError as exc:
        if exc.code == "REVIEW_INVALID":
            await _block_stage(ctx, "REVIEW_INVALID", "REVIEW_INVALID")
        else:
            await _fail_stage(ctx, "REVIEW_UNAVAILABLE", "REVIEW_UNAVAILABLE")
        raise
    report_checks = report_result
    if not isinstance(report_checks, list):
        raise _invalid_review()
    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision=report_decision,
        checks=report_checks,
        target_kind="artifact",
        raw=review.data,
    )
    if report_decision != "pass":
        await _block_stage(ctx, "IMAGE_NOT_APPROVED", "IMAGE_NOT_APPROVED")
        return
    approved_report = await latest_artifact(ctx, artifact.kind, status="approved")
    if approved_report is None:
        await _block_stage(ctx, "IMAGE_NOT_APPROVED", "IMAGE_NOT_APPROVED")
        return
    # 报告与图片是不同审批目标：报告批准只允许展示报告，绝不授权图片。
    await add_approval(
        ctx,
        approval_type="quality_report",
        artifact=approved_report,
        approved_by="output_reviewer",
        checks={
            "target_id": quality_target,
            "ruleset_version": RULESET_VERSION,
            "checks": report_checks,
        },
        model_fingerprint=review.model_fingerprint,
    )
    image_checks = image_result
    if not isinstance(image_checks, list):
        raise _invalid_review()
    all_quality_pass = (
        quality_data.get("decision") == "pass"
        and all(
            check.get("outcome") == "pass"
            for check in quality_data.get("checks", [])
            if check.get("rule_id") in {rule.rule_id for rule in rules if rule.applicable}
        )
    )
    if image_decision == "pass" and all_quality_pass:
        await _add_image_review(
            ctx,
            artifact_id=approved_report.id,
            decision="pass",
            checks=image_checks,
            raw=review.data or {},
            target_id=image_target,
        )
        await _set_asset(
            ctx,
            candidate.asset,
            "approved",
            "quality and independent review passed",
            candidate.sha256,
        )
        await add_approval(
            ctx,
            approval_type="image",
            artifact=approved_report,
            approved_by="output_reviewer",
            target_digest=candidate.sha256,
            checks={
                "asset_id": candidate.asset.id,
                "asset_digest": candidate.sha256,
                "quality_report_id": approved_report.id,
                "quality_target_id": quality_target,
                "image_target_id": image_target,
                "report_target_id": quality_target,
                "ruleset_version": RULESET_VERSION,
                "review_model_fingerprint": await latest_model_fingerprint(
                    ctx, "output_reviewer"
                ),
            },
        )
        await _set_job(
            ctx,
            candidate.job,
            "approved",
            result_asset_id=candidate.asset.id,
        )
        await set_stage(ctx, "S10", "active")
        return
    decision = image_decision if image_decision in {
        "pass",
        "revise",
        "reject",
        "unknown",
    } else "unknown"
    if image_decision == "pass":
        # A reviewer pass cannot override a failing quality report.
        decision = "reject" if quality_data.get("decision") == "fail" else "unknown"
    await _add_image_review(
        ctx,
        artifact_id=approved_report.id,
        decision=decision,
        checks=image_checks,
        raw=review.data or {},
        target_id=image_target,
    )
    await _set_asset(
        ctx,
        candidate.asset,
        "quarantined",
        "quality or independent review did not pass",
        candidate.sha256,
    )
    job_status = "rejected" if decision == "reject" else "quarantined"
    await _set_job(ctx, candidate.job, job_status)
    await _block_stage(ctx, "IMAGE_NOT_APPROVED", "IMAGE_NOT_APPROVED")


async def _retry_rejected_generation(
    ctx: WorkflowContext, payload: dict[str, Any]
) -> None:
    # s08 owns replacement job creation; import lazily because s08 imports s09.
    from app.workflow.stages import s08

    await s08._start_replacement(
        ctx,
        payload,
        issue_code="IMAGE_NOT_APPROVED",
        previous_status="rejected",
        previous_intent_status="failed",
        acknowledgement_key="acknowledge_new_attempt",
        acknowledgement_error="NEW_ATTEMPT_ACK_REQUIRED",
        acknowledgement_message="重新生成会新增一次受理计数并重新占用生成槽位，请明确确认后再操作。",
        request_prefix="v3-generation-quality-retry",
    )


async def _revise_prompt_and_retry(
    ctx: WorkflowContext, payload: dict[str, Any]
) -> None:
    note = payload.get("text")
    if not isinstance(note, str) or not note.strip():
        raise ApiError(422, "VALIDATION_ERROR", "请说明提示词调整内容")
    # Reuse S07's compile path but keep S09 as the current stage until the
    # replacement job has been accepted. Advancing to S08 before the intent
    # exists would expose submit_generation against a failed intent.
    from app.workflow.stages import s07, s08

    try:
        await s07._compile(
            ctx, note.strip()[:20000], advance=False, allow_known_rejection=True
        )
    except ApiError as exc:
        if exc.code in {"PROMPT_REQUIRES_RECONFIRMATION", "REQUIREMENT_DRIFT"}:
            raise
        # Other compile failures may have marked S07 failed/blocked. Keep the
        # current quality rejection actionable instead of stranding the run.
        if ctx.run is not None and ctx.run.stage == s07.STAGE:
            await set_stage(
                ctx,
                STAGE,
                "blocked",
                issue_code="IMAGE_NOT_APPROVED",
                template_id="IMAGE_NOT_APPROVED",
            )
        raise
    await s08._start_replacement(
        ctx,
        payload,
        issue_code="IMAGE_NOT_APPROVED",
        previous_status="rejected",
        previous_intent_status="failed",
        acknowledgement_key="acknowledge_new_attempt",
        acknowledgement_error="NEW_ATTEMPT_ACK_REQUIRED",
        acknowledgement_message="重新生成会新增一次受理计数并重新占用生成槽位，请明确确认后再操作。",
        request_prefix="v3-generation-quality-retry",
    )


async def handle(
    ctx: WorkflowContext, action: str, payload: dict[str, Any]
) -> None:
    if action not in ACTIONS:
        raise ApiError(
            409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
        )
    if ctx.run is None or ctx.run.stage != STAGE:
        raise ApiError(
            409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
        )
    allowed_statuses = {"active", "needs_input", "blocked"}
    if action == "recheck_candidate":
        # A durable candidate may still be rechecked after a role call failed
        # with a recoverable technical error; no new generation is requested.
        allowed_statuses.add("failed")
    if ctx.run.status not in allowed_statuses:
        raise ApiError(
            409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
        )
    if action == "retry_rejected_generation":
        await _retry_rejected_generation(ctx, payload)
        return
    if action == "revise_prompt_and_retry":
        await _revise_prompt_and_retry(ctx, payload)
        return
    artifact_kind = "quality_report"
    recheck_asset_id: str | None = None
    if action == "recheck_candidate":
        # A recheck is a review action. It is valid while the normal candidate
        # review is still active (for example after the quality-report budget
        # was consumed) and after the run is blocked for a rejected image.
        if not _can_recheck(ctx):
            raise ApiError(
                409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
            )
        raw_asset_id = payload.get("asset_id")
        if raw_asset_id is not None:
            if not isinstance(raw_asset_id, str) or not raw_asset_id.strip():
                raise ApiError(422, "VALIDATION_ERROR", "修正图标识无效")
            recheck_asset_id = raw_asset_id.strip()
        artifact_kind = "quality_recheck"
    try:
        candidate = await _load_candidate(
            ctx,
            asset_id=recheck_asset_id,
            allow_rejected_job=action == "recheck_candidate",
        )
    except ApiError as exc:
        if exc.code == "SERVICE_UNAVAILABLE":
            await _fail_stage(ctx, "SERVICE_UNAVAILABLE", "SERVICE_UNAVAILABLE")
        raise
    snapshot, snapshot_digest, execution_snapshot = await _confirmed_snapshot(ctx)
    reference, reference_usage, reference_bytes = await _reference_context(ctx, snapshot)
    if reference_usage != "none" and (reference is None or reference_bytes is None):
        # A declared reference must be readable before it can be used as
        # evidence. Otherwise a model could claim a product check it never saw.
        await _block_stage(ctx, "IMAGE_NOT_APPROVED", "UNREADABLE_IMAGE")
        return
    has_product = reference is not None and reference_usage == "product"
    rules = _rule_specs(has_product, execution_snapshot)
    size = str(
        snapshot.get("size")
        or ctx.project.image_size
        or ctx.settings.visual().image_size
        or "unknown"
    )
    rule_ids = tuple(rule.rule_id for rule in rules if rule.applicable)
    quality_target = f"quality_report:{candidate.asset.id}"
    image_target = f"image:{candidate.asset.id}"
    requirement_text = str(
        snapshot.get("product_description")
        or snapshot.get("purpose")
        or "按确认需求展示商品主体"
    )
    forbidden = _forbidden_requirement(snapshot)
    image_evidence = f"image:{candidate.asset.id}"
    requirement_evidence = f"requirement:{snapshot_digest}"
    technical_evidence = f"technical:{candidate.asset.id}"
    allowed = {
        image_evidence,
        requirement_evidence,
        technical_evidence,
    }
    if reference is not None:
        allowed.add(f"reference:{reference.id}")
    images = [OrganizeImage(data=candidate.data, mime_type=candidate.mime_type)]
    if reference_bytes is not None and reference is not None:
        images.append(OrganizeImage(data=reference_bytes, mime_type=reference.mime_type))
    rule_payload = _rule_payload(rules, snapshot, requirement_text, forbidden, size)
    try:
        quality = await run_role(
            ctx,
            "quality_compliance",
            STAGE,
            {
                "asset_id": candidate.asset.id,
                "asset_ref": image_evidence,
                "mime_type": candidate.mime_type,
                "width": candidate.width,
                "height": candidate.height,
                "technical": {
                    "sha256": candidate.sha256,
                    "mime_type": candidate.mime_type,
                    "width": candidate.width,
                    "height": candidate.height,
                    "expected_size": size,
                    "size_matches": _parse_expected_size(size)
                    == (candidate.width, candidate.height),
                },
                "rules": rule_payload,
                "reference_usage": reference_usage,
                "reference_asset_id": reference.id if reference is not None else None,
                "approved_inputs": {
                    "confirmed_snapshot": snapshot,
                    "final_brief": snapshot,
                    "execution_snapshot": execution_snapshot,
                },
            },
            approved_inputs={
                "confirmed_snapshot": snapshot,
                "final_brief": snapshot,
                "execution_snapshot": execution_snapshot,
                "reference_usage": reference_usage,
                "reference_asset_id": reference.id if reference is not None else None,
            },
            images=tuple(images),
            required_rule_ids=rule_ids,
            allowed_evidence_ids=tuple(sorted(allowed)),
        )
    except ApiError:
        await _fail_stage(ctx, "SERVICE_UNAVAILABLE", "SERVICE_UNAVAILABLE")
        raise
    if quality.status == "blocked":
        await _set_asset(
            ctx,
            candidate.asset,
            "quarantined",
            "quality blocked",
            candidate.sha256,
        )
        await _set_job(ctx, candidate.job, "quarantined")
        await _block_stage(
            ctx, "IMAGE_NOT_APPROVED", _issue_code(quality, "UNREADABLE_IMAGE")
        )
        return
    try:
        quality_data = _validate_quality(
            quality,
            asset=candidate.asset,
            rules=rules,
            allowed=allowed,
        )
    except ApiError:
        await _set_asset(
            ctx,
            candidate.asset,
            "quarantined",
            "quality contract invalid",
            candidate.sha256,
        )
        await _fail_stage(ctx, "SERVICE_UNAVAILABLE", "CONTRACT_ERROR")
        raise
    quality_data = _with_local_technical_result(
        quality_data,
        candidate=candidate,
        size=size,
        technical_evidence=technical_evidence,
    )
    await _review_and_apply(
        ctx,
        candidate=candidate,
        quality_data=quality_data,
        rules=rules,
        quality_target=quality_target,
        image_target=image_target,
        quality_allowed=allowed,
        snapshot=snapshot,
        execution_snapshot=execution_snapshot,
        size=size,
        rule_payload=rule_payload,
        artifact_kind=artifact_kind,
    )


__all__ = ["ACTIONS", "STAGE", "available", "handle"]

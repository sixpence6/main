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
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import (
    WorkflowContext,
    add_approval,
    add_review,
    approved_artifact,
    latest_artifact,
    latest_model_fingerprint,
    requirement_snapshot_digest,
    set_stage,
    start_artifact,
)
from app.workflow.tables import Artifact, ArtifactReview, GenerationJob

STAGE = "S09"
ACTIONS = ["review_candidate"]

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
    ("hard:size_format", "尺寸与图片格式符合本地技术校验", ("technical", "requirement"), True),
)
_RULE_ORDER = tuple(rule[0] for rule in _RULES)
_DEFAULT_REVIEW_RULE = "review:quality_report"
_REVIEW_RULE_IDS = frozenset(
    _RULE_ORDER
    + (
        _DEFAULT_REVIEW_RULE,
        "review:scope",
        "review:evidence",
        "review:consistency",
        "review:summary",
    )
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


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    if ctx.run is None or ctx.run.stage != STAGE:
        return []
    if ctx.run.status not in {"active", "needs_input", "blocked"}:
        return []
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


async def _load_candidate(ctx: WorkflowContext) -> _Candidate:
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
        asset = await session.get(Asset, job.result_asset_id)
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
    if job is not None and job.status == "rejected":
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


async def _confirmed_snapshot(ctx: WorkflowContext) -> tuple[dict[str, Any], str]:
    artifact = await approved_artifact(ctx, "final_brief")
    if artifact is None:
        artifact = await approved_artifact(ctx, "brief_draft")
    if artifact is not None:
        payload = _json_dict(artifact.payload_json)
        if payload:
            return payload, artifact.digest
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
    return payload, await requirement_snapshot_digest(ctx)


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
    texts = snapshot.get("texts")
    if isinstance(texts, list) and texts:
        return "；".join(str(item) for item in texts if isinstance(item, str))
    value = snapshot.get("text_content")
    if isinstance(value, str) and value:
        return value
    return "无确认画面文字，也不得出现额外文字或 Logo"


def _forbidden_requirement(snapshot: dict[str, Any]) -> str:
    forbidden = snapshot.get("forbidden")
    if isinstance(forbidden, list) and forbidden:
        return "；".join(str(item) for item in forbidden if isinstance(item, str))
    return "无确认禁止项"


def _rule_specs(has_product_reference: bool) -> list[_RuleSpec]:
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


def _rule_payload(
    rules: list[_RuleSpec],
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
            "requirement": (
                forbidden
                if rule.rule_id == "hard:forbidden"
                else size
                if rule.rule_id == "hard:size_format"
                else requirement_text
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


def _invalid_review() -> ApiError:
    return ApiError(502, "CONTRACT_ERROR", templates.render("REVIEW_UNAVAILABLE"))


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
    # A pass must cover every evidence type the rule declares. The server owns
    # this list, so the model cannot drop the reference or requirement evidence
    # from an otherwise passing check.
    return all(evidence_type in present_types for evidence_type in expected_types)


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


def _validate_review(
    output: RoleOutput,
    *,
    quality_target: str,
    image_target: str,
    rules: list[_RuleSpec],
    allowed: set[str],
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    if output.status != "ready" or output.data is None:
        raise _invalid_review()
    results = output.data.get("results")
    if not isinstance(results, list) or len(results) != 2:
        raise _invalid_review()
    by_target: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            raise _invalid_review()
        target_id = result.get("target_id")
        if target_id not in {quality_target, image_target} or target_id in by_target:
            raise _invalid_review()
        by_target[target_id] = result
    if set(by_target) != {quality_target, image_target}:
        raise _invalid_review()
    for target_id, result in by_target.items():
        checks = result.get("checks")
        if not isinstance(checks, list) or not checks:
            raise _invalid_review()
        seen: set[str] = set()
        for check in checks:
            if not isinstance(check, dict):
                raise _invalid_review()
            rule_id = check.get("rule_id")
            if not isinstance(rule_id, str) or rule_id in seen:
                raise _invalid_review()
            if rule_id not in _REVIEW_RULE_IDS:
                raise _invalid_review()
            seen.add(rule_id)
            outcome = check.get("outcome")
            if outcome not in {"pass", "fail", "unknown", "not_applicable"}:
                raise _invalid_review()
            evidence = check.get("evidence_ids")
            if not isinstance(evidence, list):
                raise _invalid_review()
            for evidence_id in evidence:
                if not isinstance(evidence_id, str) or evidence_id not in allowed:
                    raise _invalid_review()
        decision = result.get("decision")
        if decision not in {"pass", "revise", "reject", "unknown"}:
            raise _invalid_review()
        if target_id == quality_target:
            report_check = next(
                (item for item in checks if item.get("rule_id") == "review:quality_report"),
                None,
            )
            if report_check is None:
                raise _invalid_review()
            report_outcome = report_check.get("outcome")
            if report_outcome not in {"pass", "fail", "unknown"}:
                raise _invalid_review()
            if report_outcome == "pass" and not report_check.get("evidence_ids"):
                raise _invalid_review()
            if (decision == "pass") != (report_outcome == "pass"):
                raise _invalid_review()
            if decision == "pass":
                for check in checks:
                    if check.get("outcome") != "pass":
                        raise _invalid_review()
            continue
        # The independent reviewer must attest every applicable hard rule
        # exactly once, with evidence from this review call. A fail/unknown is
        # a legitimate review finding, not a contract violation: it simply
        # prevents an image approval below. A non-applicable hard rule, when
        # present, must be marked not_applicable.
        applicable_ids = {rule.rule_id for rule in rules if rule.applicable}
        by_rule = {
            item.get("rule_id"): item
            for item in checks
            if isinstance(item, dict) and item.get("rule_id") in _RULE_ORDER
        }
        for rule in rules:
            if not rule.applicable:
                continue
            check = by_rule.get(rule.rule_id)
            if check is None:
                raise _invalid_review()
            outcome = check.get("outcome")
            if outcome not in {"pass", "fail", "unknown"}:
                raise _invalid_review()
            evidence = check.get("evidence_ids")
            if not isinstance(evidence, list):
                raise _invalid_review()
            for evidence_id in evidence:
                if not isinstance(evidence_id, str) or evidence_id not in allowed:
                    raise _invalid_review()
                evidence_type = _evidence_type(evidence_id)
                if evidence_type is None or evidence_type not in rule.evidence_types:
                    raise _invalid_review()
            if outcome == "pass" and (
                not evidence
                or not _check_evidence(check, rule.evidence_types, allowed)
            ):
                raise _invalid_review()
        for check in checks:
            rule_id = check.get("rule_id")
            if (
                rule_id in _RULE_ORDER
                and rule_id not in applicable_ids
                and check.get("outcome") != "not_applicable"
            ):
                raise _invalid_review()
        if decision == "pass":
            for check in checks:
                rule_id = check.get("rule_id")
                expected = (
                    "not_applicable"
                    if rule_id in _RULE_ORDER and rule_id not in applicable_ids
                    else "pass"
                )
                if check.get("outcome") != expected:
                    raise _invalid_review()
        elif not any(
            check.get("outcome") in {"fail", "unknown"} for check in checks
        ):
            raise _invalid_review()
    return (
        str(by_target[quality_target].get("decision")),
        by_target[quality_target],
        str(by_target[image_target].get("decision")),
        by_target[image_target],
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
                    {"target_id": target_id, "checks": checks},
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
    size: str,
    rule_payload: list[dict[str, Any]],
    existing_artifact: Artifact | None = None,
) -> None:
    artifact = existing_artifact
    if artifact is None:
        artifact = await start_artifact(ctx, "quality_report", quality_data)
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
    reference, _, reference_bytes = await _reference_context(ctx, snapshot)
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
                "rules": rule_payload,
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
    except ApiError:
        await _fail_stage(ctx, "REVIEW_UNAVAILABLE", "REVIEW_UNAVAILABLE")
        raise
    report_checks = report_result.get("checks")
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
    approved_report = await latest_artifact(ctx, "quality_report", status="approved")
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
            "checks": report_checks,
        },
        model_fingerprint=review.model_fingerprint,
    )
    image_checks = image_result.get("checks")
    if not isinstance(image_checks, list):
        image_checks = []
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
                "review_model_fingerprint": await latest_model_fingerprint(
                    ctx, "output_reviewer"
                ),
            },
        )
        await _set_job(ctx, candidate.job, "approved")
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
    if ctx.run.status not in {"active", "needs_input", "blocked"}:
        raise ApiError(
            409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE")
        )
    try:
        candidate = await _load_candidate(ctx)
    except ApiError as exc:
        if exc.code == "SERVICE_UNAVAILABLE":
            await _fail_stage(ctx, "SERVICE_UNAVAILABLE", "SERVICE_UNAVAILABLE")
        raise
    snapshot, snapshot_digest = await _confirmed_snapshot(ctx)
    reference, reference_usage, reference_bytes = await _reference_context(ctx, snapshot)
    if reference_usage != "none" and (reference is None or reference_bytes is None):
        # A declared reference must be readable before it can be used as
        # evidence. Otherwise a model could claim a product check it never saw.
        await _block_stage(ctx, "IMAGE_NOT_APPROVED", "UNREADABLE_IMAGE")
        return
    has_product = reference is not None and reference_usage == "product"
    rules = _rule_specs(has_product)
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
    rule_payload = _rule_payload(rules, requirement_text, forbidden, size)
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
            },
            approved_inputs={
                "confirmed_snapshot": snapshot,
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
        size=size,
        rule_payload=rule_payload,
    )


__all__ = ["ACTIONS", "STAGE", "available", "handle"]

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.adapters.base import OrganizeImage
from app.db import read_session, write_transaction
from app.errors import ApiError, conflict, validation_error
from app.generation_guard import job_authorization_ref
from app.models import Asset, Generation, Message
from app.schemas import Brief
from app.services.assets import ImageValidationError, inspect_image, resolve_asset_file
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
from app.workflow.access import can_access_asset
from app.workflow.adjudication import (
    RULESET_VERSION,
    ProtocolIssue,
    RuleSet,
    governance_ruleset,
    review_output,
)
from app.workflow.contracts import ContractError, digest_payload, validate_data
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import (
    WorkflowContext,
    add_approval,
    add_review,
    create_run,
    set_stage,
    start_artifact,
)
from app.workflow.tables import (
    Approval,
    Artifact,
    Delivery,
    GenerationJob,
    LegacyItem,
)

STAGE = "S11"
ACTIONS = [
    "retrieve_original_result",
    "review_legacy_item",
]

LEGACY_TEXT_RULES = (
    "LEGACY_TARGET_IDENTITY",
    "LEGACY_SNAPSHOT",
    "LEGACY_SOURCE_BASIS",
    "LEGACY_CONTENT_UNCHANGED",
)
LEGACY_PROMPT_RULES = (
    "LEGACY_TARGET_IDENTITY",
    "LEGACY_SNAPSHOT",
    "LEGACY_SOURCE_BASIS",
    "LEGACY_PROMPT_UNCHANGED",
)
LEGACY_REPORT_RULES = (
    "LEGACY_REPORT_ACCURATE",
    "LEGACY_REPORT_EVIDENCE",
    "LEGACY_REPORT_SNAPSHOT",
)
LEGACY_DELIVERY_RULES = (
    "LEGACY_DELIVERY_ASSET",
    "LEGACY_DELIVERY_SNAPSHOT",
    "LEGACY_DELIVERY_SAFE",
    "LEGACY_DELIVERY_CAPTION",
)
LEGACY_APPROVAL_TYPES = {
    "message": "legacy_text",
    "generation": "legacy_generation_prompt",
}


def available(ctx: WorkflowContext) -> list[str]:
    if ctx.run is None or ctx.run.stage != STAGE:
        return []
    if ctx.run.status in {"active", "blocked", "failed"}:
        return ["retrieve_original_result", "review_legacy_item"]
    return []


def _item_id(payload: dict[str, Any]) -> str:
    if set(payload) != {"item_id"}:
        raise validation_error("请求参数不合法")
    value = payload.get("item_id")
    if not isinstance(value, str) or not value.strip():
        raise validation_error("请选择需要补审的历史内容")
    return value


def _content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    return digest_payload(snapshot)


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_snapshot(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        Brief.model_validate(payload)
    except Exception:
        return None
    if not payload.get("product_description") or not payload.get("purpose"):
        return None
    return payload


def _parse_snapshot_loose(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _contains_url(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in ("http://", "https://", "www.", "data:", "://"))


def _block(ctx: WorkflowContext, code: str, template_id: str = "LEGACY_UNREVIEWED") -> None:
    return None


async def _block_stage(
    ctx: WorkflowContext, code: str, template_id: str = "LEGACY_UNREVIEWED"
) -> None:
    await set_stage(ctx, STAGE, "blocked", issue_code=code, template_id=template_id)


async def _ensure_run(ctx: WorkflowContext) -> None:
    if ctx.run is None:
        await create_run(ctx, STAGE)
        return
    if ctx.run.stage != STAGE:
        raise conflict("ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    await set_stage(ctx, STAGE, "active")


async def _finish(ctx: WorkflowContext) -> None:
    await set_stage(ctx, STAGE, "completed")


def _review_unavailable() -> ApiError:
    return ApiError(503, "REVIEW_UNAVAILABLE", templates.render("REVIEW_UNAVAILABLE"))


def _service_unavailable() -> ApiError:
    return ApiError(503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE"))


def _review_ruleset(target_kind: str, required_rules: tuple[str, ...]) -> RuleSet:
    return governance_ruleset(
        target_kind,
        tuple((rule_id, "历史内容核验") for rule_id in required_rules),
        evidence_types=(
            "artifact",
            "report",
            "image",
            "requirement",
            "technical",
            "reference",
            "snapshot",
        ),
    )


def _review_invalid(message: str) -> ApiError:
    return ApiError(409, "REVIEW_INVALID", message)


def _validate_review(
    output: RoleOutput,
    target_id: str,
    required_rules: tuple[str, ...],
    allowed_evidence: set[str],
    *,
    target_kind: str,
) -> tuple[str, list[dict[str, Any]]]:
    if output.status != "ready" or output.data is None:
        raise _review_unavailable()
    try:
        adjudication = review_output(
            output.data,
            target_id=target_id,
            ruleset=_review_ruleset(target_kind, required_rules),
            allowed_evidence=allowed_evidence,
        )
    except ProtocolIssue as exc:
        raise _review_invalid(exc.message) from None
    return adjudication.decision, adjudication.check_payloads()

def _validate_quality(
    output: RoleOutput,
    asset_id: str,
    required_rules: tuple[str, ...],
    allowed_evidence: set[str],
) -> dict[str, Any] | None:
    if output.status != "ready" or output.data is None:
        return None
    data = output.data
    if data.get("asset_id") != asset_id:
        return None
    decision = data.get("decision")
    if decision not in {"pass", "fail", "unknown"}:
        return None
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        return None
    seen: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            return None
        rule_id = check.get("rule_id")
        if rule_id not in required_rules:
            return None
        evidence = check.get("evidence_ids")
        if not isinstance(evidence, list) or not evidence:
            return None
        if any(not isinstance(item, str) or item not in allowed_evidence for item in evidence):
            return None
        note = check.get("note")
        if isinstance(note, str) and _contains_url(note):
            return None
        if decision == "pass" and check.get("outcome") != "pass":
            return None
        seen.append(str(rule_id))
    if decision == "pass":
        if set(seen) != set(required_rules) or len(seen) != len(set(seen)):
            return None
    elif len(seen) != len(set(seen)):
        return None
    for field in ("user_summary",):
        value = data.get(field)
        if isinstance(value, str) and _contains_url(value):
            return None
    for field in ("soft_feedback", "limitations"):
        for value in data.get(field, []):
            if isinstance(value, str) and _contains_url(value):
                return None
    return data


def _validate_delivery(
    output: RoleOutput,
    asset_id: str,
    requirement_revision: int,
) -> dict[str, Any] | None:
    if output.status != "ready" or output.data is None:
        return None
    data = output.data
    if data.get("asset_id") != asset_id:
        return None
    if data.get("requirement_revision") != requirement_revision:
        return None
    hint = data.get("filename_hint")
    if not isinstance(hint, str) or not hint.strip():
        return None
    if any(token in hint for token in ("/", "\\", "://", "..")):
        return None
    caption = data.get("caption")
    if not isinstance(caption, str) or len(caption) > 800 or _contains_url(caption):
        return None
    try:
        validate_data("delivery_manifest", data)
    except ContractError:
        return None
    return data


async def _load_item(ctx: WorkflowContext, item_id: str) -> LegacyItem:
    async with read_session(ctx.session_factory) as session:
        item = await session.get(LegacyItem, item_id)
    if item is None or item.project_id != ctx.project_id:
        raise conflict("LEGACY_ITEM_NOT_FOUND", templates.render("ACTION_UNAVAILABLE"))
    return item


def _approval_checks(approval: Approval) -> dict[str, Any]:
    return _json_dict(approval.checks_json)


async def _has_active_legacy_approval(
    ctx: WorkflowContext, item: LegacyItem, content_digest: str
) -> bool:
    async with read_session(ctx.session_factory) as session:
        approvals = (
            await session.scalars(
                select(Approval).where(
                    Approval.project_id == ctx.project_id,
                    Approval.revoked_at.is_(None),
                    Approval.approval_type == LEGACY_APPROVAL_TYPES.get(item.kind, "legacy_text"),
                )
            )
        ).all()
    for approval in approvals:
        checks = _approval_checks(approval)
        if (
            checks.get("legacy_item_id") == item.id
            and checks.get("content_digest") == content_digest
            and approval.artifact_digest == content_digest
        ):
            return True
    return False


async def _asset_is_still_available(ctx: WorkflowContext, asset_id: str) -> bool:
    async with read_session(ctx.session_factory) as session:
        asset = await session.get(Asset, asset_id)
        if asset is None:
            return False
        decision = await can_access_asset(session, ctx.settings, asset, download=True)
    return decision.allow


async def _load_message(ctx: WorkflowContext, item: LegacyItem) -> Message | None:
    async with read_session(ctx.session_factory) as session:
        message = await session.get(Message, item.ref_id)
    if message is None or message.project_id != ctx.project_id or message.role != "assistant":
        await _block_stage(ctx, "LEGACY_TARGET_INVALID")
        return None
    return message


async def _load_generation(ctx: WorkflowContext, item: LegacyItem) -> Generation | None:
    async with read_session(ctx.session_factory) as session:
        generation = await session.get(Generation, item.ref_id)
    if generation is None or generation.project_id != ctx.project_id:
        await _block_stage(ctx, "LEGACY_TARGET_INVALID")
        return None
    return generation


async def _load_reference(
    ctx: WorkflowContext, reference_asset_id: str | None
) -> tuple[Asset, bytes, str] | None:
    if reference_asset_id is None:
        return None
    async with read_session(ctx.session_factory) as session:
        asset = await session.get(Asset, reference_asset_id)
    if asset is None or asset.project_id != ctx.project_id or asset.kind != "reference":
        await _block_stage(ctx, "LEGACY_REFERENCE_MISSING")
        return None
    validated = await _validate_asset_file(ctx, asset)
    if validated is None:
        return None
    data, _digest = validated
    return asset, data, _digest


async def _validate_asset_file(ctx: WorkflowContext, asset: Asset) -> tuple[bytes, str] | None:
    path = resolve_asset_file(ctx.settings, asset)
    if path is None:
        await _block_stage(ctx, "LEGACY_FILE_MISSING")
        return None
    try:
        data = path.read_bytes()
    except OSError:
        await _block_stage(ctx, "LEGACY_FILE_MISSING")
        return None
    digest = hashlib.sha256(data).hexdigest()
    if asset.sha256 and asset.sha256 != digest:
        await _block_stage(ctx, "LEGACY_DIGEST_MISMATCH")
        return None
    try:
        info = inspect_image(data)
    except ImageValidationError:
        await _block_stage(ctx, "LEGACY_IMAGE_INVALID")
        return None
    if (
        info.mime_type != asset.mime_type
        or info.width != asset.width
        or info.height != asset.height
    ):
        await _block_stage(ctx, "LEGACY_IMAGE_INVALID")
        return None
    return data, digest


async def _user_inputs(ctx: WorkflowContext, project_id: str) -> list[dict[str, str]]:
    async with read_session(ctx.session_factory) as session:
        messages = (
            await session.scalars(
                select(Message)
                .where(Message.project_id == project_id, Message.role == "user")
                .order_by(Message.sequence_no)
            )
        ).all()
    return [{"id": message.id, "content": message.content} for message in messages]


def _legacy_evidence(
    item: LegacyItem,
    content_digest: str,
    snapshot_digest: str,
    reference_id: str | None,
) -> set[str]:
    evidence = {item.id, content_digest, snapshot_digest}
    if reference_id:
        evidence.add(reference_id)
    return evidence


async def _review_text_target(
    ctx: WorkflowContext,
    *,
    item: LegacyItem,
    target_kind: str,
    content: str,
    snapshot: dict[str, Any],
    user_inputs: list[dict[str, str]],
    reference_id: str | None,
    rules: tuple[str, ...],
) -> tuple[str, list[dict[str, Any]], RoleOutput]:
    content_digest = _content_digest(content)
    snapshot_digest = _snapshot_digest(snapshot)
    allowed = _legacy_evidence(item, content_digest, snapshot_digest, reference_id)
    target = {
        "target_id": item.id,
        "target_kind": target_kind,
        "legacy_item_id": item.id,
        "content": content,
        "content_digest": content_digest,
        "snapshot": snapshot,
        "snapshot_digest": snapshot_digest,
        "user_inputs": user_inputs,
        "source": "v1_model_output",
        "approved_state": False,
    }
    try:
        output = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": "legacy_review",
                "target_kind": target_kind,
                "source": "v1_legacy",
                "ruleset_version": RULESET_VERSION,
                "rules": _review_ruleset(target_kind, rules).describe(),
            },
            review_targets=[target],
            required_rule_ids=rules,
            allowed_evidence_ids=tuple(sorted(allowed)),
        )
    except ApiError:
        await set_stage(
            ctx,
            STAGE,
            "failed",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise
    try:
        decision, checks = _validate_review(
            output, item.id, rules, allowed, target_kind=target_kind
        )
    except ApiError as exc:
        await set_stage(
            ctx,
            STAGE,
            "blocked" if exc.status_code == 409 else "failed",
            issue_code=exc.code,
            template_id=exc.code,
        )
        raise
    return decision, checks, output


async def _record_text_approval(
    ctx: WorkflowContext,
    *,
    item: LegacyItem,
    target_kind: str,
    content_digest: str,
    snapshot_digest: str,
    reference_digest: str,
    checks: list[dict[str, Any]],
    rules: tuple[str, ...],
    output: RoleOutput,
) -> None:
    now = format_timestamp(utc_now())
    approval_type = LEGACY_APPROVAL_TYPES.get(item.kind, "legacy_text")
    async with write_transaction(ctx.session_factory) as session:
        current = await session.get(LegacyItem, item.id)
        if current is None or current.project_id != ctx.project_id:
            raise conflict("LEGACY_ITEM_NOT_FOUND", templates.render("ACTION_UNAVAILABLE"))
        session.add(
            Approval(
                id=str(uuid4()),
                project_id=ctx.project_id,
                artifact_id=None,
                approval_type=approval_type,
                artifact_digest=content_digest,
                approved_by="output_reviewer",
                requirement_snapshot_digest=snapshot_digest,
                referenced_material_digest=reference_digest,
                prompt_version="2.0.0",
                rules_version="2.0.0",
                model_fingerprint=(output.model_fingerprint or "unavailable")[:200],
                checks_json=json.dumps(
                    {
                        "legacy_item_id": item.id,
                        "target_kind": target_kind,
                        "content_digest": content_digest,
                        "snapshot_digest": snapshot_digest,
                        "rule_ids": list(rules),
                        "ruleset_version": RULESET_VERSION,
                        "checks": checks,
                        "source": "v1_legacy",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                created_at=now,
            )
        )
        current.status = "approved"
        current.approved_by = "output_reviewer"
        current.reviewed_at = now
        current.note = f"{approval_type}_reviewed"
        await session.flush()


async def _review_text_like(ctx: WorkflowContext, item: LegacyItem) -> None:
    if item.kind == "message":
        message = await _load_message(ctx, item)
        if message is None:
            return
        content = message.content
        snapshot = _parse_snapshot(message.brief_snapshot_json)
        target_kind = "legacy_message"
        rules = LEGACY_TEXT_RULES
    else:
        generation = await _load_generation(ctx, item)
        if generation is None:
            return
        content = generation.prompt
        snapshot = _parse_snapshot(generation.brief_snapshot_json)
        target_kind = "legacy_prompt"
        rules = LEGACY_PROMPT_RULES
        if not content.strip():
            await _block_stage(ctx, "LEGACY_CONTENT_MISSING")
            return
    if not content.strip():
        await _block_stage(ctx, "LEGACY_CONTENT_MISSING")
        return
    if snapshot is None:
        await _block_stage(ctx, "LEGACY_SNAPSHOT_MISSING")
        return
    content_digest = _content_digest(content)
    if await _has_active_legacy_approval(ctx, item, content_digest):
        await _finish(ctx)
        return

    reference_id = (
        snapshot.get("reference_asset_id")
        if item.kind == "message"
        else generation.reference_asset_id
    )
    reference = await _load_reference(ctx, reference_id)
    if reference is None and reference_id is not None:
        return
    reference_digest = (
        reference[2] if reference is not None else digest_payload({"reference": "none"})
    )
    user_inputs = await _user_inputs(ctx, ctx.project_id)
    decision, checks, output = await _review_text_target(
        ctx,
        item=item,
        target_kind=target_kind,
        content=content,
        snapshot=snapshot,
        user_inputs=user_inputs,
        reference_id=reference_id,
        rules=rules,
    )
    if decision != "pass":
        await _block_stage(ctx, f"REVIEW_{decision.upper()}", "REVIEW_UNAVAILABLE")
        return
    await _record_text_approval(
        ctx,
        item=item,
        target_kind=target_kind,
        content_digest=content_digest,
        snapshot_digest=_snapshot_digest(snapshot),
        reference_digest=reference_digest,
        checks=checks,
        rules=rules,
        output=output,
    )
    await _finish(ctx)


def _image_rules(snapshot: dict[str, Any], has_reference: bool) -> tuple[str, ...]:
    rules = ["LEGACY_IMAGE_FILE", "LEGACY_IMAGE_SNAPSHOT", "LEGACY_IMAGE_SIZE"]
    rules.append("LEGACY_IMAGE_PRODUCT" if has_reference else "LEGACY_IMAGE_NO_REFERENCE")
    if str(snapshot.get("text_content") or "").strip():
        rules.append("LEGACY_IMAGE_TEXT")
    else:
        rules.append("LEGACY_IMAGE_NO_EXTRA_TEXT")
    return tuple(rules)


def _images(primary: tuple[bytes, str], reference: tuple[bytes, str] | None) -> tuple[Any, ...]:
    result: list[Any] = [OrganizeImage(data=primary[0], mime_type=primary[1])]
    if reference is not None:
        result.append(OrganizeImage(data=reference[0], mime_type=reference[1]))
    return tuple(result)


async def _quality_review(
    ctx: WorkflowContext,
    *,
    asset: Asset,
    asset_bytes: bytes,
    asset_digest: str,
    snapshot: dict[str, Any],
    snapshot_digest: str,
    reference: tuple[Asset, bytes, str] | None,
    rules: tuple[str, ...],
) -> tuple[dict[str, Any], Artifact] | None:
    reference_id = reference[0].id if reference is not None else None
    allowed = {asset.id, snapshot_digest}
    if reference_id:
        allowed.add(reference_id)
    images = _images(
        (asset_bytes, asset.mime_type),
        (reference[1], reference[0].mime_type) if reference is not None else None,
    )
    try:
        output = await run_role(
            ctx,
            "quality_compliance",
            STAGE,
            {
                "asset_id": asset.id,
                "asset_digest": asset_digest,
                "snapshot": snapshot,
                "snapshot_digest": snapshot_digest,
                "reference_asset_id": reference_id,
                "reference_usage": "product" if reference_id else "none",
                "source": "v1_legacy",
            },
            images=images,
            capability="VISION",
            required_rule_ids=rules,
            allowed_evidence_ids=tuple(sorted(allowed)),
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
    data = _validate_quality(output, asset.id, rules, allowed)
    if data is None:
        await _block_stage(ctx, "QUALITY_INVALID", "REVIEW_UNAVAILABLE")
        return None
    if data["decision"] != "pass":
        await _block_stage(ctx, "HARD_REQUIREMENT_FAILED", "REVIEW_UNAVAILABLE")
        return None
    try:
        artifact = await start_artifact(ctx, "quality_report", data)
    except ContractError:
        await _block_stage(ctx, "QUALITY_INVALID", "REVIEW_UNAVAILABLE")
        return None
    return data, artifact


async def _review_quality_report(
    ctx: WorkflowContext,
    *,
    artifact: Artifact,
    quality: dict[str, Any],
    asset: Asset,
    asset_bytes: bytes,
    snapshot: dict[str, Any],
    snapshot_digest: str,
    reference: tuple[Asset, bytes, str] | None,
) -> tuple[str, list[dict[str, Any]], RoleOutput] | None:
    reference_id = reference[0].id if reference is not None else None
    allowed = {artifact.id, asset.id, snapshot_digest}
    if reference_id:
        allowed.add(reference_id)
    images = _images(
        (asset_bytes, asset.mime_type),
        (reference[1], reference[0].mime_type) if reference is not None else None,
    )
    target = {
        "target_id": artifact.id,
        "target_kind": "quality_report",
        "candidate": quality,
        "asset_id": asset.id,
        "snapshot": snapshot,
        "snapshot_digest": snapshot_digest,
    }
    try:
        output = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": "quality_compliance",
                "target_kind": "quality_report",
                "source": "v1_legacy",
                "ruleset_version": RULESET_VERSION,
                "rules": _review_ruleset("quality_report", LEGACY_REPORT_RULES).describe(),
            },
            approved_inputs={"quality_report": quality},
            review_targets=[target],
            required_rule_ids=LEGACY_REPORT_RULES,
            allowed_evidence_ids=tuple(sorted(allowed)),
            images=images,
            capability="VISION",
        )
    except ApiError:
        await set_stage(
            ctx,
            STAGE,
            "failed",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise
    try:
        decision, checks = _validate_review(
            output,
            artifact.id,
            LEGACY_REPORT_RULES,
            allowed,
            target_kind="quality_report",
        )
    except ApiError as exc:
        await set_stage(
            ctx,
            STAGE,
            "blocked" if exc.status_code == 409 else "failed",
            issue_code=exc.code,
            template_id=exc.code,
        )
        raise
    return decision, checks, output


async def _review_image_target(
    ctx: WorkflowContext,
    *,
    asset: Asset,
    asset_bytes: bytes,
    asset_digest: str,
    snapshot: dict[str, Any],
    snapshot_digest: str,
    quality: dict[str, Any],
    quality_artifact: Artifact,
    reference: tuple[Asset, bytes, str] | None,
    rules: tuple[str, ...],
) -> tuple[str, list[dict[str, Any]], RoleOutput] | None:
    reference_id = reference[0].id if reference is not None else None
    allowed = {asset.id, quality_artifact.id, snapshot_digest}
    if reference_id:
        allowed.add(reference_id)
    images = _images(
        (asset_bytes, asset.mime_type),
        (reference[1], reference[0].mime_type) if reference is not None else None,
    )
    target = {
        "target_id": asset.id,
        "target_kind": "image",
        "asset_id": asset.id,
        "asset_digest": asset_digest,
        "quality_report": quality,
        "snapshot": snapshot,
        "snapshot_digest": snapshot_digest,
    }
    try:
        output = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": "quality_compliance",
                "target_kind": "image",
                "source": "v1_legacy",
                "ruleset_version": RULESET_VERSION,
                "rules": _review_ruleset("image", rules).describe(),
            },
            review_targets=[target],
            required_rule_ids=rules,
            allowed_evidence_ids=tuple(sorted(allowed)),
            images=images,
            capability="VISION",
        )
    except ApiError:
        await set_stage(
            ctx,
            STAGE,
            "failed",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise
    try:
        decision, checks = _validate_review(
            output, asset.id, rules, allowed, target_kind="image"
        )
    except ApiError as exc:
        await set_stage(
            ctx,
            STAGE,
            "blocked" if exc.status_code == 409 else "failed",
            issue_code=exc.code,
            template_id=exc.code,
        )
        raise
    return decision, checks, output


async def _delivery_review(
    ctx: WorkflowContext,
    *,
    asset: Asset,
    asset_digest: str,
    snapshot: dict[str, Any],
    snapshot_digest: str,
    quality: dict[str, Any],
    requirement_revision: int,
) -> tuple[dict[str, Any], Artifact] | None:
    try:
        output = await run_role(
            ctx,
            "delivery_manager",
            STAGE,
            {
                "asset_id": asset.id,
                "asset_digest": asset_digest,
                "requirement_revision": requirement_revision,
                "snapshot": snapshot,
                "quality_summary": quality.get("user_summary", ""),
                "source": "v1_legacy",
            },
            approved_inputs={"quality_report": quality},
            required_rule_ids=LEGACY_DELIVERY_RULES,
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
    data = _validate_delivery(output, asset.id, requirement_revision)
    if data is None:
        await _block_stage(ctx, "DELIVERY_INVALID", "REVIEW_UNAVAILABLE")
        return None
    try:
        artifact = await start_artifact(ctx, "delivery_manifest", data)
    except ContractError:
        await _block_stage(ctx, "DELIVERY_INVALID", "REVIEW_UNAVAILABLE")
        return None

    allowed = {artifact.id, asset.id, snapshot_digest, quality.get("asset_id", asset.id)}
    target = {
        "target_id": artifact.id,
        "target_kind": "delivery_manifest",
        "candidate": data,
        "asset_id": asset.id,
        "asset_digest": asset_digest,
        "snapshot": snapshot,
        "snapshot_digest": snapshot_digest,
    }
    try:
        review = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": "delivery_manager",
                "target_kind": "delivery_manifest",
                "ruleset_version": RULESET_VERSION,
                "rules": _review_ruleset("delivery_manifest", LEGACY_DELIVERY_RULES).describe(),
            },
            approved_inputs={"delivery_manifest": data},
            review_targets=[target],
            required_rule_ids=LEGACY_DELIVERY_RULES,
            allowed_evidence_ids=tuple(sorted(allowed)),
        )
    except ApiError:
        await set_stage(
            ctx,
            STAGE,
            "failed",
            issue_code="REVIEW_UNAVAILABLE",
            template_id="REVIEW_UNAVAILABLE",
        )
        raise
    try:
        decision, checks = _validate_review(
            review,
            artifact.id,
            LEGACY_DELIVERY_RULES,
            allowed,
            target_kind="delivery_manifest",
        )
    except ApiError as exc:
        await set_stage(
            ctx,
            STAGE,
            "blocked" if exc.status_code == 409 else "failed",
            issue_code=exc.code,
            template_id=exc.code,
        )
        raise
    if decision != "pass":
        await add_review(
            ctx,
            artifact,
            reviewer_role="output_reviewer",
            decision=decision,
            checks={"checks": checks, "rule_ids": list(LEGACY_DELIVERY_RULES)},
        )
        await _block_stage(ctx, f"REVIEW_{decision.upper()}", "REVIEW_UNAVAILABLE")
        return None
    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={"checks": checks, "rule_ids": list(LEGACY_DELIVERY_RULES)},
    )
    return data, artifact


async def _publish_legacy_image(
    ctx: WorkflowContext,
    *,
    item: LegacyItem,
    asset: Asset,
    asset_digest: str,
    generation: Generation,
    delivery_data: dict[str, Any],
    delivery_artifact: Artifact,
    quality_artifact: Artifact,
    checks: list[dict[str, Any]],
    rules: tuple[str, ...],
    model_fingerprint: str,
    snapshot_digest: str,
    reference_digest: str,
) -> None:
    now = format_timestamp(utc_now())
    filename = f"{ctx.project_id}-legacy-{asset.id[:8]}.png"[:120]
    caption = str(delivery_data.get("caption") or "")[:800]
    async with write_transaction(ctx.session_factory) as session:
        stored_asset = await session.get(Asset, asset.id)
        stored_item = await session.get(LegacyItem, item.id)
        if stored_asset is None or stored_item is None:
            raise conflict("LEGACY_ITEM_NOT_FOUND", templates.render("ACTION_UNAVAILABLE"))
        stored_asset.sha256 = asset_digest
        stored_asset.review_status = "approved"
        stored_asset.review_note = "legacy_reviewed"
        stored_item.status = "approved"
        stored_item.approved_by = "output_reviewer"
        stored_item.reviewed_at = now
        stored_item.note = "legacy_image_reviewed"

        job = await session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.project_id == ctx.project_id,
                GenerationJob.result_asset_id == asset.id,
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        request_id = str(uuid4())
        request_digest_value = digest_payload(
            {"asset_id": asset.id, "snapshot": snapshot_digest}
        )
        authorization_ref = job_authorization_ref(
            project_id=ctx.project_id,
            run_id=ctx.run_id,
            request_id=request_id,
            request_digest=request_digest_value,
            confirmed_snapshot_digest=snapshot_digest,
        )
        if job is None:
            job = GenerationJob(
                id=str(uuid4()),
                project_id=ctx.project_id,
                run_id=ctx.run_id,
                owner_account_id=ctx.project.owner_account_id,
                operator_account_id=(
                    ctx.principal.account_id if ctx.principal is not None else None
                ),
                authorization_ref=authorization_ref,
                generation_id=None,
                request_id=request_id,
                request_digest=request_digest_value,
                prompt_artifact_id=None,
                confirmed_snapshot_digest=snapshot_digest,
                status="published",
                submit_attempted=0,
                remote_task_id=None,
                result_asset_id=asset.id,
                error_code=None,
                error_message=None,
                created_at=now,
                updated_at=now,
            )
            session.add(job)
            await session.flush()
        else:
            job.status = "published"
            job.result_asset_id = asset.id
            job.confirmed_snapshot_digest = snapshot_digest
            job.owner_account_id = ctx.project.owner_account_id
            job.operator_account_id = (
                ctx.principal.account_id if ctx.principal is not None else None
            )
            job.authorization_ref = authorization_ref
            job.updated_at = now

        delivery = await session.scalar(
            select(Delivery).where(Delivery.asset_id == asset.id).limit(1)
        )
        if delivery is None:
            session.add(
                Delivery(
                    id=str(uuid4()),
                    project_id=ctx.project_id,
                    generation_job_id=job.id,
                    asset_id=asset.id,
                    manifest_artifact_id=delivery_artifact.id,
                    status="published",
                    filename=filename,
                    caption=caption,
                    asset_digest=asset_digest,
                    approved_at=now,
                    published_at=now,
                    created_at=now,
                )
            )
        else:
            delivery.generation_job_id = job.id
            delivery.manifest_artifact_id = delivery_artifact.id
            delivery.status = "published"
            delivery.filename = filename
            delivery.caption = caption
            delivery.asset_digest = asset_digest
            delivery.approved_at = now
            delivery.published_at = now
        await session.flush()
        await add_approval(
            ctx,
            approval_type="image",
            artifact=quality_artifact,
            approved_by="output_reviewer",
            target_digest=asset_digest,
            checks={
                "legacy_item_id": item.id,
                "asset_id": asset.id,
                "asset_digest": asset_digest,
                "quality_report_id": quality_artifact.id,
                "rule_ids": list(rules),
                "ruleset_version": RULESET_VERSION,
                "checks": checks,
                "source": "v1_legacy",
            },
            requirement_digest=snapshot_digest,
            material_digest=reference_digest,
            model_fingerprint=model_fingerprint,
            session=session,
        )
        await add_approval(
            ctx,
            approval_type="delivery_manifest",
            artifact=delivery_artifact,
            approved_by="output_reviewer",
            checks={
                "legacy_item_id": item.id,
                "asset_id": asset.id,
                "asset_digest": asset_digest,
                "manifest_artifact_id": delivery_artifact.id,
                "ruleset_version": RULESET_VERSION,
                "source": "v1_legacy",
            },
            requirement_digest=snapshot_digest,
            material_digest=reference_digest,
            model_fingerprint=model_fingerprint,
            session=session,
        )


async def _review_legacy_image(ctx: WorkflowContext, item: LegacyItem) -> None:
    async with read_session(ctx.session_factory) as session:
        asset = await session.get(Asset, item.ref_id)
        generation = await session.scalar(
            select(Generation)
            .where(
                Generation.project_id == ctx.project_id,
                Generation.result_asset_id == item.ref_id,
            )
            .order_by(Generation.version_no.desc())
            .limit(1)
        )
    if asset is None or asset.project_id != ctx.project_id or asset.kind != "generated":
        await _block_stage(ctx, "LEGACY_TARGET_INVALID")
        return
    if generation is None:
        await _block_stage(ctx, "LEGACY_SNAPSHOT_MISSING")
        return
    snapshot = _parse_snapshot(generation.brief_snapshot_json)
    if snapshot is None:
        await _block_stage(ctx, "LEGACY_SNAPSHOT_MISSING")
        return
    if item.status == "approved" and await _asset_is_still_available(ctx, asset.id):
        await _finish(ctx)
        return
    validated = await _validate_asset_file(ctx, asset)
    if validated is None:
        return
    asset_bytes, asset_digest = validated
    reference = await _load_reference(ctx, generation.reference_asset_id)
    if reference is None and generation.reference_asset_id is not None:
        return
    reference_digest = (
        reference[2] if reference is not None else digest_payload({"reference": "none"})
    )
    rules = _image_rules(snapshot, reference is not None)
    snapshot_digest = _snapshot_digest(snapshot)
    quality_result = await _quality_review(
        ctx,
        asset=asset,
        asset_bytes=asset_bytes,
        asset_digest=asset_digest,
        snapshot=snapshot,
        snapshot_digest=snapshot_digest,
        reference=reference,
        rules=rules,
    )
    if quality_result is None:
        return
    quality, quality_artifact = quality_result
    report_review = await _review_quality_report(
        ctx,
        artifact=quality_artifact,
        quality=quality,
        asset=asset,
        asset_bytes=asset_bytes,
        snapshot=snapshot,
        snapshot_digest=snapshot_digest,
        reference=reference,
    )
    if report_review is None:
        return
    report_decision, report_checks, _ = report_review
    if report_decision != "pass":
        await add_review(
            ctx,
            quality_artifact,
            reviewer_role="output_reviewer",
            decision=report_decision,
            checks={"checks": report_checks, "rule_ids": list(LEGACY_REPORT_RULES)},
        )
        await _block_stage(ctx, f"REVIEW_{report_decision.upper()}", "REVIEW_UNAVAILABLE")
        return
    await add_review(
        ctx,
        quality_artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={"checks": report_checks, "rule_ids": list(LEGACY_REPORT_RULES)},
    )
    quality_artifact.status = "approved"

    image_review = await _review_image_target(
        ctx,
        asset=asset,
        asset_bytes=asset_bytes,
        asset_digest=asset_digest,
        snapshot=snapshot,
        snapshot_digest=snapshot_digest,
        quality=quality,
        quality_artifact=quality_artifact,
        reference=reference,
        rules=rules,
    )
    if image_review is None:
        return
    image_decision, image_checks, image_output = image_review
    if image_decision != "pass":
        await _block_stage(ctx, f"REVIEW_{image_decision.upper()}", "REVIEW_UNAVAILABLE")
        return

    requirement_revision = max(1, int(generation.confirmed_revision or 1))
    delivery_result = await _delivery_review(
        ctx,
        asset=asset,
        asset_digest=asset_digest,
        snapshot=snapshot,
        snapshot_digest=snapshot_digest,
        quality=quality,
        requirement_revision=requirement_revision,
    )
    if delivery_result is None:
        return
    delivery_data, delivery_artifact = delivery_result
    delivery_artifact.status = "approved"
    await _publish_legacy_image(
        ctx,
        item=item,
        asset=asset,
        asset_digest=asset_digest,
        generation=generation,
        delivery_data=delivery_data,
        delivery_artifact=delivery_artifact,
        quality_artifact=quality_artifact,
        checks=image_checks,
        rules=rules,
        model_fingerprint=image_output.model_fingerprint,
        snapshot_digest=snapshot_digest,
        reference_digest=reference_digest,
    )
    await _finish(ctx)


async def _review_legacy_item(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    item_id = _item_id(payload)
    item = await _load_item(ctx, item_id)
    await _ensure_run(ctx)
    if item.kind in {"message", "generation"}:
        await _review_text_like(ctx, item)
        return
    if item.kind == "asset":
        await _review_legacy_image(ctx, item)
        return
    await _block_stage(ctx, "LEGACY_TARGET_INVALID")


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if action == "review_legacy_item":
        await _review_legacy_item(ctx, payload)
        return
    if action == "retrieve_original_result":
        from app.workflow.stages import s11_retrieval

        await s11_retrieval.retrieve(ctx, payload)
        return
    raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))


__all__ = [
    "STAGE",
    "ACTIONS",
    "LEGACY_TEXT_RULES",
    "LEGACY_PROMPT_RULES",
    "LEGACY_REPORT_RULES",
    "LEGACY_DELIVERY_RULES",
    "available",
    "handle",
]

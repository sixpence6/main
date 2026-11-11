from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.acceptance import GenerationIntent, release_slots_in_session
from app.adapters.base import OrganizeImage
from app.auth.audit import write_audit
from app.db import read_session, write_transaction
from app.errors import ApiError, conflict, validation_error
from app.generation_guard import (
    GenerationGuardError,
    ensure_job_active,
    ensure_job_active_in_session,
)
from app.models import Asset, Generation, Message, Project
from app.services.assets import (
    ImageValidationError,
    delete_asset_file,
    inspect_image,
    resolve_asset_file,
    write_asset_file,
)
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
from app.workflow.access import asset_approval_matches
from app.workflow.adjudication import (
    RULESET_VERSION,
    Adjudication,
    ProtocolIssue,
    gate_mode_for_target,
    governance_ruleset,
    review_output,
)
from app.workflow.contracts import DeliveryData, digest_payload
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import (
    WorkflowContext,
    add_approval,
    add_review,
    approved_artifact,
    latest_approval,
    revoke_active_approval_types,
    set_stage,
    start_artifact,
)
from app.workflow.tables import Approval, Artifact, Delivery, GenerationJob, WorkflowRun

STAGE = "S10"
ACTIONS = [
    "prepare_delivery",
    "approve_delivery",
    "publish_delivery",
    "request_new_version",
]

# 交付清单独立审查的固定规则；模型不能少报、重复或伪造证据。
_MANIFEST_RULES = (
    "G07_MANIFEST_ASSET",
    "G07_MANIFEST_REQUIREMENT",
    "G07_MANIFEST_EVIDENCE",
    "G07_MANIFEST_FILENAME",
    "G07_MANIFEST_CAPTION",
)
_MANIFEST_RULESET = governance_ruleset(
    "delivery_manifest",
    tuple((rule_id, "交付清单核验") for rule_id in _MANIFEST_RULES),
    evidence_types=("artifact", "report", "image"),
)
_EXECUTABLE_SUFFIXES = {
    ".exe",
    ".bat",
    ".cmd",
    ".sh",
    ".js",
    ".php",
    ".py",
    ".msi",
    ".scr",
    ".com",
    ".jar",
}
_FORBIDDEN_CAPTION_MARKERS = (
    "://",
    "保证",
    "必过",
    "绝对保真",
    "一定通过",
    "平台保证",
    "100%",
)
_CAPTION_NEGATION = re.compile(r"(?:不|无法|不能|不得|禁止|避免|没有|无)[^；;。\n]{0,8}$")


def _caption_marker_is_claim(value: str, marker: str) -> bool:
    """Return True when a forbidden caption marker is asserted, not negated.

    A truthful disclaimer such as "不保证商品绝对真实" must be allowed; only
    an unnegated promise ("保证平台必过") is a prohibited claim.
    """
    start = 0
    while True:
        index = value.find(marker, start)
        if index < 0:
            return False
        prefix = value[max(0, index - 16) : index]
        if not _CAPTION_NEGATION.search(prefix):
            return True
        start = index + len(marker)


def _action_unavailable() -> ApiError:
    return ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))


def _asset_unavailable() -> ApiError:
    return ApiError(409, "ASSET_UNAVAILABLE", templates.render("ASSET_UNAVAILABLE"))


def _image_not_approved() -> ApiError:
    return ApiError(409, "IMAGE_NOT_APPROVED", templates.render("IMAGE_NOT_APPROVED"))


def _digest_mismatch() -> ApiError:
    return ApiError(409, "DIGEST_MISMATCH", "资产摘要不一致，已阻止交付")


def _requirement_changed() -> ApiError:
    return ApiError(409, "REQUIREMENT_CHANGED", "原需求快照不一致，请重新审查")


def _manifest_invalid() -> ApiError:
    return ApiError(409, "DELIVERY_MANIFEST_INVALID", "交付清单不符合安全要求")


def _parse_size(value: str | None) -> tuple[int, int] | None:
    if not value or "x" not in value:
        return None
    left, right = value.split("x", 1)
    if not left.isdigit() or not right.isdigit():
        return None
    width, height = int(left), int(right)
    if width <= 0 or height <= 0:
        return None
    return width, height


def _file_extension(mime_type: str) -> str:
    return "png" if mime_type == "image/png" else "jpg"


def _safe_filename_hint(value: str) -> bool:
    if not value or len(value) > 120:
        return False
    lowered = value.lower()
    if any(marker in lowered for marker in ("/", "\\", "://", "..")):
        return False
    if ":" in value:
        return False
    suffix = Path(value).suffix.lower()
    return suffix not in _EXECUTABLE_SUFFIXES


def _safe_caption(value: str) -> bool:
    if len(value) > 800:
        return False
    lowered = value.lower()
    return not any(
        _caption_marker_is_claim(lowered, marker.lower())
        for marker in _FORBIDDEN_CAPTION_MARKERS
    )


async def _load_job_and_asset(ctx: WorkflowContext) -> tuple[GenerationJob, Asset]:
    async with ctx.session_factory() as session:
        job = await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.run_id == ctx.run_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        if job is None or not job.result_asset_id:
            raise _asset_unavailable()
        if job.status not in {"approved", "published"}:
            raise _image_not_approved()
        asset = await session.get(Asset, job.result_asset_id)
    if (
        asset is None
        or asset.project_id != ctx.project_id
        or asset.kind != "generated"
        or asset.review_status != "approved"
        or not asset.sha256
    ):
        raise _image_not_approved()
    return job, asset


async def _require_image_approval(
    ctx: WorkflowContext, asset: Asset, job: GenerationJob
) -> Approval:
    approval = await latest_approval(ctx, "image")
    if approval is None:
        raise _image_not_approved()
    if (
        approval.approved_by != "output_reviewer"
        or approval.rules_version != "2.0.0"
        or approval.prompt_version != "2.0.0"
        or approval.requirement_snapshot_digest != job.confirmed_snapshot_digest
    ):
        raise _asset_unavailable()
    async with ctx.session_factory() as session:
        if not await asset_approval_matches(session, asset, approval):
            raise _digest_mismatch()
    return approval


async def _verify_image_file(ctx: WorkflowContext, asset: Asset) -> tuple[bytes, Any]:
    path = resolve_asset_file(ctx.settings, asset)
    if path is None:
        raise _asset_unavailable()
    data = await asyncio.to_thread(path.read_bytes)
    if hashlib.sha256(data).hexdigest() != asset.sha256:
        raise _digest_mismatch()
    try:
        info = inspect_image(data)
    except ImageValidationError:
        raise _asset_unavailable() from None
    from app.final_confirmation import FinalAuthorization
    async with ctx.session_factory() as session:
        frozen_generation = await session.scalar(select(Generation).join(
            FinalAuthorization, FinalAuthorization.generation_id == Generation.id
        ).where(Generation.project_id == ctx.project_id, Generation.result_asset_id == asset.id))
    expected = _parse_size(
        frozen_generation.image_size if frozen_generation else ctx.project.image_size
    )
    if expected is None:
        raise _asset_unavailable()
    if (info.width, info.height) != expected or (
        asset.width,
        asset.height,
    ) != expected:
        raise _digest_mismatch()
    return data, info


async def _require_manifest_evidence(
    ctx: WorkflowContext, asset: Asset, job: GenerationJob
) -> tuple[Any, Approval, dict[str, Any]]:
    await _require_image_approval(ctx, asset, job)
    artifact = await approved_artifact(ctx, "delivery_manifest")
    if artifact is None:
        raise ApiError(409, "ARTIFACT_NOT_APPROVED", "交付清单尚未通过独立审查")
    approval = await latest_approval(ctx, "delivery_manifest")
    if approval is None:
        raise ApiError(409, "APPROVAL_MISSING", "缺少交付清单批准")
    if (
        approval.approved_by != "output_reviewer"
        or approval.artifact_id != artifact.id
        or not approval.artifact_digest
        or approval.artifact_digest != artifact.digest
        or approval.requirement_snapshot_digest != job.confirmed_snapshot_digest
        or approval.rules_version != "2.0.0"
        or approval.prompt_version != "2.0.0"
    ):
        raise _asset_unavailable()
    try:
        payload = DeliveryData.model_validate_json(artifact.payload_json)
    except Exception:
        raise _manifest_invalid() from None
    if digest_payload(payload.model_dump(mode="json")) != artifact.digest:
        raise _digest_mismatch()
    if payload.asset_id != asset.id or payload.requirement_revision != ctx.run.requirement_revision:
        raise _requirement_changed()
    return artifact, approval, payload.model_dump(mode="json")


def _validate_review(
    output: RoleOutput, target_id: str, allowed_evidence: set[str]
) -> Adjudication:
    if output.status != "ready" or output.data is None:
        raise ApiError(
            503, "REVIEW_UNAVAILABLE", templates.render("REVIEW_UNAVAILABLE")
        )
    try:
        adjudication = review_output(
            output.data,
            target_id=target_id,
            ruleset=_MANIFEST_RULESET,
            allowed_evidence=allowed_evidence,
        )
    except ProtocolIssue as exc:
        raise ApiError(409, "REVIEW_INVALID", exc.message) from None
    if not adjudication.approved:
        raise ApiError(409, "REVIEW_REJECTED", templates.render("REVIEW_REJECTED"))
    return adjudication


async def _accept_manifest_review(
    ctx: WorkflowContext,
    *,
    artifact: Artifact,
    asset: Asset,
    job: GenerationJob,
    target_id: str,
    review: RoleOutput,
    adjudication: Adjudication,
) -> None:
    """Persist one passing manifest review and its approval exactly once."""
    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks=adjudication.review_checks(),
        target_kind="artifact",
        raw=review.data,
    )
    artifact.status = "approved"
    await add_approval(
        ctx,
        approval_type="delivery_manifest",
        artifact=artifact,
        approved_by="output_reviewer",
        checks={
            "asset_id": asset.id,
            "asset_digest": asset.sha256,
            "target_id": target_id,
            "requirement_revision": ctx.run.requirement_revision,
            "ruleset_version": RULESET_VERSION,
        },
        requirement_digest=job.confirmed_snapshot_digest,
        model_fingerprint=review.model_fingerprint,
    )
    await set_stage(
        ctx,
        STAGE,
        "needs_input",
        issue_code="DELIVERY_MANIFEST_READY",
        message="交付清单已通过独立审查，请确认发布。",
        template_id="WAIT_STAGE",
        params={"stage_label": "准备交付"},
    )


async def _requirement_payload(ctx: WorkflowContext, job: GenerationJob) -> dict[str, Any]:
    """只使用生成确认时的不可变需求快照；找不到同摘要快照就拒绝。"""
    async with ctx.session_factory() as session:
        artifact = await session.scalar(
            select(Artifact)
            .where(
                Artifact.run_id == ctx.run_id,
                Artifact.kind.in_(("final_brief", "brief_draft")),
                Artifact.status == "approved",
                Artifact.digest == job.confirmed_snapshot_digest,
            )
            .order_by(Artifact.created_at.desc())
            .limit(1)
        )
    if artifact is not None:
        try:
            brief = json.loads(artifact.payload_json)
        except ValueError:
            raise _requirement_changed() from None
        if not isinstance(brief, dict):
            raise _requirement_changed()
        return {
            "revision": ctx.run.requirement_revision,
            "snapshot_digest": job.confirmed_snapshot_digest,
            "brief": brief,
        }
    brief = {
        "product_description": ctx.project.product_description or "",
        "purpose": ctx.project.purpose or "",
        "style": ctx.project.style or "",
        "composition": ctx.project.composition or "",
        "text_content": ctx.project.text_content or "",
    }
    if digest_payload(brief) != job.confirmed_snapshot_digest:
        raise _requirement_changed()
    return {
        "revision": ctx.run.requirement_revision,
        "snapshot_digest": job.confirmed_snapshot_digest,
        "brief": brief,
    }


async def _accept_manifest_without_content_review(
    ctx: WorkflowContext,
    *,
    artifact: Artifact,
    asset: Asset,
    job: GenerationJob,
    target_id: str,
) -> None:
    """Approve one locally validated manifest when the content gate is off.

    The delivery file itself is still verified byte-for-byte against the
    approved image before this helper is called. The audit row says content
    review was skipped; it must never look like a model verdict.
    """
    checks = [
        {
            "rule_id": rule_id,
            "outcome": "pass",
            "evidence_ids": [asset.id, artifact.id],
            "note": "门禁已关闭，仅执行本地交付校验",
        }
        for rule_id in _MANIFEST_RULES
    ]
    audit = {
        "gate_mode": "off",
        "content_review": "skipped",
        "technical_check": "pass",
        "asset_id": asset.id,
        "asset_digest": asset.sha256,
        "artifact_id": artifact.id,
        "target_id": target_id,
    }
    await add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks=checks,
        target_kind="artifact",
        raw=audit,
    )
    artifact.status = "approved"
    await add_approval(
        ctx,
        approval_type="delivery_manifest",
        artifact=artifact,
        approved_by="output_reviewer",
        checks={
            "asset_id": asset.id,
            "asset_digest": asset.sha256,
            "target_id": target_id,
            "requirement_revision": ctx.run.requirement_revision,
            "ruleset_version": RULESET_VERSION,
            "gate_mode": "off",
            "content_review": "skipped",
        },
        requirement_digest=job.confirmed_snapshot_digest,
        model_fingerprint="gate-off:local-delivery-only",
    )
    await set_stage(
        ctx,
        STAGE,
        "needs_input",
        issue_code="DELIVERY_MANIFEST_READY",
        message="交付清单已完成本地校验，请确认发布。",
        template_id="WAIT_STAGE",
        params={"stage_label": "准备交付"},
    )


async def _prepare(ctx: WorkflowContext) -> None:
    job, asset = await _load_job_and_asset(ctx)
    image_approval = await _require_image_approval(ctx, asset, job)
    data, info = await _verify_image_file(ctx, asset)

    # 已有同图片交付清单时直接复用，不重复调用模型。
    existing = await approved_artifact(ctx, "delivery_manifest")
    if existing is not None:
        await _require_manifest_evidence(ctx, asset, job)
        await set_stage(
            ctx,
            STAGE,
            "needs_input",
            issue_code="DELIVERY_MANIFEST_READY",
            message="交付清单已通过独立审查，请确认发布。",
            template_id="WAIT_STAGE",
            params={"stage_label": "准备交付"},
        )
        return

    requirement = await _requirement_payload(ctx, job)
    approval_evidence = {
        "approval_id": image_approval.id,
        "artifact_digest": image_approval.artifact_digest,
        "requirement_snapshot_digest": image_approval.requirement_snapshot_digest,
        "rules_version": image_approval.rules_version,
    }
    target_asset = {
        "asset_id": asset.id,
        "mime_type": asset.mime_type,
        "width": info.width,
        "height": info.height,
        "byte_size": asset.byte_size,
        "sha256": asset.sha256,
    }
    if ctx.consolidated:
        model_data = DeliveryData(
            asset_id=asset.id, requirement_revision=ctx.run.requirement_revision,
            filename_hint=_delivery_filename(asset, ctx.run.requirement_revision),
            caption="图片已完成本地技术校验，请核对商品细节后确认发布。",
        )
        delivery = RoleOutput(role="delivery_manager", status="ready", data=model_data.model_dump(mode="json"))
    else:
        delivery = await run_role(
            ctx,
            "delivery_manager",
            STAGE,
            {
                "target_asset": target_asset,
                "requirement": requirement,
                "image_approval": approval_evidence,
                "delivery_scope": {"count": 1, "size": ctx.project.image_size},
            },
            capability="TEXT",
            attempt=1,
        )
    try:
        model = DeliveryData.model_validate(delivery.require_data())
    except ApiError:
        raise
    except Exception:
        raise _manifest_invalid() from None
    if model.asset_id != asset.id or model.requirement_revision != ctx.run.requirement_revision:
        raise _requirement_changed()
    if not _safe_filename_hint(model.filename_hint):
        raise _manifest_invalid()
    if not _safe_caption(model.caption):
        raise _manifest_invalid()

    artifact = await start_artifact(ctx, "delivery_manifest", model.model_dump(mode="json"))
    target_id = f"delivery_manifest:{artifact.id}"
    if gate_mode_for_target("delivery_manifest") == "off":
        await _accept_manifest_without_content_review(
            ctx,
            artifact=artifact,
            asset=asset,
            job=job,
            target_id=target_id,
        )
        return
    allowed_evidence = (asset.id, image_approval.id, artifact.id)
    try:
        review = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": "delivery_manager",
                "manifest": model.model_dump(mode="json"),
                "target_asset": target_asset,
                "requirement": requirement,
                "image_approval": approval_evidence,
                "ruleset_version": RULESET_VERSION,
                "rules": _MANIFEST_RULESET.describe(),
            },
            review_targets=[
                {
                    "target_id": target_id,
                    "target_kind": "delivery_manifest",
                    "asset_id": asset.id,
                    "requirement_revision": ctx.run.requirement_revision,
                }
            ],
            images=(OrganizeImage(data=data, mime_type=asset.mime_type),),
            required_rule_ids=_MANIFEST_RULES,
            allowed_evidence_ids=allowed_evidence,
            attempt=1,
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
        adjudication = _validate_review(review, target_id, set(allowed_evidence))
    except ApiError as exc:
        await set_stage(
            ctx,
            STAGE,
            "blocked" if exc.status_code == 409 else "failed",
            issue_code=exc.code,
            template_id=exc.code,
        )
        raise

    await _accept_manifest_review(
        ctx,
        artifact=artifact,
        asset=asset,
        job=job,
        target_id=target_id,
        review=review,
        adjudication=adjudication,
    )


def _delivery_filename(asset: Asset, requirement_revision: int) -> str:
    return f"delivery-{asset.id[:8]}-r{requirement_revision}.{_file_extension(asset.mime_type)}"


async def _approve(ctx: WorkflowContext) -> None:
    job, asset = await _load_job_and_asset(ctx)
    artifact, _approval, payload = await _require_manifest_evidence(ctx, asset, job)
    async with ctx.session_factory() as session:
        existing = await session.scalar(
            select(Delivery)
            .where(Delivery.generation_job_id == job.id)
            .order_by(Delivery.created_at.desc())
            .limit(1)
        )
    if existing is not None:
        if existing.status == "revoked":
            # 交付记录没有第二个槽位；已撤销后必须重新生成，不能让旧记录复用新字节。
            raise _asset_unavailable()
        if (
            existing.manifest_artifact_id != artifact.id
            or existing.asset_id != asset.id
            or existing.asset_digest != asset.sha256
        ):
            raise _digest_mismatch()
        if existing.status == "published":
            await set_stage(
                ctx,
                STAGE,
                "completed",
                issue_code="DELIVERY_PUBLISHED",
                message="交付已发布。",
                template_id="WAIT_STAGE",
                params={"stage_label": "准备交付"},
            )
            return
        if existing.status == "approved":
            await set_stage(
                ctx,
                STAGE,
                "needs_input",
                issue_code="DELIVERY_APPROVED",
                message="交付已批准，请发布。",
                template_id="WAIT_STAGE",
                params={"stage_label": "准备交付"},
            )
            return
    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        stored_job = await session.get(GenerationJob, job.id)
        if stored_job is None or stored_job.status not in {"approved", "published"}:
            raise _image_not_approved()
        delivery = Delivery(
            id=str(uuid4()),
            project_id=ctx.project_id,
            generation_job_id=job.id,
            asset_id=asset.id,
            manifest_artifact_id=artifact.id,
            status="approved",
            filename=_delivery_filename(asset, ctx.run.requirement_revision),
            caption=str(payload.get("caption") or ""),
            asset_digest=asset.sha256 or "",
            approved_at=now,
            created_at=now,
        )
        session.add(delivery)
        await session.flush()
    await set_stage(
        ctx,
        STAGE,
        "needs_input",
        issue_code="DELIVERY_APPROVED",
        message="交付已批准，请发布。",
        template_id="WAIT_STAGE",
        params={"stage_label": "准备交付"},
    )


async def _latest_active_approval(
    session, project_id: str, approval_type: str
) -> Approval | None:
    return await session.scalar(
        select(Approval)
        .where(
            Approval.project_id == project_id,
            Approval.approval_type == approval_type,
            Approval.revoked_at.is_(None),
        )
        .order_by(Approval.created_at.desc(), Approval.id.desc())
        .limit(1)
    )


async def _revalidate_publication_in_session(
    ctx: WorkflowContext,
    session,
    *,
    delivery: Delivery,
    asset: Asset,
    job: GenerationJob,
    artifact: Artifact,
    data: bytes,
) -> Delivery:
    """Recheck every publication fact inside the one final write transaction.

    The preflight reads are only a fast rejection path.  A concurrent request,
    account lifecycle change, approval revocation, asset replacement or file
    rewrite must still make this transaction fail before anything is published.
    """
    await ensure_job_active_in_session(session, job.id)
    stored_job = await session.get(GenerationJob, job.id)
    stored_asset = await session.get(Asset, asset.id)
    stored_delivery = await session.get(Delivery, delivery.id)
    stored_artifact = await session.get(Artifact, artifact.id)
    if (
        stored_job is None
        or stored_asset is None
        or stored_delivery is None
        or stored_artifact is None
        or stored_job.status != "approved"
        or stored_job.result_asset_id != stored_asset.id
        or stored_asset.project_id != ctx.project_id
        or stored_asset.kind != "generated"
        or stored_asset.review_status != "approved"
        or not stored_asset.sha256
        or stored_delivery.status != "approved"
        or stored_delivery.project_id != ctx.project_id
        or stored_delivery.generation_job_id != stored_job.id
        or stored_delivery.asset_id != stored_asset.id
        or stored_delivery.manifest_artifact_id != stored_artifact.id
        or stored_delivery.asset_digest != stored_asset.sha256
        or stored_artifact.status != "approved"
    ):
        raise _asset_unavailable()

    image_approval = await _latest_active_approval(session, ctx.project_id, "image")
    if (
        image_approval is None
        or image_approval.approved_by != "output_reviewer"
        or image_approval.rules_version != "2.0.0"
        or image_approval.prompt_version != "2.0.0"
        or image_approval.requirement_snapshot_digest
        != stored_job.confirmed_snapshot_digest
        or not await asset_approval_matches(session, stored_asset, image_approval)
    ):
        raise _image_not_approved()

    manifest_approval = await _latest_active_approval(
        session, ctx.project_id, "delivery_manifest"
    )
    if (
        manifest_approval is None
        or manifest_approval.approved_by != "output_reviewer"
        or manifest_approval.artifact_id != stored_artifact.id
        or not manifest_approval.artifact_digest
        or manifest_approval.artifact_digest != stored_artifact.digest
        or manifest_approval.requirement_snapshot_digest
        != stored_job.confirmed_snapshot_digest
        or manifest_approval.rules_version != "2.0.0"
        or manifest_approval.prompt_version != "2.0.0"
    ):
        raise _asset_unavailable()

    try:
        payload = DeliveryData.model_validate_json(stored_artifact.payload_json)
    except Exception:
        raise _manifest_invalid() from None
    if digest_payload(payload.model_dump(mode="json")) != stored_artifact.digest:
        raise _digest_mismatch()
    run = await session.get(WorkflowRun, stored_job.run_id)
    if (
        run is None
        or payload.asset_id != stored_asset.id
        or payload.requirement_revision != run.requirement_revision
    ):
        raise _requirement_changed()
    requirement = await session.scalar(
        select(Artifact.id)
        .where(
            Artifact.run_id == stored_job.run_id,
            Artifact.kind.in_(("final_brief", "brief_draft")),
            Artifact.status == "approved",
            Artifact.digest == stored_job.confirmed_snapshot_digest,
        )
        .limit(1)
    )
    if requirement is None:
        raise _requirement_changed()

    source_path = resolve_asset_file(ctx.settings, stored_asset)
    if source_path is None:
        raise _asset_unavailable()
    try:
        actual = await asyncio.to_thread(source_path.read_bytes)
    except OSError:
        raise _asset_unavailable() from None
    if (
        hashlib.sha256(actual).hexdigest() != stored_asset.sha256
        or hashlib.sha256(data).hexdigest() != stored_asset.sha256
    ):
        raise _digest_mismatch()
    return stored_delivery


async def _publish(ctx: WorkflowContext) -> None:
    job, asset = await _load_job_and_asset(ctx)
    try:
        await ensure_job_active(ctx.session_factory, job.id)
    except GenerationGuardError as exc:
        raise ApiError(409, exc.code, exc.message) from exc
    from app.final_confirmation import execution_request

    # Publishing and execution both require the one durable final
    # authorization; legacy rows without it are never published.
    await execution_request(ctx.app, job.generation_id)
    artifact, _approval, payload = await _require_manifest_evidence(ctx, asset, job)
    data, _info = await _verify_image_file(ctx, asset)
    async with ctx.session_factory() as session:
        delivery = await session.scalar(
            select(Delivery)
            .where(Delivery.generation_job_id == job.id)
            .order_by(Delivery.created_at.desc())
            .limit(1)
        )
    if delivery is None:
        raise ApiError(409, "DELIVERY_NOT_APPROVED", "交付尚未批准，不能发布")
    if delivery.status == "published":
        await set_stage(
            ctx,
            STAGE,
            "completed",
            issue_code="DELIVERY_PUBLISHED",
            message="交付已发布。",
            template_id="WAIT_STAGE",
            params={"stage_label": "准备交付"},
        )
        return
    if delivery.status != "approved":
        raise ApiError(409, "DELIVERY_NOT_APPROVED", "交付尚未批准，不能发布")
    if (
        delivery.manifest_artifact_id != artifact.id
        or delivery.asset_id != asset.id
        or not delivery.asset_digest
        or delivery.asset_digest != asset.sha256
    ):
        raise _digest_mismatch()
    # Each attempt owns its file. A losing concurrent publisher must never
    # overwrite or delete the winner's committed destination during rollback.
    destination = f"published/{delivery.id}-{uuid4()}.{_file_extension(asset.mime_type)}"
    try:
        await asyncio.to_thread(write_asset_file, ctx.settings, destination, data)
    except OSError:
        raise ApiError(409, "ASSET_UNAVAILABLE", templates.render("ASSET_UNAVAILABLE")) from None
    now = format_timestamp(utc_now())
    try:
        async with write_transaction(ctx.session_factory) as session:
            stored_delivery = await _revalidate_publication_in_session(
                ctx,
                session,
                delivery=delivery,
                asset=asset,
                job=job,
                artifact=artifact,
                data=data,
            )
            stored_asset = await session.get(Asset, asset.id)
            stored_job = await session.get(GenerationJob, job.id)
            write_audit(
                session,
                event_type="delivery.published",
                result="success",
                now=ctx.app.state.clock.now(),
                actor_kind="account" if ctx.principal is not None else "anonymous",
                actor_account_id=(
                    ctx.principal.account_id if ctx.principal is not None else None
                ),
                target_kind="delivery",
                target_id=stored_delivery.id,
                after={
                    "project_id": ctx.project_id,
                    "job_id": stored_job.id,
                    "generation_id": stored_job.generation_id,
                    "asset_id": stored_asset.id,
                    "asset_digest": stored_asset.sha256,
                },
            )
            stored_asset.relative_path = destination
            stored_delivery.status = "published"
            stored_delivery.published_at = now
            stored_job.status = "published"
            stored_job.updated_at = now
            if stored_job.intent_id:
                intent = await session.get(GenerationIntent, stored_job.intent_id)
                if intent is not None:
                    intent.status = "delivered"
                    intent.updated_at = now
                    await release_slots_in_session(
                        session, intent_id=intent.id, reason="delivery_published"
                    )
            await session.flush()
    except BaseException as exc:
        await asyncio.to_thread(delete_asset_file, ctx.settings, destination)
        if isinstance(exc, GenerationGuardError):
            raise ApiError(409, exc.code, exc.message) from exc
        if isinstance(exc, ApiError):
            raise
        # 数据库失败也固定返回不可用，不能把底层异常或半发布状态暴露给调用方。
        raise ApiError(409, "ASSET_UNAVAILABLE", templates.render("ASSET_UNAVAILABLE")) from None
    await set_stage(
        ctx,
        STAGE,
        "completed",
        issue_code="DELIVERY_PUBLISHED",
        message="交付已发布。",
        template_id="WAIT_STAGE",
        params={"stage_label": "准备交付"},
    )




def _feedback_text(payload: dict[str, Any]) -> str:
    if set(payload) - {"text"}:
        raise validation_error("请求参数不合法")
    value = payload.get("text")
    if not isinstance(value, str) or not value.strip():
        raise validation_error("请说明本次修改要求")
    if len(value) > 20_000:
        raise validation_error("修改说明过长")
    return value


def _run_of(ctx: WorkflowContext) -> WorkflowRun:
    if ctx.run is None:
        raise _action_unavailable()
    return ctx.run


async def _current_delivery(
    ctx: WorkflowContext,
) -> tuple[GenerationJob, Asset, Delivery]:
    async with read_session(ctx.session_factory) as session:
        job = await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.run_id == ctx.run_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        if job is None or not job.result_asset_id:
            raise _image_not_approved()
        asset = await session.get(Asset, job.result_asset_id)
        delivery = await session.scalar(
            select(Delivery)
            .where(
                Delivery.project_id == ctx.project_id,
                Delivery.generation_job_id == job.id,
            )
            .order_by(Delivery.created_at.desc())
            .limit(1)
        )
    if (
        asset is None
        or asset.project_id != ctx.project_id
        or asset.kind != "generated"
        or asset.review_status != "approved"
        or not asset.sha256
        or job.status not in {"approved", "published"}
        or delivery is None
        or delivery.status not in {"approved", "published"}
    ):
        raise _image_not_approved()
    if not delivery.asset_digest or delivery.asset_digest != asset.sha256:
        raise _image_not_approved()
    return job, asset, delivery


def _payload(artifact: Artifact) -> dict[str, Any]:
    try:
        value = json.loads(artifact.payload_json)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _report_matches_asset(report: Artifact, asset_id: str | None) -> bool:
    if not asset_id:
        return False
    payload = _payload(report)
    if payload.get("reference_usage") not in {"product", "style", "composition"}:
        return False
    expected = "reference:" + asset_id
    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        return False
    for item in observations:
        if not isinstance(item, dict):
            return False
        ids = item.get("evidence_ids")
        if not isinstance(ids, list) or not ids or any(value != expected for value in ids):
            return False
    texts = payload.get("texts")
    if not isinstance(texts, list):
        return False
    for item in texts:
        if not isinstance(item, dict):
            return False
        ids = item.get("evidence_ids")
        if not isinstance(ids, list) or not ids or any(value != expected for value in ids):
            return False
    return True


async def _copy_reference_report(ctx: WorkflowContext, old_run_id: str) -> bool:
    asset_id = ctx.project.reference_asset_id
    if not asset_id:
        return False
    async with read_session(ctx.session_factory) as session:
        reports = (
            await session.scalars(
                select(Artifact)
                .where(
                    Artifact.project_id == ctx.project_id,
                    Artifact.kind == "reference_report",
                    Artifact.status == "approved",
                )
                .order_by(Artifact.created_at.desc())
            )
        ).all()
    source = next(
        (
            report
            for report in reports
            if report.run_id != ctx.run_id and _report_matches_asset(report, asset_id)
        ),
        None,
    )
    if source is None:
        return False
    payload = _payload(source)
    copied = await start_artifact(ctx, "reference_report", payload)
    await add_review(
        ctx,
        copied,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={
            "carried_from_report": source.id,
            "carried_from_run": old_run_id,
            "checks": [],
        },
        raw={"target_kind": "reference_report", "carried_from_run": old_run_id},
    )
    return True


async def _request_new_version(ctx: WorkflowContext, payload: dict[str, Any], *, defer: bool = False) -> None:
    text = _feedback_text(payload)
    _job, _asset, delivery = await _current_delivery(ctx)
    old_run = _run_of(ctx)
    if delivery.status not in {"approved", "published"}:
        raise _image_not_approved()

    now = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        if project is None:
            raise conflict("PROJECT_NOT_FOUND", "项目不存在")
        project.revision += 1
        project.prompt_revision = None
        project.confirmed_revision = None
        project.brief_status = "draft"
        project.updated_at = now
        highest_message = await session.scalar(
            select(func.max(Message.sequence_no)).where(
                Message.project_id == ctx.project_id
            )
        )
        session.add(
            Message(
                id=str(uuid4()),
                project_id=ctx.project_id,
                sequence_no=(highest_message or 0) + 1,
                role="user",
                content=text,
                project_revision=project.revision,
                brief_snapshot_json=None,
                operation_id=None,
                turn_kind="workflow",
                created_at=now,
            )
        )
        highest_run = await session.scalar(
            select(func.max(WorkflowRun.sequence_no)).where(
                WorkflowRun.project_id == ctx.project_id
            )
        )
        new_run = WorkflowRun(
            id=str(uuid4()),
            project_id=ctx.project_id,
            sequence_no=(highest_run or 0) + 1,
            stage="S03",
            status="active",
            requirement_revision=project.revision,
            role_policy=ctx.settings.workflow_role_policy if ctx.direct else old_run.role_policy,
            created_at=now,
            updated_at=now,
        )
        session.add(new_run)
        await session.flush()
    ctx.project = project
    ctx.project.revision = project.revision
    ctx.run = new_run

    # The current feedback cycle's confirmation and execution authorization
    # cannot carry into a new version. A published run's prompt approval is a
    # historical fact required to read its immutable snapshot, so only an
    # unpublished cycle loses its prompt approval here.
    revocation_types = (
        ("requirements_card",)
        if delivery.status == "published"
        else ("requirements_card", "generation_prompt")
    )
    await revoke_active_approval_types(ctx, revocation_types, "new_version")

    if defer:
        return
    if ctx.direct:
        from app.workflow.state import set_intent

        await set_intent(ctx, "image_request", "new_request")
    has_reference = ctx.project.reference_asset_id is not None
    if has_reference:
        copied = await _copy_reference_report(ctx, old_run.id)
        if not copied:
            # A changed reference must be analysed again before a new
            # requirement version can use it. No text-only fallback exists.
            from app.workflow.stages import s02

            await s02.analyze(ctx)
            if ctx.direct:
                return

    if ctx.direct:
        from app.workflow.stages import s03

        await s03._compose(ctx, "compose_brief", None, record_user_text=False)


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    if ctx.run is None or ctx.run.stage != STAGE:
        return []
    if ctx.run.status == "completed":
        if ctx.run.issue_code == "DELIVERY_PUBLISHED":
            return ["request_new_version"]
        return []
    if ctx.run.issue_code == "DELIVERY_MANIFEST_READY":
        return ["approve_delivery"]
    if ctx.run.issue_code == "DELIVERY_APPROVED":
        return ["publish_delivery", "request_new_version"]
    if ctx.run.issue_code is None:
        return ["prepare_delivery"]
    return []


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action == "prepare_delivery":
        await _prepare(ctx)
        return
    if action == "approve_delivery":
        await _approve(ctx)
        return
    if action == "publish_delivery":
        await _publish(ctx)
        return
    if action == "request_new_version":
        await _request_new_version(ctx, payload)
        return
    raise _action_unavailable()


__all__ = ["STAGE", "ACTIONS", "available", "handle"]

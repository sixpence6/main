from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from app.db import read_session, write_transaction
from app.errors import ApiError, conflict, validation_error
from app.models import Project
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
from app.workflow.stages import s03
from app.workflow.state import (
    WorkflowContext,
    add_approval,
    invalidate_requirements,
    latest_approval,
    latest_artifact,
    record_diagnostic,
    referenced_material_digest,
    set_stage,
)
from app.workflow.tables import Artifact, ArtifactReview, Approval

STAGE = "S06"
ACTIONS = ["confirm_requirements", "revise_requirements"]

CONFIRMATION_KEY = "confirmation"
SCHEMA_VERSION = "2.0.0"
_APPROVAL_TYPE = "requirements_card"

_HARD_REQUIREMENT_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("must_include", "必须包含", "positive_prompt"),
    ("must_preserve", "必须保留", "positive_prompt"),
    ("forbidden", "禁止项", "negative_prompt"),
    ("texts", "图片文字", "positive_prompt"),
)


def available(ctx: WorkflowContext) -> list[str]:
    if ctx.run is None:
        return []
    if ctx.run.stage != STAGE or ctx.run.status != "active":
        return []
    return list(ACTIONS)


def _not_available() -> ApiError:
    return ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))


def _artifact_error() -> ApiError:
    return conflict(
        "ARTIFACT_NOT_APPROVED",
        "当前需求卡尚未通过独立审查，暂不能确认。请刷新后重试。",
    )


def _changed_error() -> ApiError:
    return conflict(
        "REQUIREMENTS_CHANGED",
        "需求卡内容、参考图或需求版本已变化，请刷新后重新确认当前版本。",
    )


def _digest_error() -> ApiError:
    return conflict(
        "CARD_DIGEST_MISMATCH",
        "你确认的需求卡版本已不是当前版本，请刷新后确认。",
    )


def _revision_error() -> ApiError:
    return conflict(
        "REQUIREMENT_REVISION_CONFLICT",
        "需求版本已更新，请刷新后重新确认。",
    )


def _unresolved_error() -> ApiError:
    return conflict(
        "REQUIREMENTS_UNRESOLVED",
        "需求卡仍有未解决的问题，请先补充或修改需求后再确认。",
    )


async def _latest_review(
    ctx: WorkflowContext, artifact_id: str
) -> ArtifactReview | None:
    async with read_session(ctx.session_factory) as session:
        return await session.scalar(
            select(ArtifactReview)
            .where(
                ArtifactReview.artifact_id == artifact_id,
                ArtifactReview.project_id == ctx.project_id,
                ArtifactReview.target_kind == "artifact",
            )
            .order_by(ArtifactReview.created_at.desc(), ArtifactReview.id.desc())
            .limit(1)
        )


def _payload(artifact: Artifact) -> dict[str, Any] | None:
    try:
        value = json.loads(artifact.payload_json)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def _require_approved_card(
    ctx: WorkflowContext,
) -> tuple[Artifact, dict[str, Any]]:
    """Return the latest final brief only when its latest review is a pass.

    Artifact.status alone is not enough: a later revise/reject review can
    leave an old approved status behind, so the latest independent review is
    checked explicitly. No model is called here; the review already exists.
    """
    artifact = await latest_artifact(ctx, "final_brief")
    if artifact is None or artifact.status != "approved":
        raise _artifact_error()
    review = await _latest_review(ctx, artifact.id)
    if review is None or review.decision != "pass":
        raise _artifact_error()
    data = _payload(artifact)
    if data is None:
        raise _artifact_error()
    unresolved = data.get("unresolved")
    if isinstance(unresolved, list) and unresolved:
        raise _unresolved_error()
    return artifact, data


def _client_binding(payload: dict[str, Any], artifact: Artifact, ctx: WorkflowContext) -> None:
    """Validate optional client binding without ever trusting client approval.

    The client may echo the card it rendered; these fields are assertions to be
    checked, never an authorization. Any self-reported confirmed or
    approved_by value is deliberately ignored.
    """
    digest = payload.get("artifact_digest")
    if digest is None:
        digest = payload.get("card_digest")
    if digest is not None and digest != artifact.digest:
        raise _digest_error()
    artifact_id = payload.get("artifact_id")
    if artifact_id is not None and artifact_id != artifact.id:
        raise _digest_error()
    requirement_revision = payload.get("requirement_revision")
    if requirement_revision is not None and requirement_revision != ctx.run.requirement_revision:
        raise _revision_error()


def _hard_requirements(artifact: Artifact, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Build deterministic, unique IDs for every hard requirement.

    applies_to reuses the generation contract's binding vocabulary so a later
    prompt compiler can apply each requirement without guessing.
    """
    prefix = artifact.digest[:16]
    items: list[dict[str, Any]] = []
    for field, label, binding in _HARD_REQUIREMENT_FIELDS:
        values = data.get(field)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            if not isinstance(value, str) or not value:
                continue
            items.append(
                {
                    "id": f"hr_{prefix}_{field}_{index + 1}",
                    "category": field,
                    "label": label,
                    "value": value,
                    "applies_to": binding,
                    "applicability": "required",
                    "source": "final_brief",
                }
            )
    size = data.get("size")
    if isinstance(size, str) and size:
        items.append(
            {
                "id": f"hr_{prefix}_size_1",
                "category": "size",
                "label": "输出尺寸",
                "value": size,
                "applies_to": "size",
                "applicability": "required",
                "source": "final_brief",
            }
        )
    reference_usage = data.get("reference_usage")
    if isinstance(reference_usage, str) and reference_usage != "none":
        items.append(
            {
                "id": f"hr_{prefix}_reference_usage_1",
                "category": "reference_usage",
                "label": "参考图用途",
                "value": reference_usage,
                "applies_to": "reference_asset_id",
                "applicability": "required",
                "source": "final_brief",
            }
        )
    reference_asset_id = data.get("reference_asset_id")
    if isinstance(reference_asset_id, str) and reference_asset_id:
        items.append(
            {
                "id": f"hr_{prefix}_reference_asset_id_1",
                "category": "reference_asset_id",
                "label": "参考图",
                "value": reference_asset_id,
                "applies_to": "reference_asset_id",
                "applicability": "required" if reference_usage != "none" else "optional",
                "source": "final_brief",
            }
        )
    return items


def _confirmation(approval: Approval) -> dict[str, Any] | None:
    try:
        checks = json.loads(approval.checks_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(checks, dict):
        return None
    value = checks.get(CONFIRMATION_KEY)
    return value if isinstance(value, dict) else None


async def _matches_existing(
    ctx: WorkflowContext,
    approval: Approval,
    artifact: Artifact,
    data: dict[str, Any],
) -> bool:
    snapshot = _confirmation(approval)
    if snapshot is None:
        return False
    if approval.artifact_digest != artifact.digest:
        return False
    if snapshot.get("artifact_id") != artifact.id:
        return False
    if snapshot.get("artifact_digest") != artifact.digest:
        return False
    if snapshot.get("artifact_revision_no") != artifact.revision_no:
        return False
    if snapshot.get("project_revision") != ctx.project.revision:
        return False
    if snapshot.get("requirement_revision") != ctx.run.requirement_revision:
        return False
    if snapshot.get("reference_asset_id") != data.get("reference_asset_id"):
        return False
    if snapshot.get("reference_usage") != data.get("reference_usage"):
        return False
    material = await referenced_material_digest(ctx)
    if snapshot.get("referenced_material_digest") != material:
        return False
    if snapshot.get("hard_requirements") != _hard_requirements(artifact, data):
        return False
    return True


async def _reject_stale_existing(
    ctx: WorkflowContext, approval: Approval, artifact: Artifact | None, data: dict[str, Any] | None
) -> bool:
    """Revoke a confirmation that no longer matches the current card.

    Returns True when a stale confirmation existed and was invalidated. The
    caller then refuses the request; a late click can never restore it.
    """
    if artifact is None or data is None or not await _matches_existing(ctx, approval, artifact, data):
        await invalidate_requirements(ctx, "requirements_changed")
        await record_diagnostic(
            ctx,
            "requirements_confirmation_invalidated",
            {"approval_id": approval.id, "reason": "current_version_changed"},
        )
        # The old confirmation is gone, so the user must be able to see the
        # confirmation action again after a changed requirement version.
        if ctx.run is not None and ctx.run.stage in ("S06", "S07"):
            await set_stage(ctx, STAGE, "active")
        return True
    return False


async def _revise_requirements(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    """Start a fresh S03 requirements version from an explicit user edit.

    The current approved card is read from the database, never from the client.
    A confirmed card may be revised in S06 or directly after confirmation in
    S07. Any other stage must use the normal stage actions instead.
    """
    if ctx.run is None or ctx.run.stage not in {"S06", "S07"}:
        raise _not_available()
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise validation_error("消息内容不能为空")
    if len(text) > 20_000:
        raise validation_error("消息内容过长")

    artifact, data = await _require_approved_card(ctx)
    confirmation = await latest_approval(ctx, _APPROVAL_TYPE)
    if ctx.run.stage == "S07":
        if confirmation is None:
            raise _changed_error()
        if not await _matches_existing(ctx, confirmation, artifact, data):
            raise _changed_error()
    elif confirmation is not None and not await _matches_existing(
        ctx, confirmation, artifact, data
    ):
        await _reject_stale_existing(ctx, confirmation, artifact, data)
        raise _changed_error()

    previous_revision = ctx.project.revision
    current_requirement = {
        "artifact_id": artifact.id,
        "artifact_digest": artifact.digest,
        "artifact_revision_no": artifact.revision_no,
        "payload": data,
    }
    await record_diagnostic(
        ctx,
        "requirements_revision_requested",
        {
            "previous_project_revision": previous_revision,
            "artifact_id": artifact.id,
            "artifact_digest": artifact.digest,
            "confirmation_id": confirmation.id if confirmation is not None else None,
        },
    )
    await s03.start_requirements_revision(
        ctx,
        text,
        current_requirement=current_requirement,
    )


async def _confirm(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise _changed_error()

    artifact = await latest_artifact(ctx, "final_brief")
    data = _payload(artifact) if artifact is not None else None
    existing = await latest_approval(ctx, _APPROVAL_TYPE)
    if existing is not None:
        if artifact is not None and data is not None and await _matches_existing(ctx, existing, artifact, data):
            # Idempotent replay: return the original snapshot untouched.
            return
        await _reject_stale_existing(ctx, existing, artifact, data)
        raise _changed_error()

    artifact, data = await _require_approved_card(ctx)

    # A previous confirmation of this exact card may already be revoked
    # because the requirement version changed. Never re-confirm an old card
    # under the new project revision: only a newly reviewed card can be
    # confirmed again.
    historical = await latest_approval(ctx, _APPROVAL_TYPE, active_only=False)
    if historical is not None:
        snapshot = _confirmation(historical)
        if (
            snapshot is not None
            and snapshot.get("artifact_digest") == artifact.digest
            and (
                snapshot.get("project_revision") != ctx.project.revision
                or snapshot.get("requirement_revision") != ctx.run.requirement_revision
            )
        ):
            raise _changed_error()

    _client_binding(payload, artifact, ctx)

    if data.get("reference_asset_id") != ctx.project.reference_asset_id:
        raise _changed_error()

    material = await referenced_material_digest(ctx)
    now = format_timestamp(utc_now())
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact.id,
        "artifact_digest": artifact.digest,
        "artifact_revision_no": artifact.revision_no,
        "project_revision": ctx.project.revision,
        "requirement_revision": ctx.run.requirement_revision,
        "reference_asset_id": data.get("reference_asset_id"),
        "reference_usage": data.get("reference_usage"),
        "referenced_material_digest": material,
        "hard_requirements": _hard_requirements(artifact, data),
        "confirmed_by": "user",
        "confirmed_at": now,
    }
    approval = await add_approval(
        ctx,
        approval_type=_APPROVAL_TYPE,
        artifact=artifact,
        approved_by="user",
        checks={CONFIRMATION_KEY: snapshot},
        requirement_digest=artifact.digest,
        material_digest=material,
        model_fingerprint="not_applicable",
    )
    # The immutable approval is the authoritative record; these project
    # fields are the current-state projection used by S07/S08 gate checks.
    now_state = format_timestamp(utc_now())
    async with write_transaction(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        if project is None:
            raise _changed_error()
        project.brief_status = "confirmed"
        project.confirmed_revision = project.revision
        project.updated_at = now_state
        await session.flush()
    ctx.project.brief_status = "confirmed"
    ctx.project.confirmed_revision = ctx.project.revision
    ctx.project.updated_at = now_state

    await record_diagnostic(
        ctx,
        "requirements_confirmed",
        {
            "approval_id": approval.id,
            "artifact_id": artifact.id,
            "artifact_digest": artifact.digest,
            "project_revision": ctx.project.revision,
            "requirement_revision": ctx.run.requirement_revision,
        },
    )
    # Confirmation only moves to the prompt-compilation stage. It never
    # compiles a prompt, creates a GenerationJob, or calls the image provider.
    await set_stage(ctx, "S07", "active")


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise _not_available()
    if action == "revise_requirements":
        await _revise_requirements(ctx, payload)
        return
    await _confirm(ctx, payload)


__all__ = ["STAGE", "ACTIONS", "available", "handle"]

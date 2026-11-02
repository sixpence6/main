from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from app.acceptance import (
    GenerationIntent,
    accept_in_session,
    intent_for_request,
    intent_request_digest,
    normalize_request_id,
)
from app.db import read_session, write_transaction
from app.errors import ApiError, conflict, validation_error
from app.models import Asset, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
from app.workflow.snapshot import build_execution_snapshot, normalize_user_locks
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
from app.workflow.tables import Approval, Artifact, ArtifactReview, WorkflowRun

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




def _execution_snapshot(
    artifact: Artifact,
    data: dict[str, Any],
    ctx: WorkflowContext,
    material: str,
    reference_usage: str | None,
    user_locks: dict[str, str] | None = None,
) -> dict[str, Any]:
    return build_execution_snapshot(
        artifact_id=artifact.id,
        artifact_digest=artifact.digest,
        artifact_revision_no=artifact.revision_no,
        project_revision=ctx.project.revision,
        requirement_revision=ctx.run.requirement_revision,
        reference_asset_id=ctx.project.reference_asset_id,
        reference_usage=reference_usage or "none",
        referenced_material_digest=material,
        brief=data,
        hard_requirements=_hard_requirements(artifact, data),
        user_locks=user_locks,
    )

def _requested_user_locks(payload: dict[str, Any], data: dict[str, Any]) -> dict[str, str]:
    raw = payload.get("user_locks")
    if raw is None:
        raw = data.get("user_locks")
    try:
        return normalize_user_locks(raw)
    except ValueError:
        raise validation_error("用户锁定信息无效") from None


def _client_binding(
    payload: dict[str, Any], artifact: Artifact, data: dict[str, Any], ctx: WorkflowContext
) -> None:
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
    size = payload.get("size")
    if size is not None:
        expected_size = str(
            data.get("size") or ctx.project.image_size or ctx.settings.visual().image_size or ""
        )
        if str(size) != expected_size:
            raise _changed_error()
    reference_usage = payload.get("reference_usage")
    if reference_usage is not None:
        expected_usage = data.get("reference_usage") or "none"
        if reference_usage != expected_usage:
            raise _changed_error()
    reference_asset_id = payload.get("reference_asset_id")
    if reference_asset_id is not None and reference_asset_id != data.get("reference_asset_id"):
        raise _changed_error()
    if "user_locks" in payload:
        _requested_user_locks(payload, data)


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
    user_locks: dict[str, str] | None = None,
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
    try:
        stored_locks = normalize_user_locks(snapshot.get("user_locks"))
        requested_locks = normalize_user_locks(
            user_locks if user_locks is not None else data.get("user_locks")
        )
    except ValueError:
        return False
    if stored_locks != requested_locks:
        return False
    return snapshot.get("hard_requirements") == _hard_requirements(artifact, data)


async def _reject_stale_existing(
    ctx: WorkflowContext,
    approval: Approval,
    artifact: Artifact | None,
    data: dict[str, Any] | None,
    user_locks: dict[str, str] | None = None,
) -> bool:
    """Revoke a confirmation that no longer matches the current card.

    Returns True when a stale confirmation existed and was invalidated. The
    caller then refuses the request; a late click can never restore it.
    """
    if (
        artifact is None
        or data is None
        or not await _matches_existing(ctx, approval, artifact, data, user_locks)
    ):
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


def _request_id(payload: dict[str, Any]) -> str | None:
    return normalize_request_id(payload.get("request_id"))


def _snapshot_matches_current(
    approval: Approval, project: Project, run: WorkflowRun, artifact: Artifact
) -> bool:
    snapshot = _confirmation(approval)
    return bool(
        snapshot
        and snapshot.get("artifact_digest") == artifact.digest
        and snapshot.get("project_revision") == project.revision
        and snapshot.get("requirement_revision") == run.requirement_revision
        and snapshot.get("reference_asset_id") == project.reference_asset_id
        and snapshot.get("reference_usage")
        == (project.reference_asset_id and snapshot.get("reference_usage") or "none")
    )


def _reference_usage(ctx: WorkflowContext, data: dict[str, Any]) -> str:
    raw = data.get("reference_usage")
    usage = raw if isinstance(raw, str) and raw else "none"
    if ctx.project.reference_asset_id is None:
        if usage != "none":
            raise _changed_error()
    elif usage not in {"product", "style", "composition"}:
        raise _changed_error()
    return usage


async def _confirm_atomic(
    ctx: WorkflowContext,
    artifact: Artifact,
    data: dict[str, Any],
    material: str,
    request_id: str,
    user_locks: dict[str, str] | None = None,
) -> GenerationIntent:
    """Confirm and accept in one BEGIN IMMEDIATE transaction.

    The approval row, immutable intent, 24h usage ledger, all three live slots,
    project confirmation projection and stage transition either all commit or
    all roll back. No model call happens inside this transaction.
    """

    image_size = str(
        data.get("size") or ctx.project.image_size or ctx.settings.visual().image_size or ""
    )
    if not image_size:
        raise _changed_error()
    configured = ctx.project.image_size or ctx.settings.visual().image_size
    if configured and image_size != configured:
        raise _changed_error()
    reference_usage = _reference_usage(ctx, data)

    now = format_timestamp(utc_now())
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact.id,
        "artifact_digest": artifact.digest,
        "artifact_revision_no": artifact.revision_no,
        "project_revision": ctx.project.revision,
        "requirement_revision": ctx.run.requirement_revision,
        "reference_asset_id": ctx.project.reference_asset_id,
        "reference_usage": reference_usage,
        "referenced_material_digest": material,
        "hard_requirements": _hard_requirements(artifact, data),
        "user_locks": dict(user_locks or {}),
        "confirmed_by": "user",
        "confirmed_at": now,
    }
    snapshot.update(
        _execution_snapshot(
            artifact, data, ctx, material, reference_usage, user_locks=user_locks
        )
    )
    async with write_transaction(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        run = await session.get(WorkflowRun, ctx.run_id)
        stored_artifact = await session.get(Artifact, artifact.id)
        if project is None or run is None or stored_artifact is None:
            raise _changed_error()
        if project.revision != snapshot["project_revision"]:
            raise _changed_error()
        if (
            run.project_id != project.id
            or run.requirement_revision != snapshot["requirement_revision"]
        ):
            raise _changed_error()
        if stored_artifact.project_id != project.id or stored_artifact.run_id != run.id:
            raise _changed_error()
        if stored_artifact.digest != artifact.digest or stored_artifact.status != "approved":
            raise _changed_error()
        if project.owner_account_id is None or ctx.principal is None:
            raise _changed_error()
        if project.owner_account_id != ctx.principal.account_id:
            raise _changed_error()
        if project.reference_asset_id is not None:
            reference = await session.get(Asset, project.reference_asset_id)
            if (
                reference is None
                or reference.project_id != project.id
                or reference.kind != "reference"
            ):
                raise _changed_error()

        active = await session.scalar(
            select(Approval)
            .where(
                Approval.project_id == project.id,
                Approval.approval_type == _APPROVAL_TYPE,
                Approval.revoked_at.is_(None),
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        if active is not None:
            if not _snapshot_matches_current(active, project, run, stored_artifact):
                raise _changed_error()
            approval = active
        else:
            approval = await add_approval(
                ctx,
                approval_type=_APPROVAL_TYPE,
                artifact=stored_artifact,
                approved_by="user",
                checks={CONFIRMATION_KEY: snapshot},
                requirement_digest=stored_artifact.digest,
                material_digest=material,
                model_fingerprint="not_applicable",
                session=session,
            )

        expected_digest = intent_request_digest(
            project_id=project.id,
            run_id=run.id,
            owner_account_id=project.owner_account_id,
            operator_account_id=ctx.principal.account_id,
            request_id=request_id,
            requirement_revision=run.requirement_revision,
            artifact_id=stored_artifact.id,
            artifact_digest=stored_artifact.digest,
            reference_asset_id=project.reference_asset_id,
            reference_usage=reference_usage,
            image_size=image_size,
        )
        existing_intent = await intent_for_request(
            session, project_id=project.id, request_id=request_id
        )
        if existing_intent is not None:
            if existing_intent.request_digest != expected_digest:
                raise conflict(
                    "INTENT_REPLAY_CONFLICT",
                    "同一请求标识已绑定不同的生成参数，请刷新后重新确认。",
                )
            if existing_intent.status not in {"accepted", "executing", "delivered"}:
                raise conflict(
                    "INTENT_STATE_INVALID",
                    "上次生成授权已结束，请重新确认当前需求后再生成。",
                )
            intent = existing_intent
        else:
            result = await accept_in_session(
                session,
                settings=ctx.settings,
                project=project,
                run=run,
                operator_account_id=ctx.principal.account_id,
                artifact=stored_artifact,
                requirement_revision=run.requirement_revision,
                reference_usage=reference_usage,
                image_size=image_size,
                request_id=request_id,
                approval_id=approval.id,
            )
            intent = result.intent

        project.brief_status = "confirmed"
        project.confirmed_revision = project.revision
        project.updated_at = now
        run.stage = "S07"
        run.status = "active"
        run.issue_code = None
        run.issue_message = None
        run.template_id = None
        run.template_params_json = None
        run.updated_at = now
        await session.flush()
    ctx.project.brief_status = "confirmed"
    ctx.project.confirmed_revision = ctx.project.revision
    ctx.project.updated_at = now
    ctx.run.stage = "S07"
    ctx.run.status = "active"
    ctx.run.updated_at = now
    return intent


async def _after_acceptance(ctx: WorkflowContext, intent: GenerationIntent) -> None:
    await record_diagnostic(
        ctx,
        "requirements_confirmed",
        {
            "approval_id": intent.approval_id,
            "artifact_id": intent.artifact_id,
            "artifact_digest": intent.artifact_digest,
            "intent_id": intent.id,
            "request_id": intent.request_id,
            "project_revision": ctx.project.revision,
            "requirement_revision": ctx.run.requirement_revision,
        },
    )
    # The durable acceptance is already committed. The background driver only
    # compiles/reviews the prompt and submits the image after this transaction.
    from app.generation_cycle import schedule_confirmed_cycle

    schedule_confirmed_cycle(ctx.app, ctx.project_id, ctx.run_id, intent.id)


async def _confirm(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise _changed_error()

    request_id = _request_id(payload)
    artifact = await latest_artifact(ctx, "final_brief")
    data = _payload(artifact) if artifact is not None else None
    if artifact is not None and data is not None:
        _client_binding(payload, artifact, data, ctx)
    user_locks = (
        _requested_user_locks(payload, data)
        if artifact is not None and data is not None
        else {}
    )
    existing = await latest_approval(ctx, _APPROVAL_TYPE)
    if existing is not None:
        if (
            artifact is not None
            and data is not None
            and await _matches_existing(ctx, existing, artifact, data, user_locks)
        ):
            # Idempotent replay: return the original snapshot untouched. A
            # v3 client that supplies a request id also backfills the durable
            # intent/usage/slot fact if an older confirmation lacks one.
            if request_id is not None and ctx.project.owner_account_id is not None:
                material = await referenced_material_digest(ctx)
                intent = await _confirm_atomic(
                    ctx, artifact, data, material, request_id, user_locks=user_locks
                )
                await _after_acceptance(ctx, intent)
            return
        await _reject_stale_existing(ctx, existing, artifact, data, user_locks)
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

    _client_binding(payload, artifact, data, ctx)

    if data.get("reference_asset_id") != ctx.project.reference_asset_id:
        raise _changed_error()

    material = await referenced_material_digest(ctx)
    user_locks = _requested_user_locks(payload, data)
    if request_id is not None and ctx.project.owner_account_id is not None:
        intent = await _confirm_atomic(
            ctx, artifact, data, material, request_id, user_locks=user_locks
        )
        await _after_acceptance(ctx, intent)
        return

    now = format_timestamp(utc_now())
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact.id,
        "artifact_digest": artifact.digest,
        "artifact_revision_no": artifact.revision_no,
        "project_revision": ctx.project.revision,
        "requirement_revision": ctx.run.requirement_revision,
        "reference_asset_id": ctx.project.reference_asset_id,
        "reference_usage": data.get("reference_usage"),
        "referenced_material_digest": material,
        "hard_requirements": _hard_requirements(artifact, data),
        "user_locks": dict(user_locks),
        "confirmed_by": "user",
        "confirmed_at": now,
    }
    snapshot.update(
        _execution_snapshot(
            artifact,
            data,
            ctx,
            material,
            data.get("reference_usage"),
            user_locks=user_locks,
        )
    )
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

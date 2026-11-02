from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.acceptance import (
    GenerationIntent,
    accept_in_session,
    close_intent_tracking,
    intent_for_request,
    intent_request_digest,
    latest_intent,
    terminate_intent,
)
from app.auth.audit import write_audit
from app.auth.tokens import token_digest
from app.db import read_session, write_transaction
from app.errors import ApiError, conflict
from app.generation_guard import (
    GenerationGuardError,
    ensure_job_active,
    generation_authorization_ref,
    job_authorization_ref,
)
from app.generation_recovery import recover_remote, resume_interrupted
from app.models import Asset, Generation, Project
from app.presenters import brief_from_project, dumps_json, snapshot_json
from app.services.assets import resolve_asset_file
from app.services.config_status import build_config_status
from app.services.generations import (
    execute_generation,
    request_digest,
    service_fingerprint,
)
from app.timeutil import format_timestamp, utc_now
from app.workflow import state, templates
from app.workflow.contracts import ContractError, digest_payload, validate_data
from app.workflow.stages import s09, s10
from app.workflow.stages.s07 import (
    _candidate_errors,
    _hard_requirements,
    _require_confirmed,
)
from app.workflow.state import WorkflowContext
from app.workflow.tables import Artifact, DiagnosticEvent, GenerationJob, WorkflowRun

STAGE = "S08"
ACTIONS = [
    "submit_generation",
    "verify_generation",
    "retry_generation",
    "recover_generation",
    "close_generation_tracking",
    "start_new_generation",
]
logger = logging.getLogger(__name__)

_ACTIVE_JOB_STATUSES = (
    "created",
    "submit_pending",
    "submitted",
    "remote_pending",
    "result_fetched",
)
_UNKNOWN_ISSUE = "GENERATION_UNKNOWN"
_RECOVERABLE_BLOCK_CODES = {"PROCESS_INTERRUPTED", "GENERATION_UNKNOWN"}
_RECOVERY_BLOCK_CODES = _RECOVERABLE_BLOCK_CODES | {
    "RESULT_RETRIEVAL_PENDING",
    "GENERATION_FAILED",
}
_UNCERTAIN_BLOCK_CODES = {"GENERATION_UNKNOWN", "RESULT_RETRIEVAL_PENDING"}
_CLOSED_ISSUE = "LOCAL_TRACKING_CLOSED"


def _replayable(ctx: WorkflowContext) -> bool:
    if ctx.run is None or ctx.run.stage != STAGE:
        return False
    if ctx.run.status == "active":
        return True
    return (
        ctx.run.status == "blocked"
        and ctx.run.issue_code in _RECOVERABLE_BLOCK_CODES
    )


def available(ctx: WorkflowContext) -> list[str]:
    if ctx.run is None or ctx.run.stage != STAGE:
        return []
    if ctx.run.status == "active":
        return ["submit_generation"]
    if ctx.run.status != "blocked":
        return []
    issue = ctx.run.issue_code
    if issue in _UNCERTAIN_BLOCK_CODES:
        return ["verify_generation", "recover_generation", "close_generation_tracking"]
    if issue in _RECOVERABLE_BLOCK_CODES or issue == "RESULT_RETRIEVAL_PENDING":
        return ["verify_generation", "recover_generation"]
    if issue == "GENERATION_FAILED":
        return ["retry_generation"]
    if issue == _CLOSED_ISSUE:
        return ["start_new_generation"]
    return []


def _now() -> str:
    return format_timestamp(utc_now())


def _capability_error(status) -> ApiError:
    missing = status.generation.missing_capabilities
    capability = missing[0] if missing else "IMAGE_GEN"
    return ApiError(
        503,
        "CAPABILITY_NOT_READY",
        templates.render(
            "CAPABILITY_NOT_READY",
            capability_label=templates.capability_label(capability) or "图片生成",
        ),
    )


def _prompt_not_approved() -> ApiError:
    return ApiError(409, "PROMPT_NOT_APPROVED", "执行提示词尚未通过独立审查，不能提交生成。")


def _prompt_stale() -> ApiError:
    return ApiError(409, "PROMPT_STALE", "执行提示词与当前确认需求不一致，请重新确认并编制提示词。")


def _reference_unavailable() -> ApiError:
    return ApiError(409, "REFERENCE_UNAVAILABLE", "参考图文件不可用，已阻止提交生成。")


async def _load_prompt(
    ctx: WorkflowContext, brief: dict[str, Any], *, legacy: bool = False
) -> tuple[Any, dict[str, Any]]:
    prompt_artifact = await state.approved_artifact(ctx, "prompt_plan")
    approval = await state.latest_approval(ctx, "generation_prompt")
    if prompt_artifact is None or approval is None:
        raise _prompt_not_approved()
    if approval.artifact_digest != prompt_artifact.digest:
        raise _prompt_not_approved()
    if approval.requirement_snapshot_digest != (await state.requirement_snapshot_digest(ctx)):
        raise _prompt_not_approved()
    try:
        payload = validate_data("prompt_plan", json.loads(prompt_artifact.payload_json)).model_dump(
            mode="json"
        )
    except (ContractError, json.JSONDecodeError):
        raise _prompt_stale() from None
    configured_size = ctx.project.image_size or ctx.settings.visual().image_size or ""
    errors = _candidate_errors(
        payload,
        _hard_requirements(brief),
        brief,
        configured_size,
        legacy=legacy,
    )
    if errors:
        raise _prompt_stale()
    if payload.get("size") != configured_size:
        raise _prompt_stale()
    return prompt_artifact, payload


async def _check_reference(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    reference_id = payload.get("reference_asset_id")
    if not reference_id:
        return
    visual = ctx.settings.visual()
    capabilities = ctx.registry.visual_capabilities(visual.adapter_id or "")
    if capabilities is None or capabilities.reference_image is not True:
        raise ApiError(
            503,
            "CAPABILITY_NOT_READY",
            templates.render("CAPABILITY_NOT_READY", capability_label="图片生成"),
        )
    async with read_session(ctx.session_factory) as session:
        asset = await session.get(Asset, reference_id)
    if asset is None or asset.project_id != ctx.project_id or asset.kind != "reference":
        raise _reference_unavailable()
    if (
        capabilities.reference_mime_types
        and asset.mime_type not in capabilities.reference_mime_types
    ):
        raise ApiError(422, "REFERENCE_FORMAT_UNSUPPORTED", "当前参考图格式不受生图适配器支持。")
    if (
        capabilities.provider_max_reference_bytes is not None
        and asset.byte_size > capabilities.provider_max_reference_bytes
    ):
        raise ApiError(413, "REFERENCE_TOO_LARGE", "参考图超过生图适配器的文件大小上限。")
    if resolve_asset_file(ctx.settings, asset) is None:
        raise _reference_unavailable()


async def _job_by_request_id(ctx: WorkflowContext, request_id: str) -> GenerationJob | None:
    async with read_session(ctx.session_factory) as session:
        return await session.scalar(
            select(GenerationJob).where(
                GenerationJob.project_id == ctx.project_id,
                GenerationJob.request_id == request_id,
            )
        )


async def _existing_job(
    ctx: WorkflowContext, request_id: str, prompt_artifact_id: str
) -> GenerationJob | None:
    # Request IDs are deterministic for a submission and for an acknowledged
    # retry. Looking them up exactly makes replay safe even after a later,
    # acknowledged generation has been created.
    existing = await _job_by_request_id(ctx, request_id)
    if existing is not None:
        return existing
    async with read_session(ctx.session_factory) as session:
        active = await session.scalar(
            select(GenerationJob)
            .where(
                GenerationJob.project_id == ctx.project_id,
                GenerationJob.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
    if active is not None and active.prompt_artifact_id != prompt_artifact_id:
        raise conflict("GENERATION_IN_PROGRESS", "当前项目已有进行中的生成任务，不能重复提交。")
    return active


async def _interrupted_job(
    ctx: WorkflowContext, prompt_artifact_id: str
) -> GenerationJob | None:
    async with read_session(ctx.session_factory) as session:
        return await session.scalar(
            select(GenerationJob).where(
                GenerationJob.project_id == ctx.project_id,
                GenerationJob.run_id == ctx.run_id,
                GenerationJob.prompt_artifact_id == prompt_artifact_id,
                GenerationJob.status == "failed",
                GenerationJob.error_code == "PROCESS_INTERRUPTED",
            ).order_by(GenerationJob.created_at.desc()).limit(1)
        )


async def _create_job(
    ctx: WorkflowContext,
    *,
    prompt_artifact: Any,
    payload: dict[str, Any],
    brief: dict[str, Any],
    request_id: str,
    input_digest: str,
    snapshot_digest: str,
    attempt: int = 1,
    previous: Generation | None = None,
    intent: GenerationIntent | None = None,
) -> tuple[GenerationJob, Generation]:
    visual = ctx.settings.visual()
    canonical_payload: dict[str, Any] = {
        "project_id": ctx.project_id,
        "request_id": request_id,
        "run_id": ctx.run_id,
        "confirmed_revision": ctx.project.confirmed_revision,
        "requirement_revision": ctx.run.requirement_revision,
        "stage": STAGE,
        "attempt": attempt,
        "input_digest": input_digest,
        "prompt_artifact_id": prompt_artifact.id,
        "prompt_digest": prompt_artifact.digest,
        "confirmed_snapshot_digest": snapshot_digest,
    }
    if previous is not None:
        canonical_payload["previous_generation_id"] = previous.id
        canonical_payload["previous_request_id"] = previous.request_id
        if previous.status == "unknown":
            canonical_payload["acknowledge_unknown"] = True
    canonical = dumps_json(canonical_payload)
    digest = request_digest(canonical)
    owner_account_id = ctx.project.owner_account_id
    operator_account_id = ctx.principal.account_id if ctx.principal is not None else None
    authorization_ref = job_authorization_ref(
        project_id=ctx.project_id,
        run_id=ctx.run_id,
        request_id=request_id,
        request_digest=digest,
        confirmed_snapshot_digest=snapshot_digest,
    )
    now = _now()
    async with write_transaction(ctx.session_factory) as session:
        if intent is not None:
            stored_intent = await session.get(GenerationIntent, intent.id)
            if stored_intent is None:
                raise conflict("INTENT_STATE_INVALID", "生成授权已结束，不能提交新任务。")
            if stored_intent.status == "accepted":
                stored_intent.status = "executing"
                stored_intent.updated_at = now
                intent.status = "executing"
            elif stored_intent.status != "executing":
                raise conflict("INTENT_STATE_INVALID", "生成授权已结束，不能提交新任务。")
        highest = await session.scalar(
            select(func.max(Generation.version_no)).where(Generation.project_id == ctx.project_id)
        )
        snapshot = snapshot_json(brief_from_project(ctx.project))
        generation = Generation(
            id=str(uuid4()),
            project_id=ctx.project_id,
            owner_account_id=owner_account_id,
            operator_account_id=operator_account_id,
            authorization_ref=generation_authorization_ref(
                project_id=ctx.project_id,
                request_id=request_id,
                request_digest=digest,
                confirmed_revision=ctx.project.confirmed_revision,
            ),
            intent_id=intent.id if intent is not None else None,
            version_no=(highest or 0) + 1,
            request_id=request_id,
            request_canonical=canonical,
            request_digest=digest,
            confirmed_revision=ctx.project.confirmed_revision,
            status="queued",
            stage="waiting_slot",
            brief_snapshot_json=snapshot,
            prompt=payload["positive_prompt"],
            image_size=payload["size"],
            reference_asset_id=payload.get("reference_asset_id"),
            adapter_id=visual.adapter_id,
            model=visual.model,
            service_fingerprint=service_fingerprint(visual),
            submit_attempted=0,
            created_at=now,
        )
        job = GenerationJob(
            id=str(uuid4()),
            project_id=ctx.project_id,
            run_id=ctx.run_id,
            owner_account_id=owner_account_id,
            operator_account_id=operator_account_id,
            authorization_ref=authorization_ref,
            intent_id=intent.id if intent is not None else None,
            request_id=request_id,
            request_digest=digest,
            prompt_artifact_id=prompt_artifact.id,
            confirmed_snapshot_digest=snapshot_digest,
            status="submit_pending",
            submit_attempted=0,
            created_at=now,
            updated_at=now,
        )
        session.add(generation)
        session.add(job)
        await session.flush()
        generation.workflow_job_id = job.id
        job.generation_id = generation.id
        if previous is not None:
            session.add(
                DiagnosticEvent(
                    id=str(uuid4()),
                    project_id=ctx.project_id,
                    event=(
                        "generation_retry_confirmed"
                        if previous.status == "failed"
                        else "generation_risk_acknowledged"
                    ),
                    detail_json=dumps_json(
                        {
                            "previous_generation_id": previous.id,
                            "previous_request_id": previous.request_id,
                            "previous_status": previous.status,
                            "new_generation_id": generation.id,
                            "new_request_id": request_id,
                        }
                    ),
                    created_at=now,
                )
            )
        await session.flush()
    return job, generation


def _schedule(
    app,
    job_id: str,
    generation_id: str,
    prompt_artifact_id: str,
    prompt_digest: str,
    snapshot_digest: str,
) -> None:
    task = asyncio.create_task(
        _run_job(app, job_id, generation_id, prompt_artifact_id, prompt_digest, snapshot_digest),
        name=f"workflow-generation-{job_id}",
    )
    app.state.generation_tasks.add(task)
    task.add_done_callback(app.state.generation_tasks.discard)


async def _mark_job(
    session_factory,
    job_id: str,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    result_asset_id: str | None = None,
    remote_task_id: str | None = None,
    submit_attempted: int | None = None,
) -> None:
    async with write_transaction(session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None:
            return
        job.status = status
        job.error_code = error_code
        job.error_message = error_message
        job.result_asset_id = result_asset_id
        job.remote_task_id = remote_task_id
        if submit_attempted is not None:
            job.submit_attempted = submit_attempted
        job.updated_at = _now()
        await session.flush()


async def _block_for_account(app, job_id: str, error: GenerationGuardError) -> None:
    """Stop a persisted task without pretending a remote request was cancelled."""

    async with read_session(app.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None:
            return
        project_id = job.project_id
        run_id = job.run_id
        owner_account_id = job.owner_account_id
        request_id = job.request_id
        generation = await session.get(Generation, job.generation_id) if job.generation_id else None
        remote_started = bool(generation is not None and generation.remote_task_id)
        asset_id = generation.result_asset_id if generation is not None else None
    now = _now()
    status = "unknown" if remote_started else "failed"
    async with write_transaction(app.state.session_factory) as session:
        stored_job = await session.get(GenerationJob, job_id)
        if stored_job is not None:
            stored_job.status = status
            stored_job.error_code = error.code
            stored_job.error_message = error.message
            stored_job.updated_at = now
        stored_generation = (
            await session.get(Generation, stored_job.generation_id)
            if stored_job is not None and stored_job.generation_id
            else None
        )
        if stored_generation is not None:
            stored_generation.status = status
            stored_generation.stage = "done"
            stored_generation.error_code = error.code
            stored_generation.error_message = error.message
            stored_generation.completed_at = now
        if asset_id:
            stored_asset = await session.get(Asset, asset_id)
            if stored_asset is not None:
                stored_asset.review_status = "rejected"
                stored_asset.review_note = "账号已停用，结果未发布"
        await session.flush()
    ctx = await state.load_context(app, project_id)
    if (
        ctx.run is not None
        and ctx.run.id == run_id
        and ctx.run.stage == STAGE
        and ctx.run.status in {"active", "blocked"}
    ):
        await state.set_stage(
                ctx,
                STAGE,
                "blocked",
                issue_code="ACCOUNT_DISABLED",
                message="账号已停用，后台生成已安全停止；恢复后不会自动续发。",
            )
    try:
        audit_now = app.state.clock.now()
        async with write_transaction(app.state.session_factory) as session:
            write_audit(
                session,
                event_type="generation_account_blocked",
                result="denied",
                now=audit_now,
                actor_kind="system",
                target_account_id=owner_account_id,
                target_kind="generation_job",
                target_id=job_id,
                subject_ref=token_digest("generation-job:" + request_id)[:48],
                reason_code=error.code,
                detail={"stage": "background", "remote_task_recorded": remote_started},
            )
    except Exception:  # pragma: no cover - audit outage must not mask the stop
        logger.exception("failed to audit blocked generation job %s", job_id)


async def _mark_unknown(
    app,
    job_id: str,
    *,
    error_code: str,
    error_message: str,
    remote_task_id: str | None = None,
) -> None:
    await _mark_job(
        app.state.session_factory,
        job_id,
        status="unknown",
        error_code=error_code,
        error_message=error_message,
        remote_task_id=remote_task_id,
        submit_attempted=1,
    )
    async with read_session(app.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None:
            return
        latest_job_id = await session.scalar(
            select(GenerationJob.id)
            .where(GenerationJob.run_id == job.run_id)
            .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc())
            .limit(1)
        )
        project_id = job.project_id
        run_id = job.run_id
    if latest_job_id != job_id:
        return
    ctx = await state.load_context(app, project_id)
    if ctx.run is None or ctx.run.id != run_id or ctx.run.stage != STAGE:
        return
    if ctx.run.status not in {"active", "blocked"}:
        return
    await state.set_stage(
        ctx,
        STAGE,
        "blocked",
        issue_code=_UNKNOWN_ISSUE,
        message=templates.render(_UNKNOWN_ISSUE),
        template_id=_UNKNOWN_ISSUE,
    )


async def _still_current(
    app, job_id: str, prompt_artifact_id: str, prompt_digest: str, snapshot_digest: str
) -> bool:
    async with read_session(app.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None or job.generation_id is None:
            return False
        project_id = job.project_id
        generation_id = job.generation_id
        latest_generation_id = await session.scalar(
            select(Generation.id)
            .where(Generation.project_id == project_id)
            .order_by(Generation.version_no.desc(), Generation.created_at.desc())
            .limit(1)
        )
    # A later generation for the same project supersedes this one.  This is
    # what keeps a late result from an acknowledged unknown task from
    # overwriting the replacement task or becoming publishable.
    if latest_generation_id != generation_id:
        return False
    ctx = await state.load_context(app, project_id)
    if ctx.run is None:
        return False
    if ctx.project.confirmed_revision != ctx.project.revision:
        return False
    if ctx.run.requirement_revision != ctx.project.revision:
        return False
    prompt = await state.approved_artifact(ctx, "prompt_plan")
    approval = await state.latest_approval(ctx, "generation_prompt")
    if prompt is None or prompt.id != prompt_artifact_id or prompt.digest != prompt_digest:
        return False
    if approval is None or approval.artifact_digest != prompt.digest:
        return False
    return approval.requirement_snapshot_digest == snapshot_digest


async def _advance_to_review(app, job_id: str) -> None:
    try:
        await ensure_job_active(app.state.session_factory, job_id)
    except GenerationGuardError as exc:
        await _block_for_account(app, job_id, exc)
        return
    async with read_session(app.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None:
            return
        project_id = job.project_id
        run_id = job.run_id
        intent_id = job.intent_id
    ctx = await state.load_context(app, project_id)
    if ctx.run is not None and ctx.run.id == run_id and ctx.run.stage == STAGE:
        await state.set_stage(ctx, "S09", "active")
    if intent_id is None:
        return
    # A v3 accepted fact advances through quality review and delivery-manifest
    # preparation without another human click. Approval and publish remain
    # explicit human actions in S10.
    try:
        fresh = await state.load_context(app, project_id)
        if (
            fresh.run is not None
            and fresh.run.id == run_id
            and fresh.run.stage == "S09"
            and fresh.run.status in {"active", "needs_input", "blocked"}
        ):
            await s09.handle(fresh, "review_candidate", {})
        fresh = await state.load_context(app, project_id)
        if (
            fresh.run is not None
            and fresh.run.id == run_id
            and fresh.run.stage == "S10"
            and fresh.run.status == "active"
        ):
            await s10.handle(fresh, "prepare_delivery", {})
    except (ApiError, GenerationGuardError) as exc:
        logger.warning("post-generation review chain stopped: %s", exc)
    except BaseException:
        logger.exception("post-generation review chain failed for job %s", job_id)


async def _latest_job(app, run_id: str) -> GenerationJob | None:
    async with read_session(app.state.session_factory) as session:
        return await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.run_id == run_id)
            .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc())
            .limit(1)
        )


async def _settle_terminal(app, job_id: str, error_code: str | None) -> None:
    """Keep one count and release slots only on proven terminal failure."""
    if error_code in {"RESULT_FETCH_FAILED", "ACCOUNT_DISABLED", "ACCOUNT_INACTIVE"}:
        if error_code == "RESULT_FETCH_FAILED":
            await _set_recovery_issue(app, job_id, "RESULT_RETRIEVAL_PENDING")
        return
    async with read_session(app.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None:
            return
        intent_id = job.intent_id
    if intent_id is not None:
        await terminate_intent(
            app.state.session_factory,
            intent_id=intent_id,
            reason=f"generation_terminal:{error_code or 'VISUAL_TASK_FAILED'}"[:64],
        )
    await _set_recovery_issue(app, job_id, "GENERATION_FAILED")


async def _set_recovery_issue(app, job_id: str, issue: str, message: str | None = None) -> None:
    async with read_session(app.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None:
            return
        project_id = job.project_id
        run_id = job.run_id
    ctx = await state.load_context(app, project_id)
    if ctx.run is None or ctx.run.id != run_id or ctx.run.stage != STAGE:
        return
    await state.set_stage(
        ctx,
        STAGE,
        "blocked",
        issue_code=issue,
        message=message or templates.render(issue),
        template_id=issue,
    )


def _uncertain_block(ctx: WorkflowContext) -> bool:
    return (
        ctx.run is not None
        and ctx.run.status == "blocked"
        and ctx.run.issue_code in _UNCERTAIN_BLOCK_CODES
    )


async def _close_tracking(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    if not _uncertain_block(ctx):
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if payload.get("acknowledge_local_close") is not True:
        raise conflict(
            "LOCAL_CLOSE_ACK_REQUIRED",
            "结束本地跟踪会保留原受理计数且不代表取消远端任务，请明确确认后再操作。",
        )
    if ctx.principal is None:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录。")
    job = await _latest_job(ctx.app, ctx.run_id)
    if job is None or job.intent_id is None:
        raise conflict("RECOVERY_NOT_AVAILABLE", "没有可结案的本地生成任务。")
    async with read_session(ctx.session_factory) as session:
        intent = await session.get(GenerationIntent, job.intent_id)
    if intent is None:
        raise conflict("RECOVERY_NOT_AVAILABLE", "没有可结案的生成授权。")
    if intent.status not in {"accepted", "executing"}:
        raise conflict("INTENT_STATE_INVALID", "生成授权已结束，不能重复结案。")
    changed = await close_intent_tracking(
        ctx.session_factory,
        intent_id=intent.id,
        operator_account_id=ctx.principal.account_id,
        reason="local_tracking_closed",
    )
    if not changed:
        raise conflict("INTENT_STATE_INVALID", "生成授权已结束，不能重复结案。")
    await _set_recovery_issue(
        ctx.app,
        job.id,
        _CLOSED_ISSUE,
        templates.render(_CLOSED_ISSUE),
    )


async def _start_replacement(
    ctx: WorkflowContext,
    payload: dict[str, Any],
    *,
    issue_code: str,
    previous_status: str,
    previous_intent_status: str,
    acknowledgement_key: str,
    acknowledgement_error: str,
    acknowledgement_message: str,
    request_prefix: str,
) -> None:
    if (
        ctx.run is None
        or ctx.run.status != "blocked"
        or ctx.run.issue_code != issue_code
    ):
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if payload.get(acknowledgement_key) is not True:
        raise conflict(acknowledgement_error, acknowledgement_message)
    if ctx.principal is None:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录。")
    job = await _latest_job(ctx.app, ctx.run_id)
    if job is None or job.generation_id is None or job.intent_id is None:
        raise conflict(acknowledgement_error, "请从当前任务发起新的确认。")
    requested_previous = payload.get("previous_generation_id")
    if requested_previous not in (None, "") and requested_previous != job.generation_id:
        raise conflict(acknowledgement_error, "请从当前任务发起新的确认。")
    previous = await _generation_by_id(ctx, job.generation_id)
    allowed_previous_statuses = {previous_status}
    if (
        previous_status == "unknown"
        and issue_code == _CLOSED_ISSUE
        and previous_intent_status == "closed"
    ):
        # 旧版本 recover_generation 可能把已明确失败的生成任务保留为 failed，
        # 但随后 close_generation_tracking 已关闭授权。此时仍应允许用户显式
        # 确认风险后发起新任务，而不是卡在无法前进的 LOCAL_TRACKING_CLOSED。
        allowed_previous_statuses.add("failed")
    if previous_status == "rejected" and issue_code == "IMAGE_NOT_APPROVED":
        # 质检拒绝时 Generation 本身已成功产出图片，但对应工作流任务为
        # rejected；显式重新生成应允许替换这张未获批准的图片。
        allowed_previous_statuses.add("succeeded")
    if (
        previous is None
        or previous.project_id != ctx.project_id
        or previous.status not in allowed_previous_statuses
    ):
        message = (
            "当前任务不是可重新生成的已失败任务。"
            if "failed" in allowed_previous_statuses
            else "当前任务不是可风险确认的未知任务。"
        )
        raise conflict(acknowledgement_error, message)
    async with read_session(ctx.session_factory) as session:
        previous_intent = await session.get(GenerationIntent, job.intent_id)
    if previous_intent is None or previous_intent.status != previous_intent_status:
        message = (
            "原生成授权尚未结束，不能重新生成。"
            if previous_status == "failed"
            else "请先结束本地跟踪，再发起新任务。"
        )
        raise conflict("INTENT_STATE_INVALID", message)

    final_artifact, brief, _execution_snapshot = await _require_confirmed(ctx)
    prompt_artifact, prompt_payload = await _load_prompt(
        ctx, brief, legacy=_execution_snapshot is None
    )
    if prompt_payload["size"] != brief["size"]:
        raise conflict("PROMPT_STALE", "执行提示词与当前确认尺寸不一致。")
    reference_usage = brief.get("reference_usage") or "none"
    image_size = brief["size"]
    request_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{request_prefix}:{previous.id}:{prompt_artifact.id}:{final_artifact.digest}",
        )
    )
    expected_digest = intent_request_digest(
        project_id=ctx.project_id,
        run_id=ctx.run_id,
        owner_account_id=ctx.project.owner_account_id or "",
        operator_account_id=ctx.principal.account_id,
        request_id=request_id,
        requirement_revision=ctx.run.requirement_revision,
        artifact_id=final_artifact.id,
        artifact_digest=final_artifact.digest,
        reference_asset_id=ctx.project.reference_asset_id,
        reference_usage=reference_usage,
        image_size=image_size,
    )
    async with read_session(ctx.session_factory) as session:
        existing_intent = await intent_for_request(
            session, project_id=ctx.project_id, request_id=request_id
        )
    if existing_intent is not None:
        if existing_intent.request_digest != expected_digest:
            raise conflict(
                "INTENT_REPLAY_CONFLICT",
                "同一请求标识已绑定不同的生成参数，请刷新后重新确认。",
            )
        if existing_intent.status not in {"accepted", "executing"}:
            raise conflict("INTENT_STATE_INVALID", "新任务授权已结束。")
        intent = existing_intent
    else:
        async with write_transaction(ctx.session_factory) as session:
            project = await session.get(Project, ctx.project_id)
            run = await session.get(WorkflowRun, ctx.run_id)
            artifact = await session.get(Artifact, final_artifact.id)
            if project is None or run is None or artifact is None:
                raise conflict("REQUIREMENTS_CHANGED", "当前需求已变化，请刷新后重新确认。")
            if (
                project.revision != ctx.project.revision
                or run.requirement_revision != ctx.run.requirement_revision
                or artifact.digest != final_artifact.digest
                or artifact.status != "approved"
                or project.owner_account_id is None
                or project.owner_account_id != ctx.principal.account_id
            ):
                raise conflict("REQUIREMENTS_CHANGED", "当前需求已变化，请刷新后重新确认。")
            result = await accept_in_session(
                session,
                settings=ctx.settings,
                project=project,
                run=run,
                operator_account_id=ctx.principal.account_id,
                artifact=artifact,
                requirement_revision=run.requirement_revision,
                reference_usage=reference_usage,
                image_size=image_size,
                request_id=request_id,
                approval_id=previous_intent.approval_id,
                now=ctx.app.state.clock.now(),
            )
            intent = result.intent

    existing_job = await _job_by_request_id(ctx, request_id)
    if existing_job is not None:
        return
    input_digest = _input_digest(brief, prompt_payload)
    try:
        new_job, generation = await _create_job(
            ctx,
            prompt_artifact=prompt_artifact,
            payload=prompt_payload,
            brief=brief,
            request_id=request_id,
            input_digest=input_digest,
            snapshot_digest=final_artifact.digest,
            attempt=2,
            previous=previous,
            intent=intent,
        )
    except IntegrityError:
        existing_job = await _job_by_request_id(ctx, request_id)
        if existing_job is not None:
            return
        raise
    await state.set_stage(ctx, STAGE, "active")
    _schedule(
        ctx.app,
        new_job.id,
        generation.id,
        prompt_artifact.id,
        prompt_artifact.digest,
        final_artifact.digest,
    )


async def _retry_generation(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    await _start_replacement(
        ctx,
        payload,
        issue_code="GENERATION_FAILED",
        previous_status="failed",
        previous_intent_status="failed",
        acknowledgement_key="acknowledge_new_attempt",
        acknowledgement_error="NEW_ATTEMPT_ACK_REQUIRED",
        acknowledgement_message="重新生成会新增一次受理计数并重新占用生成槽位，请明确确认后再操作。",
        request_prefix="v3-generation-retry",
    )


async def _start_new_generation(ctx: WorkflowContext, payload: dict[str, Any]) -> None:
    await _start_replacement(
        ctx,
        payload,
        issue_code=_CLOSED_ISSUE,
        previous_status="unknown",
        previous_intent_status="closed",
        acknowledgement_key="acknowledge_risk",
        acknowledgement_error="RISK_ACK_REQUIRED",
        acknowledgement_message="新任务会新计一次并重新占用全部槽位，且旧任务仍可能在远端执行或计费，请明确确认。",
        request_prefix="v3-generation-risk",
    )

async def _verify(ctx: WorkflowContext) -> None:
    if (
        ctx.run is None
        or ctx.run.status != "blocked"
        or ctx.run.issue_code not in _RECOVERY_BLOCK_CODES
    ):
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    # Read-only: no provider call, no new intent, no count, no slot change.


async def _recover(ctx: WorkflowContext) -> None:
    if (
        ctx.run is None
        or ctx.run.status != "blocked"
        or ctx.run.issue_code not in _RECOVERY_BLOCK_CODES
    ):
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    job = await _latest_job(ctx.app, ctx.run_id)
    if job is None or job.generation_id is None:
        raise ApiError(409, "RECOVERY_NOT_AVAILABLE", "没有可恢复的原生成任务。")
    resumed = await resume_interrupted(ctx.app, job.id)
    if resumed is not None:
        await state.set_stage(ctx, STAGE, "active")
        _schedule(
            ctx.app,
            job.id,
            resumed["generation_id"],
            resumed["prompt_artifact_id"],
            resumed["prompt_digest"],
            resumed["snapshot_digest"],
        )
        return
    outcome = await recover_remote(ctx.app, job.id)
    if outcome == "succeeded":
        await _advance_to_review(ctx.app, job.id)
    elif outcome == "failed":
        await _settle_terminal(ctx.app, job.id, "VISUAL_TASK_FAILED")
    elif outcome == "retryable":
        await _set_recovery_issue(
            ctx.app,
            job.id,
            "RESULT_RETRIEVAL_PENDING",
            "结果文件暂时无法找回，原任务与一次计数已保留，可稍后再次核实。",
        )
    elif outcome == "unknown":
        await _mark_unknown(
            ctx.app,
            job.id,
            error_code="VISUAL_OUTCOME_UNKNOWN",
            error_message="生图结果未确认",
            remote_task_id=job.remote_task_id,
        )


async def _run_job(
    app,
    job_id: str,
    generation_id: str,
    prompt_artifact_id: str,
    prompt_digest: str,
    snapshot_digest: str,
) -> None:
    try:
        try:
            await ensure_job_active(app.state.session_factory, job_id)
        except GenerationGuardError as exc:
            await _block_for_account(app, job_id, exc)
            return
        if not await _still_current(
            app, job_id, prompt_artifact_id, prompt_digest, snapshot_digest
        ):
            await _mark_job(
                app.state.session_factory,
                job_id,
                status="failed",
                error_code="SUPERSEDED",
                error_message="需求或执行提示词已更新，旧生成任务未提交。",
            )
            await _settle_terminal(app, job_id, "SUPERSEDED")
            async with write_transaction(app.state.session_factory) as session:
                generation = await session.get(Generation, generation_id)
                if generation is not None and generation.status == "queued":
                    generation.status = "failed"
                    generation.stage = "done"
                    generation.error_code = "SUPERSEDED"
                    generation.error_message = "需求或执行提示词已更新，旧生成任务未提交。"
                    generation.completed_at = _now()
                    await session.flush()
            return

        await _mark_job(app.state.session_factory, job_id, status="submitted", submit_attempted=1)
        await execute_generation(app, generation_id)

        # 供应商返回时再次核对当前确认版本；旧版本结果只保留审计信息，
        # 不能进入待审核候选，也不能覆盖当前需求。
        if not await _still_current(
            app, job_id, prompt_artifact_id, prompt_digest, snapshot_digest
        ):
            async with read_session(app.state.session_factory) as session:
                stale_generation = await session.get(Generation, generation_id)
                stale_asset_id = stale_generation.result_asset_id if stale_generation else None
            if stale_asset_id:
                async with write_transaction(app.state.session_factory) as session:
                    stale_asset = await session.get(Asset, stale_asset_id)
                    if stale_asset is not None:
                        stale_asset.review_status = "rejected"
                        stale_asset.review_note = "需求已更新，旧生成结果已失效"
                        await session.flush()
            async with write_transaction(app.state.session_factory) as session:
                stale_generation = await session.get(Generation, generation_id)
                if stale_generation is not None:
                    stale_generation.status = "failed"
                    stale_generation.stage = "done"
                    stale_generation.error_code = "SUPERSEDED"
                    stale_generation.error_message = "需求已更新，旧生成结果已失效"
                    stale_generation.completed_at = _now()
                    await session.flush()
            await _mark_job(
                app.state.session_factory,
                job_id,
                status="failed",
                error_code="SUPERSEDED",
                error_message="需求已更新，旧生成结果已失效",
                remote_task_id=stale_generation.remote_task_id if stale_generation else None,
                submit_attempted=1,
            )
            await _settle_terminal(app, job_id, "SUPERSEDED")
            return

        async with read_session(app.state.session_factory) as session:
            generation = await session.get(Generation, generation_id)
        if generation is not None and generation.error_code in {
            "ACCOUNT_DISABLED",
            "ACCOUNT_INACTIVE",
        }:
            await _block_for_account(
                app,
                job_id,
                GenerationGuardError(
                    generation.error_code,
                    generation.error_message or "账号当前不可用，后台任务已安全停止。",
                ),
            )
            return
        if generation is None:
            await _mark_unknown(
                app,
                job_id,
                error_code="VISUAL_OUTCOME_UNKNOWN",
                error_message="生图结果未确认",
            )
            return

        if generation.status == "succeeded" and generation.result_asset_id:
            asset_id = generation.result_asset_id
            async with read_session(app.state.session_factory) as session:
                asset = await session.get(Asset, asset_id)
            if asset is not None:
                path = resolve_asset_file(app.state.settings, asset)
                digest = None
                if path is not None:
                    try:
                        digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    except OSError:
                        digest = None
                if digest is None:
                    await _mark_job(
                        app.state.session_factory,
                        job_id,
                        status="failed",
                        error_code="RESULT_FILE_MISSING",
                        error_message="结果图片文件不可用，未发布。",
                    )
                    await _settle_terminal(app, job_id, "RESULT_FILE_MISSING")
                    return
                async with write_transaction(app.state.session_factory) as session:
                    stored = await session.get(Asset, asset_id)
                    if stored is not None:
                        stored.sha256 = digest
                        stored.review_status = "quarantined"
                        stored.review_note = "待人工审核"
                        await session.flush()
                await _mark_job(
                    app.state.session_factory,
                    job_id,
                    status="quarantined",
                    result_asset_id=asset_id,
                    remote_task_id=generation.remote_task_id,
                    submit_attempted=1,
                )
                await _advance_to_review(app, job_id)
                return

        status = generation.status if generation.status in {"failed", "unknown"} else "unknown"
        if status == "unknown":
            await _mark_unknown(
                app,
                job_id,
                error_code=generation.error_code or "VISUAL_OUTCOME_UNKNOWN",
                error_message=generation.error_message or "生图结果未确认",
                remote_task_id=generation.remote_task_id,
            )
        else:
            await _mark_job(
                app.state.session_factory,
                job_id,
                status=status,
                error_code=generation.error_code or "VISUAL_TASK_FAILED",
                error_message=generation.error_message or "生成任务失败",
                remote_task_id=generation.remote_task_id,
                submit_attempted=1,
            )
            await _settle_terminal(app, job_id, generation.error_code)
    except asyncio.CancelledError:
        raise
    except Exception:
        await _mark_unknown(
            app,
            job_id,
            error_code="VISUAL_OUTCOME_UNKNOWN",
            error_message="生图结果未确认",
        )


def _request_id(ctx: WorkflowContext, prompt_artifact_id: str, final_digest: str) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            f"v2-generation:{ctx.project_id}:{ctx.run_id}:{prompt_artifact_id}:{final_digest}",
        )
    )


def _ack_request_id(
    ctx: WorkflowContext,
    prompt_artifact_id: str,
    final_digest: str,
    previous: Generation,
) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            "v2-generation-ack:"
            f"{ctx.project_id}:{ctx.run_id}:{prompt_artifact_id}:{final_digest}:"
            f"{previous.id}:{previous.request_id}",
        )
    )


async def _generation_by_id(ctx: WorkflowContext, generation_id: str) -> Generation | None:
    async with read_session(ctx.session_factory) as session:
        return await session.get(Generation, generation_id)


def _input_digest(brief: dict[str, Any], prompt: dict[str, Any]) -> str:
    return digest_payload(
        {
            "final_brief": brief,
            "prompt": prompt,
            "image_size": prompt["size"],
            "reference_asset_id": prompt.get("reference_asset_id"),
        }
    )


def _unknown_ack_required() -> ApiError:
    return conflict("UNKNOWN_ACK_REQUIRED", templates.render("GENERATION_UNKNOWN"))


async def _submit(ctx: WorkflowContext, action_payload: dict[str, Any]) -> None:
    if not _replayable(ctx):
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if ctx.run.status not in {"active", "blocked"}:
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    if (
        ctx.run.status == "blocked"
        and ctx.run.issue_code not in _RECOVERABLE_BLOCK_CODES
    ):
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))

    final_artifact, brief, _execution_snapshot = await _require_confirmed(ctx)
    prompt_artifact, prompt_payload = await _load_prompt(
        ctx, brief, legacy=_execution_snapshot is None
    )

    status = build_config_status(ctx.settings, ctx.registry)
    if not status.generation.allowed:
        raise _capability_error(status)

    await _check_reference(ctx, prompt_payload)
    intent: GenerationIntent | None = None
    if ctx.project.owner_account_id is not None:
        intent = await latest_intent(
            ctx.session_factory, project_id=ctx.project_id, run_id=ctx.run_id
        )
    if intent is not None:
        if intent.artifact_digest != final_artifact.digest:
            raise conflict("INTENT_STALE", "需求卡已变化，请重新确认后再生成。")
        if intent.requirement_revision != ctx.run.requirement_revision:
            raise conflict("INTENT_STALE", "需求版本已变化，请重新确认后再生成。")
        if intent.status not in {"accepted", "executing"}:
            raise conflict("INTENT_STATE_INVALID", "生成授权已结束，请重新确认后再生成。")
        request_id = intent.request_id
    else:
        request_id = _request_id(ctx, prompt_artifact.id, final_artifact.digest)
    original = await _job_by_request_id(ctx, request_id)

    if original is not None:
        if original.status != "unknown":
            # Any repeated click for an already-submitted request must replay
            # the original task and must not call the provider again.
            return
        if intent is not None:
            # A v3 intent-backed task may not be resubmitted under the same
            # authorization. The user must close local tracking and then
            # explicitly accept the risk of a separately counted new task.
            raise conflict(
                "LOCAL_CLOSURE_REQUIRED",
                templates.render("LOCAL_CLOSURE_REQUIRED"),
            )
        ack_value = action_payload.get("acknowledge_unknown")
        if ack_value is None:
            # The original idempotent request remains a safe no-op while the
            # outcome is unconfirmed. It may already have been billed.
            return
        if ack_value is not True:
            raise _unknown_ack_required()
        previous_id = action_payload.get("previous_generation_id")
        if not isinstance(previous_id, str) or not previous_id:
            raise _unknown_ack_required()
        previous = await _generation_by_id(ctx, previous_id)
        if (
            previous is None
            or previous.id != original.generation_id
            or previous.project_id != ctx.project_id
            or previous.status != "unknown"
        ):
            raise _unknown_ack_required()
        ack_request_id = _ack_request_id(ctx, prompt_artifact.id, final_artifact.digest, previous)
        existing_ack = await _job_by_request_id(ctx, ack_request_id)
        if existing_ack is not None:
            return
        input_digest = _input_digest(brief, prompt_payload)
        try:
            job, generation = await _create_job(
                ctx,
                prompt_artifact=prompt_artifact,
                payload=prompt_payload,
                brief=brief,
                request_id=ack_request_id,
                input_digest=input_digest,
                snapshot_digest=final_artifact.digest,
                attempt=2,
                previous=previous,
                intent=intent,
            )
        except IntegrityError:
            existing_ack = await _job_by_request_id(ctx, ack_request_id)
            if existing_ack is not None:
                return
            raise
        await state.set_stage(ctx, STAGE, "active")
        _schedule(
            ctx.app,
            job.id,
            generation.id,
            prompt_artifact.id,
            prompt_artifact.digest,
            final_artifact.digest,
        )
        return

    if ctx.run.status == "blocked":
        # A recoverable interruption with a provably unsubmitted job is an
        # idempotent replay: return the stored failed job without a provider
        # call or a replacement generation. Any other blocked run cannot be
        # acknowledged safely, so no new task may be created.
        if ctx.run.issue_code == "PROCESS_INTERRUPTED":
            interrupted = await _interrupted_job(ctx, prompt_artifact.id)
            if interrupted is not None:
                return
        raise _unknown_ack_required()
    existing = await _existing_job(ctx, request_id, prompt_artifact.id)
    if existing is not None:
        return

    input_digest = _input_digest(brief, prompt_payload)
    try:
        job, generation = await _create_job(
            ctx,
            prompt_artifact=prompt_artifact,
            payload=prompt_payload,
            brief=brief,
            request_id=request_id,
            input_digest=input_digest,
            snapshot_digest=final_artifact.digest,
            intent=intent,
        )
    except IntegrityError:
        existing = await _existing_job(ctx, request_id, prompt_artifact.id)
        if existing is not None:
            return
        raise
    _schedule(
        ctx.app,
        job.id,
        generation.id,
        prompt_artifact.id,
        prompt_artifact.digest,
        final_artifact.digest,
    )


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action == "verify_generation":
        await _verify(ctx)
        return
    if action == "retry_generation":
        await _retry_generation(ctx, payload or {})
        return
    if action == "recover_generation":
        await _recover(ctx)
        return
    if action == "close_generation_tracking":
        await _close_tracking(ctx, payload or {})
        return
    if action == "start_new_generation":
        await _start_new_generation(ctx, payload or {})
        return
    if action != "submit_generation":
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    await _submit(ctx, payload or {})


__all__ = ["ACTIONS", "STAGE", "available", "handle"]

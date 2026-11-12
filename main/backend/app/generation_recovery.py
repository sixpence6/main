from __future__ import annotations

import hashlib
import time
from typing import Any

from fastapi import FastAPI

from app.adapters.base import ProviderError, ProviderOutcome
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.generation_guard import (
    GenerationGuardError,
    ensure_generation_active_in_session,
)
from app.models import Asset, Generation, Project
from app.recovery_states import (
    AUTHORIZED_NOT_CALLED,
    SUBMITTED,
    UNKNOWN,
    RecoveryRecord,
    load_recovery_record,
    require_action,
)
from app.services.assets import resolve_asset_file
from app.services.generations import (
    ClaimedGeneration,
    _deliver_result,
    _finish_generation,
)
from app.timeutil import format_timestamp, utc_now
from app.workflow.tables import GenerationJob

_TERMINAL_EVIDENCE = {"rejected", "not_submitted"}


async def _load_record(app: FastAPI, job_id: str) -> RecoveryRecord:
    async with read_session(app.state.session_factory) as session:
        return await load_recovery_record(session, job_id)


async def _prepare(app: FastAPI, job_id: str, generation_id: str, stage: str) -> ClaimedGeneration:
    async with write_transaction(app.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        generation = await session.get(Generation, generation_id)
        if job is None or generation is None:
            raise ApiError(409, "RECOVERY_NOT_AVAILABLE", "原生成任务记录不可用。")
        try:
            await ensure_generation_active_in_session(session, generation_id)
        except GenerationGuardError as exc:
            raise ApiError(409, exc.code, exc.message) from None
        now = format_timestamp(utc_now())
        generation.status = "running"
        generation.stage = stage
        generation.error_code = None
        generation.error_message = None
        generation.completed_at = None
        job.status = "remote_pending"
        job.error_code = None
        job.error_message = None
        job.submit_attempted = 1
        project = await session.get(Project, generation.project_id)
        if project is not None:
            project.updated_at = now
        await session.flush()
        return ClaimedGeneration(
            id=generation.id,
            project_id=generation.project_id,
            version_no=generation.version_no,
            request_id=generation.request_id,
            prompt=generation.prompt,
            brief_snapshot_json=generation.brief_snapshot_json,
            image_size=generation.image_size,
            reference_asset_id=generation.reference_asset_id,
            adapter_id=generation.adapter_id,
            model=generation.model,
            started_monotonic=time.monotonic(),
        )


async def resume_interrupted(app: FastAPI, job_id: str) -> dict[str, Any] | None:
    """Resume a provably unsubmitted task after an independent process stop.

    This is an explicit user action. It reuses the original intent, count and
    slots, and never creates a new provider submission outside the normal
    single-submit claim path.
    """
    record = await _load_record(app, job_id)
    job, generation = record.job, record.generation
    if job.status != "failed" or job.error_code != "PROCESS_INTERRUPTED":
        return None
    # Consuming the final authorization is legal only while every persisted
    # fact still proves that nothing reached the provider boundary.
    if record.state != AUTHORIZED_NOT_CALLED:
        return None
    require_action(record.state, "consume_authorization")
    from app.final_confirmation import require_authorized_job
    await require_authorized_job(app.state.session_factory, job_id)
    async with read_session(app.state.session_factory) as session:
        from app.workflow.tables import Artifact

        artifact = await session.get(Artifact, job.prompt_artifact_id)
        if artifact is None:
            raise ApiError(409, "RECOVERY_NOT_AVAILABLE", "执行提示词记录不可用。")
        prompt_digest = artifact.digest
    async with write_transaction(app.state.session_factory) as session:
        current_job = await session.get(GenerationJob, job_id)
        current_generation = await session.get(Generation, generation.id)
        if current_job is None or current_generation is None:
            raise ApiError(409, "RECOVERY_NOT_AVAILABLE", "原生成任务记录不可用。")
        try:
            await ensure_generation_active_in_session(session, current_generation.id)
        except GenerationGuardError as exc:
            raise ApiError(409, exc.code, exc.message) from None
        now = format_timestamp(utc_now())
        current_generation.status = "queued"
        current_generation.stage = "waiting_slot"
        current_generation.error_code = None
        current_generation.error_message = None
        current_generation.started_at = None
        current_generation.completed_at = None
        current_job.status = "submit_pending"
        current_job.error_code = None
        current_job.error_message = None
        project = await session.get(Project, generation.project_id)
        if project is not None:
            project.updated_at = now
        await session.flush()
    return {
        "status": "resumed",
        "generation_id": generation.id,
        "prompt_artifact_id": job.prompt_artifact_id,
        "prompt_digest": prompt_digest,
        "snapshot_digest": job.confirmed_snapshot_digest,
    }


async def _finalize_job(app: FastAPI, job_id: str, generation_id: str) -> str:
    async with read_session(app.state.session_factory) as session:
        generation = await session.get(Generation, generation_id)
        if generation is None:
            return "unknown"
        status = generation.status
        result_asset_id = generation.result_asset_id
        remote_task_id = generation.remote_task_id
        error_code = generation.error_code
        error_message = generation.error_message
    if status == "succeeded" and result_asset_id:
        digest: str | None = None
        async with read_session(app.state.session_factory) as session:
            asset = await session.get(Asset, result_asset_id)
            if asset is not None:
                path = resolve_asset_file(app.state.settings, asset)
                if path is not None:
                    try:
                        digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    except OSError:
                        digest = None
        if digest is None:
            await _finish_generation(
                app.state.session_factory,
                generation_id,
                status="failed",
                error_code="RESULT_FILE_MISSING",
                error_message="结果图片文件不可用，未发布。",
            )
            await _finalize_job(app, job_id, generation_id)
            return "failed"
        async with write_transaction(app.state.session_factory) as session:
            asset = await session.get(Asset, result_asset_id)
            if asset is not None:
                asset.sha256 = digest
                asset.review_status = "quarantined"
                asset.review_note = "待人工审核"
            await session.flush()
        await _finish_job(
            app,
            job_id,
            status="quarantined",
            result_asset_id=result_asset_id,
            remote_task_id=remote_task_id,
        )
        return "succeeded"
    if status == "failed" and error_code == "RESULT_FETCH_FAILED":
        await _finish_job(
            app,
            job_id,
            status="failed",
            error_code=error_code,
            error_message=error_message,
            remote_task_id=remote_task_id,
        )
        return "retryable"
    if status == "failed":
        await _finish_job(
            app,
            job_id,
            status="failed",
            error_code=error_code or "VISUAL_TASK_FAILED",
            error_message=error_message or "生成任务失败",
            remote_task_id=remote_task_id,
        )
        return "failed"
    if status == "unknown":
        await _finish_job(
            app,
            job_id,
            status="unknown",
            error_code=error_code or "VISUAL_OUTCOME_UNKNOWN",
            error_message=error_message or "生图结果未确认",
            remote_task_id=remote_task_id,
        )
        return "unknown"
    return "pending"


async def _finish_job(
    app: FastAPI,
    job_id: str,
    *,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
    result_asset_id: str | None = None,
    remote_task_id: str | None = None,
) -> None:
    async with write_transaction(app.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None:
            return
        now = format_timestamp(utc_now())
        job.status = status
        job.error_code = error_code
        job.error_message = error_message
        if result_asset_id is not None:
            job.result_asset_id = result_asset_id
        if remote_task_id is not None:
            job.remote_task_id = remote_task_id
        job.updated_at = now
        await session.flush()


def _terminal_error(outcome: ProviderOutcome) -> tuple[str, str]:
    if outcome.evidence == "not_submitted":
        return (
            outcome.error_code or "VISUAL_NOT_SUBMITTED",
            outcome.error_message or "生成任务未提交",
        )
    return outcome.error_code or "VISUAL_TASK_FAILED", outcome.error_message or "生成任务失败"


async def recover_remote(app: FastAPI, job_id: str) -> str:
    record = await _load_record(app, job_id)
    # Verification/retrieval is the only allowed provider access in these
    # states. This function never calls adapter.submit.
    if record.state not in (SUBMITTED, UNKNOWN):
        raise ApiError(
            409,
            "RECOVERY_ACTION_NOT_ALLOWED",
            "当前任务不能核实远端结果，只能按原授权恢复或继续准备。",
        )
    require_action(record.state, "verify_submission")
    job, generation = record.job, record.generation
    role = app.state.settings.visual()
    adapter = app.state.registry.build_visual(role, app.state.http)
    if adapter is None:
        raise ApiError(503, "ADAPTER_UNAVAILABLE", "生图模型适配器不可用，无法核实原任务。")
    remote_task_id = generation.remote_task_id or job.remote_task_id
    locator = None
    if generation.result_locator_json:
        try:
            import json

            data = json.loads(generation.result_locator_json)
            locator = data.get("locator") if isinstance(data, dict) else data
        except (TypeError, ValueError):
            locator = None
    outcome: ProviderOutcome | None = None
    if remote_task_id:
        try:
            outcome = await adapter.poll(remote_task_id)
        except ProviderError as exc:
            if exc.evidence == "result_available":
                locator = exc.result_locator or locator
            elif exc.evidence in _TERMINAL_EVIDENCE:
                await _prepare(app, job_id, generation.id, "waiting_remote")
                await _finish_generation(
                    app.state.session_factory,
                    generation.id,
                    status="failed",
                    error_code=exc.error_code or "VISUAL_TASK_FAILED",
                    error_message=str(exc) or "生成任务失败",
                    remote_task_id=remote_task_id,
                )
                return await _finalize_job(app, job_id, generation.id)
            else:
                return "unknown"
        except Exception:
            return "unknown"
    if outcome is not None and outcome.state == "pending":
        return "pending"
    if outcome is not None and outcome.state == "unknown":
        await _prepare(app, job_id, generation.id, "waiting_remote")
        await _finish_generation(
            app.state.session_factory,
            generation.id,
            status="unknown",
            error_code=outcome.error_code or "VISUAL_OUTCOME_UNKNOWN",
            error_message=outcome.error_message or "生图结果未确认",
            remote_task_id=remote_task_id,
            result_locator=outcome.result_locator or locator,
            result_expires_at=outcome.result_expires_at,
        )
        return await _finalize_job(app, job_id, generation.id)
    if outcome is not None and outcome.state == "failed":
        code, message = _terminal_error(outcome)
        await _prepare(app, job_id, generation.id, "waiting_remote")
        await _finish_generation(
            app.state.session_factory,
            generation.id,
            status="failed",
            error_code=code,
            error_message=message,
            remote_task_id=remote_task_id,
            result_locator=outcome.result_locator or locator,
            result_expires_at=outcome.result_expires_at,
        )
        return await _finalize_job(app, job_id, generation.id)

    if outcome is None and locator is None:
        return "unsupported" if adapter.capabilities.asynchronous is False and not remote_task_id else "unknown"
    if outcome is None:
        outcome = ProviderOutcome(
            state="succeeded",
            remote_task_id=remote_task_id,
            result_locator=locator,
        )
    claimed = await _prepare(
        app,
        job_id,
        generation.id,
        "fetching_result" if (outcome.image_bytes is None and locator) else "waiting_remote",
    )
    await _deliver_result(
        app,
        claimed,
        adapter,
        outcome,
        fallback_locator=locator,
        fallback_remote_task_id=remote_task_id,
    )
    return await _finalize_job(app, job_id, generation.id)


__all__ = ["recover_remote", "resume_interrupted"]

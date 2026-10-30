from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.adapters.base import BriefData
from app.db import read_session, write_transaction
from app.errors import ApiError, conflict
from app.models import Asset, Generation, Project
from app.presenters import brief_from_project, dumps_json, snapshot_json
from app.services.assets import resolve_asset_file
from app.services.config_status import build_config_status
from app.services.generations import (
    execute_generation,
    parse_image_size,
    request_digest,
    service_fingerprint,
)
from app.timeutil import format_timestamp, utc_now
from app.workflow import state, templates
from app.workflow.contracts import ContractError, digest_payload, validate_data
from app.workflow.stages.s07 import _candidate_errors, _hard_requirements, _require_confirmed
from app.workflow.state import WorkflowContext
from app.workflow.tables import Approval, GenerationJob

STAGE = "S08"
ACTIONS = ["submit_generation"]

_ACTIVE_JOB_STATUSES = ("created", "submit_pending", "submitted", "remote_pending", "result_fetched")


def available(ctx: WorkflowContext) -> list[str]:
    if ctx.run is None or ctx.run.stage != STAGE or ctx.run.status != "active":
        return []
    return list(ACTIONS)


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
    ctx: WorkflowContext, brief: dict[str, Any]
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
    configured_size = (ctx.project.image_size or ctx.settings.visual().image_size or "")
    errors = _candidate_errors(payload, _hard_requirements(brief), brief, configured_size)
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
    if capabilities.reference_mime_types and asset.mime_type not in capabilities.reference_mime_types:
        raise ApiError(422, "REFERENCE_FORMAT_UNSUPPORTED", "当前参考图格式不受生图适配器支持。")
    if (
        capabilities.provider_max_reference_bytes is not None
        and asset.byte_size > capabilities.provider_max_reference_bytes
    ):
        raise ApiError(413, "REFERENCE_TOO_LARGE", "参考图超过生图适配器的文件大小上限。")
    if resolve_asset_file(ctx.settings, asset) is None:
        raise _reference_unavailable()


async def _existing_job(ctx: WorkflowContext, prompt_artifact_id: str) -> GenerationJob | None:
    async with read_session(ctx.session_factory) as session:
        existing = await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.run_id == ctx.run_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            return existing
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


async def _create_job(
    ctx: WorkflowContext,
    *,
    prompt_artifact: Any,
    payload: dict[str, Any],
    brief: dict[str, Any],
    request_id: str,
    input_digest: str,
    snapshot_digest: str,
) -> tuple[GenerationJob, Generation]:
    visual = ctx.settings.visual()
    canonical = dumps_json(
        {
            "project_id": ctx.project_id,
            "request_id": request_id,
            "run_id": ctx.run_id,
            "confirmed_revision": ctx.project.confirmed_revision,
            "requirement_revision": ctx.run.requirement_revision,
            "stage": STAGE,
            "attempt": 1,
            "input_digest": input_digest,
            "prompt_artifact_id": prompt_artifact.id,
            "prompt_digest": prompt_artifact.digest,
            "confirmed_snapshot_digest": snapshot_digest,
        }
    )
    digest = request_digest(canonical)
    now = _now()
    async with write_transaction(ctx.session_factory) as session:
        highest = await session.scalar(
            select(func.max(Generation.version_no)).where(Generation.project_id == ctx.project_id)
        )
        snapshot = snapshot_json(brief_from_project(ctx.project))
        generation = Generation(
            id=str(uuid4()),
            project_id=ctx.project_id,
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
        await session.flush()
    return job, generation


def _schedule(app, job_id: str, generation_id: str, prompt_artifact_id: str, prompt_digest: str, snapshot_digest: str) -> None:
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


async def _still_current(app, job_id: str, prompt_artifact_id: str, prompt_digest: str, snapshot_digest: str) -> bool:
    ctx = await state.load_context(app, (await _job_project(app, job_id)) or "")
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
    if approval.requirement_snapshot_digest != snapshot_digest:
        return False
    return True


async def _job_project(app, job_id: str) -> str | None:
    async with read_session(app.state.session_factory) as session:
        job = await session.get(GenerationJob, job_id)
        return job.project_id if job is not None else None


async def _run_job(app, job_id: str, generation_id: str, prompt_artifact_id: str, prompt_digest: str, snapshot_digest: str) -> None:
    try:
        if not await _still_current(app, job_id, prompt_artifact_id, prompt_digest, snapshot_digest):
            await _mark_job(
                app.state.session_factory,
                job_id,
                status="failed",
                error_code="SUPERSEDED",
                error_message="需求或执行提示词已更新，旧生成任务未提交。",
            )
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
        if not await _still_current(app, job_id, prompt_artifact_id, prompt_digest, snapshot_digest):
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
            return

        async with read_session(app.state.session_factory) as session:
            generation = await session.get(Generation, generation_id)
        if generation is None:
            await _mark_job(app.state.session_factory, job_id, status="unknown", error_code="VISUAL_OUTCOME_UNKNOWN", error_message="生图结果未确认")
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
                    await _mark_job(app.state.session_factory, job_id, status="failed", error_code="RESULT_FILE_MISSING", error_message="结果图片文件不可用，未发布。")
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
                return

        status = generation.status if generation.status in {"failed", "unknown"} else "unknown"
        await _mark_job(
            app.state.session_factory,
            job_id,
            status=status,
            error_code=generation.error_code or ("VISUAL_TASK_FAILED" if status == "failed" else "VISUAL_OUTCOME_UNKNOWN"),
            error_message=generation.error_message or ("生成任务失败" if status == "failed" else "生图结果未确认"),
            remote_task_id=generation.remote_task_id,
            submit_attempted=1,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        await _mark_job(
            app.state.session_factory,
            job_id,
            status="unknown",
            error_code="VISUAL_OUTCOME_UNKNOWN",
            error_message="生图结果未确认",
            submit_attempted=1,
        )


async def _submit(ctx: WorkflowContext) -> None:
    if ctx.run is None or ctx.run.stage != STAGE or ctx.run.status != "active":
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    final_artifact, brief = await _require_confirmed(ctx)
    prompt_artifact, payload = await _load_prompt(ctx, brief)

    status = build_config_status(ctx.settings, ctx.registry)
    if not status.generation.allowed:
        raise _capability_error(status)

    await _check_reference(ctx, payload)
    existing = await _existing_job(ctx, prompt_artifact.id)
    if existing is not None:
        return

    request_id = str(
        uuid5(
            NAMESPACE_URL,
            f"v2-generation:{ctx.project_id}:{ctx.run_id}:{prompt_artifact.id}:{final_artifact.digest}",
        )
    )
    input_digest = digest_payload(
        {
            "final_brief": brief,
            "prompt": payload,
            "image_size": payload["size"],
            "reference_asset_id": payload.get("reference_asset_id"),
        }
    )
    try:
        job, generation = await _create_job(
            ctx,
            prompt_artifact=prompt_artifact,
            payload=payload,
            brief=brief,
            request_id=request_id,
            input_digest=input_digest,
            snapshot_digest=final_artifact.digest,
        )
    except IntegrityError:
        existing = await _existing_job(ctx, prompt_artifact.id)
        if existing is not None:
            return
        raise
    _schedule(ctx.app, job.id, generation.id, prompt_artifact.id, prompt_artifact.digest, final_artifact.digest)


async def handle(ctx: WorkflowContext, action: str, payload: dict[str, Any]) -> None:
    if action not in ACTIONS:
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    await _submit(ctx)


__all__ = ["STAGE", "ACTIONS", "available", "handle"]

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI
from sqlalchemy import select

from app import recovery_states
from app.db import write_transaction
from app.models import Generation, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow.tables import DiagnosticEvent, GenerationJob, RevisionBudget, WorkflowRun

logger = logging.getLogger("app.recovery")

_INTERRUPTED_TEMPLATE = "PROCESS_INTERRUPTED"
_UNKNOWN_TEMPLATE = "GENERATION_UNKNOWN"
_OPEN_RUN_STATUSES = ("active", "needs_input", "blocked")
_OPEN_JOB_STATUSES = (
    "created",
    "submit_pending",
    "submitted",
    "remote_pending",
    "result_fetched",
)


def _checkpoint(detail_json: str | None) -> dict[str, Any] | None:
    if not detail_json:
        return None
    try:
        value = json.loads(detail_json)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def _latest_checkpoint_events(
    session, project_id: str
) -> dict[str, tuple[DiagnosticEvent, dict[str, Any]]]:
    events = list(
        (
            await session.scalars(
                select(DiagnosticEvent)
                .where(
                    DiagnosticEvent.project_id == project_id,
                    DiagnosticEvent.event == "recovery_checkpoint",
                )
                .order_by(DiagnosticEvent.created_at.desc())
                .limit(128)
            )
        ).all()
    )
    latest: dict[str, tuple[DiagnosticEvent, dict[str, Any]]] = {}
    for event in events:
        detail = _checkpoint(event.detail_json)
        if detail is None:
            continue
        run_id = detail.get("run_id")
        if isinstance(run_id, str) and run_id not in latest:
            latest[run_id] = (event, detail)
    return latest


async def _repair_role_calls(session, now: str) -> int:
    repaired = 0
    runs = list(
        (
            await session.scalars(
                select(WorkflowRun).where(WorkflowRun.status.in_(_OPEN_RUN_STATUSES))
            )
        ).all()
    )
    for run in runs:
        latest = (await _latest_checkpoint_events(session, run.project_id)).get(run.id)
        if latest is None:
            continue
        event, detail = latest
        status = detail.get("status")
        if status == "started":
            detail = dict(detail)
            detail["status"] = "error"
            detail["error_code"] = "PROCESS_INTERRUPTED"
            detail["interrupted_at"] = now
            event.detail_json = json.dumps(detail, ensure_ascii=False, sort_keys=True)
            repaired += 1
        elif status not in {"error", "timeout", "invalid", "capability"}:
            continue
        if run.status != "failed":
            run.status = "failed"
            run.issue_code = "PROCESS_INTERRUPTED"
            run.template_id = _INTERRUPTED_TEMPLATE
            run.issue_message = None
            run.updated_at = now
    return repaired


async def _repair_generation_jobs(session, now: str) -> int:
    repaired = 0
    jobs = list(
        (
            await session.scalars(
                select(GenerationJob).where(
                    GenerationJob.status.in_(_OPEN_JOB_STATUSES)
                )
            )
        ).all()
    )
    affected_runs: dict[str, str] = {}
    for job in jobs:
        generation = (
            await session.get(Generation, job.generation_id)
            if job.generation_id
            else None
        )
        if job.status == "result_fetched" and job.result_asset_id:
            # The image is already local and must stay quarantined for manual
            # review. Startup never reviews it and never expires or rewrites it.
            affected_runs[job.run_id] = "S09"
            continue
        if generation is not None and generation.result_asset_id:
            # Crash between provider success and the job bind: keep the exact
            # local result as pending review, not as a new generation.
            job.status = "result_fetched"
            job.result_asset_id = generation.result_asset_id
            job.error_code = None
            job.error_message = None
            job.updated_at = now
            affected_runs[job.run_id] = "S09"
            repaired += 1
            continue
        from app.final_confirmation import FinalAuthorization

        authorization = await session.scalar(
            select(FinalAuthorization).where(FinalAuthorization.job_id == job.id)
        )
        facts = recovery_states.facts_from_rows(
            job,
            generation,
            authorization,
            submission_audit=await recovery_states.has_submission_audit(
                session, job.id
            ),
        )
        # Job.submit_attempted, the durable submit audit trace and any remote
        # evidence all count. Unknown outcomes are never rewritten as
        # "not submitted": that would invite a second provider submit.
        uncertain = recovery_states.classify(facts) in {
            recovery_states.SUBMITTED,
            recovery_states.UNKNOWN,
        }
        if uncertain:
            job.status = "unknown"
            job.error_code = "VISUAL_OUTCOME_UNKNOWN"
            job.error_message = "上次生成结果尚未确认，可能已经计费。"
            job.updated_at = now
            if generation is not None and generation.status in {"queued", "running"}:
                generation.status = "unknown"
                generation.error_code = "VISUAL_OUTCOME_UNKNOWN"
                generation.error_message = "生图结果未确认"
                generation.completed_at = now
            affected_runs[job.run_id] = "unknown"
        else:
            # No submit attempt reached the provider boundary. Record only the
            # interruption; never call this a cancelled or submitted task.
            job.status = "failed"
            job.error_code = "PROCESS_INTERRUPTED"
            job.error_message = "生成提交尚未发出，流程因服务中断暂停。"
            job.updated_at = now
            if generation is not None and generation.status == "queued":
                generation.status = "failed"
                generation.error_code = "PROCESS_INTERRUPTED"
                generation.error_message = "生成提交尚未发出，流程因服务中断暂停。"
                generation.completed_at = now
            affected_runs[job.run_id] = "not_submitted"
        repaired += 1

    for run_id, state in affected_runs.items():
        run = await session.get(WorkflowRun, run_id)
        if run is None or run.status in {"completed", "failed"}:
            continue
        if state == "S09":
            if run.stage == "S08":
                run.stage = "S09"
                run.status = "active"
                run.issue_code = None
                run.template_id = None
                run.issue_message = None
                run.updated_at = now
            continue
        if run.stage != "S08":
            continue
        run.status = "blocked"
        if state == "unknown":
            run.issue_code = "GENERATION_UNKNOWN"
            run.template_id = _UNKNOWN_TEMPLATE
        else:
            run.issue_code = "PROCESS_INTERRUPTED"
            run.template_id = _INTERRUPTED_TEMPLATE
        run.issue_message = None
        run.updated_at = now
    return repaired


async def _repair_prompt_preparation(session, now: str) -> int:
    """Close an S07 run whose prompt-candidate budget was already exhausted.

    The compile driver runs in-process. If it stops after the third invalid
    candidate without persisting a terminal checkpoint, the run can otherwise
    stay "active" forever and the UI keeps showing the preparation spinner.
    Startup has no live compile task, so this exact active+exhausted shape is
    stale and safe to make recoverable without calling a model.
    """
    rows = (
        await session.execute(
            select(WorkflowRun, RevisionBudget)
            .join(RevisionBudget, RevisionBudget.run_id == WorkflowRun.id)
            .where(
                WorkflowRun.stage == "S07",
                WorkflowRun.status == "active",
                RevisionBudget.artifact_kind == "prompt_plan",
                RevisionBudget.used >= RevisionBudget.max_allowed,
            )
        )
    ).all()
    for run, _budget in rows:
        run.status = "blocked"
        run.issue_code = "REVISION_BUDGET_EXHAUSTED"
        run.template_id = "REVISION_BUDGET_EXHAUSTED"
        run.issue_message = None
        run.updated_at = now
    return len(rows)


async def _clear_recovery_claims(session, now: str) -> int:
    runs = list(
        (
            await session.scalars(
                select(WorkflowRun).where(WorkflowRun.recovery_claim_id.is_not(None))
            )
        ).all()
    )
    for run in runs:
        run.recovery_claim_id = None
        run.recovery_claimed_at = None
        run.updated_at = now
    return len(runs)


async def repair_leftover_state(app: FastAPI) -> None:
    """Repair persisted in-flight state before the app accepts requests.

    Every repair is a durable, idempotent write. Startup never calls a model,
    reviewer, image provider or result fetch, and never re-submits generation.
    Repeated runs are no-ops because repaired rows no longer match the filters.
    """
    session_factory = app.state.session_factory
    now = format_timestamp(utc_now())
    async with write_transaction(session_factory) as session:
        organizing = list(
            (
                await session.scalars(
                    select(Project).where(Project.brief_status == "organizing")
                )
            ).all()
        )
        for project in organizing:
            project.brief_status = "draft"
            project.organize_operation_id = None
            project.prompt_revision = None
            project.confirmed_revision = None
            project.brief_error_code = "ORGANIZATION_INTERRUPTED"
            project.brief_error_message = "整理被中断，请重新发送消息"
            project.updated_at = now

        interrupted = list(
            (
                await session.scalars(
                    select(Generation).where(Generation.status.in_(("queued", "running")))
                )
            ).all()
        )
        for generation in interrupted:
            # A workflow job is repaired below with its exact submit boundary.
            # Legacy generations keep their historical unknown semantics.
            if generation.workflow_job_id is None:
                generation.status = "unknown"
                generation.error_code = "PROCESS_INTERRUPTED"
                generation.error_message = "生成过程因服务重启中断，可稍后重新生成"
                generation.completed_at = now

        claims = await _clear_recovery_claims(session, now)
        role_calls = await _repair_role_calls(session, now)
        jobs = await _repair_generation_jobs(session, now)
        prompts = await _repair_prompt_preparation(session, now)

    if organizing or interrupted or claims or role_calls or jobs or prompts:
        logger.info(
            "repaired leftover state: "
            "organizing=%s generations=%s claims=%s role_calls=%s jobs=%s prompts=%s",
            len(organizing),
            len(interrupted),
            role_calls,
            jobs,
            prompts,
        )

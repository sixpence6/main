"""Four-state recovery facts for one generation attempt.

The classifier is intentionally conservative. Once any submission evidence
exists the state never falls back to a state that could consume a final
authorization again. In particular, an attempt with no observable outcome is
unknown and stays unknown; recovery may verify or retrieve the existing
attempt but must never submit it again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuditEvent
from app.errors import ApiError, conflict
from app.models import Generation
from app.workflow.tables import GenerationJob

WAITING = "waiting"
AUTHORIZED_NOT_CALLED = "authorized_not_called"
SUBMITTED = "submitted"
UNKNOWN = "unknown"

RECOVERY_STATES = (WAITING, AUTHORIZED_NOT_CALLED, SUBMITTED, UNKNOWN)

_ALLOWED_ACTIONS: dict[str, tuple[str, ...]] = {
    WAITING: ("continue_preparation",),
    AUTHORIZED_NOT_CALLED: ("consume_authorization",),
    SUBMITTED: ("verify_submission", "retrieve_result"),
    UNKNOWN: ("verify_submission",),
}

# Durable audit marker written in the same transaction as the submit marker.
SUBMISSION_AUDIT_EVENT = "generation.submit_started"

# Job statuses that mean the provider boundary was already crossed locally.
_DISPATCHED_JOB_STATUSES = frozenset({"submitted", "remote_pending"})
# Generation statuses that prove a provider attempt had started.
_ATTEMPT_GENERATION_STATUSES = frozenset({"running", "succeeded", "unknown"})


@dataclass(frozen=True)
class RecoveryFacts:
    has_authorization: bool = False
    submit_attempted: int = 0
    remote_task_id: str | None = None
    result_locator: str | None = None
    result_asset_id: str | None = None
    submission_audit: bool = False
    response_lost: bool = False
    corrupt: bool = False
    job_status: str | None = None
    generation_status: str | None = None


@dataclass(frozen=True)
class RecoveryRecord:
    """Persisted rows plus the derived recovery decision."""

    job: GenerationJob
    generation: Generation
    authorization: Any | None
    facts: RecoveryFacts
    state: str


def classify(facts: RecoveryFacts) -> str:
    """Return the most conservative state supported by the persisted facts."""
    if facts.corrupt or facts.response_lost:
        return UNKNOWN
    if facts.remote_task_id or facts.result_locator or facts.result_asset_id:
        return SUBMITTED
    if (
        facts.submit_attempted > 0
        or facts.submission_audit
        or facts.job_status in _DISPATCHED_JOB_STATUSES
        or facts.generation_status in _ATTEMPT_GENERATION_STATUSES
    ):
        return UNKNOWN
    if facts.has_authorization:
        return AUTHORIZED_NOT_CALLED
    return WAITING


def allowed_actions(state: str) -> tuple[str, ...]:
    actions = _ALLOWED_ACTIONS.get(state)
    if actions is None:
        raise ApiError(409, "RECOVERY_STATE_INVALID", "恢复状态不可用。")
    return actions


def can_consume_authorization(facts: RecoveryFacts) -> bool:
    """Only an authorized attempt with zero submission evidence may consume."""
    return facts.has_authorization and classify(facts) == AUTHORIZED_NOT_CALLED


def require_action(state: str, action: str) -> None:
    if action not in allowed_actions(state):
        raise conflict(
            "RECOVERY_ACTION_NOT_ALLOWED",
            f"当前恢复状态 {state} 不允许执行动作 {action}。",
        )


def require_legacy_authorization(authorization: Any | None) -> Any:
    """Legacy execution/publish is closed when the durable authorization is missing.

    The caller must pass the actual FinalAuthorization row. A missing row is a
    hard conflict, never a synthetic authorization or a backfill opportunity.
    """
    if authorization is None:
        from app.prompt_preparation import final_confirmation_required

        raise final_confirmation_required()
    return authorization


def _result_locator_is_valid(value: str | None) -> bool:
    if not value:
        return True
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, (dict, str))


def facts_from_rows(
    job: GenerationJob,
    generation: Generation | None,
    authorization: Any | None,
    *,
    submission_audit: bool = False,
) -> RecoveryFacts:
    """Load recovery facts from Generation/GenerationJob/FinalAuthorization rows."""
    if generation is None:
        return RecoveryFacts(
            has_authorization=authorization is not None,
            submit_attempted=1 if (job.submit_attempted or 0) > 0 else 0,
            remote_task_id=job.remote_task_id,
            result_asset_id=job.result_asset_id,
            submission_audit=submission_audit,
            job_status=job.status,
        )

    corrupt = False
    if job.generation_id != generation.id:
        corrupt = True
    if generation.workflow_job_id and generation.workflow_job_id != job.id:
        corrupt = True
    if authorization is not None and (
        authorization.job_id != job.id or authorization.generation_id != generation.id
    ):
        corrupt = True
    if not _result_locator_is_valid(generation.result_locator_json):
        corrupt = True

    remote_task_id = generation.remote_task_id or job.remote_task_id
    return RecoveryFacts(
        has_authorization=authorization is not None,
        submit_attempted=max(job.submit_attempted or 0, generation.submit_attempted or 0),
        remote_task_id=remote_task_id,
        result_locator=generation.result_locator_json,
        result_asset_id=generation.result_asset_id or job.result_asset_id,
        submission_audit=submission_audit,
        response_lost=(
            generation.status == "unknown"
            and not remote_task_id
            and not generation.result_locator_json
            and not generation.result_asset_id
        ),
        corrupt=corrupt,
        job_status=job.status,
        generation_status=generation.status,
    )


async def has_submission_audit(session: AsyncSession, job_id: str) -> bool:
    """Whether a durable submit-boundary audit row exists for this job."""
    found = await session.scalar(
        select(AuditEvent.id)
        .where(
            AuditEvent.event_type == SUBMISSION_AUDIT_EVENT,
            AuditEvent.target_kind == "generation_job",
            AuditEvent.target_id == job_id,
        )
        .limit(1)
    )
    return found is not None


async def load_recovery_record(
    session: AsyncSession, job_id: str
) -> RecoveryRecord:
    """Read the job, generation and durable authorization and classify them."""
    job = await session.get(GenerationJob, job_id)
    if job is None or job.generation_id is None:
        raise ApiError(409, "RECOVERY_NOT_AVAILABLE", "没有可恢复的原生成任务。")
    generation = await session.get(Generation, job.generation_id)
    if generation is None:
        raise ApiError(409, "RECOVERY_NOT_AVAILABLE", "原生成任务记录不可用。")

    # Local import keeps final_confirmation -> generation_guard -> app modules
    # free of an import cycle at module load time.
    from app.final_confirmation import FinalAuthorization

    authorization = await session.scalar(
        select(FinalAuthorization).where(FinalAuthorization.job_id == job.id)
    )
    facts = facts_from_rows(
        job,
        generation,
        authorization,
        submission_audit=await has_submission_audit(session, job.id),
    )
    return RecoveryRecord(
        job=job,
        generation=generation,
        authorization=authorization,
        facts=facts,
        state=classify(facts),
    )


__all__ = [
    "AUTHORIZED_NOT_CALLED",
    "RECOVERY_STATES",
    "SUBMISSION_AUDIT_EVENT",
    "RecoveryFacts",
    "RecoveryRecord",
    "SUBMITTED",
    "UNKNOWN",
    "WAITING",
    "allowed_actions",
    "can_consume_authorization",
    "classify",
    "facts_from_rows",
    "has_submission_audit",
    "load_recovery_record",
    "require_action",
    "require_legacy_authorization",
]

"""Explicit final authorization and one frozen image attempt, never new acceptance."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import ForeignKey, String, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from app.acceptance import GenerationIntent, GenerationSlot
from app.auth.audit import write_audit
from app.auth.models import AuthSession
from app.db import read_session, write_transaction
from app.errors import conflict, validation_error
from app.generation_guard import generation_authorization_ref, job_authorization_ref
from app.models import Base, Generation, Project
from app.ownership import require_owned_project
from app.prompt_preparation import PreparedPrompt, final_confirmation_required, validated_request
from app.timeutil import format_timestamp
from app.workflow.contracts import digest_payload
from app.workflow.tables import Approval, Artifact, GenerationJob, WorkflowRun


class FinalAuthorization(Base):
    __tablename__ = "final_authorizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    intent_id: Mapped[str] = mapped_column(
        ForeignKey("generation_intents.id"), nullable=False, unique=True
    )
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("prepared_prompts.id"), nullable=False, unique=True
    )
    job_id: Mapped[str] = mapped_column(
        ForeignKey("generation_jobs.id"), nullable=False, unique=True
    )
    generation_id: Mapped[str] = mapped_column(
        ForeignKey("generations.id"), nullable=False, unique=True
    )
    owner_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    operator_account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    command_id: Mapped[str] = mapped_column(String(36), nullable=False)
    command_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "command_id", name="uq_final_command"),)


class FinalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: UUID
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    command_id: UUID
    command_context: str = Field(pattern=r"^[0-9a-f]{64}$")


async def command_context(ctx, snapshot, session):
    if ctx.principal is None:
        return None
    login = await session.get(AuthSession, ctx.principal.session_id)
    if (
        login is None
        or login.revoked_at is not None
        or login.account_id != ctx.principal.account_id
    ):
        return None
    material = (
        f"final-prompt-v1:{login.id}:{login.account_epoch}:{snapshot.id}:{snapshot.request_digest}"
    )
    return hmac.new(login.csrf_digest.encode(), material.encode(), hashlib.sha256).hexdigest()


def frozen_brief(request):
    brief = request["requirements"]
    return {
        "product_description": brief["product_description"],
        "purpose": brief["purpose"],
        "style": "、".join(brief["style_keywords"]),
        "composition": brief["composition"],
        "text_content": "、".join(brief["texts"]),
        "prompt": request["prompt"],
        "image_size": request["output"]["size"],
        "reference_asset_id": request["references"][0]["asset_id"]
        if request["references"]
        else None,
    }


async def require_authorized_attempt(session, generation_id):
    """Durable binding, not the old synthetic authorization_ref. No legacy backfill."""
    authorization = await session.scalar(
        select(FinalAuthorization).where(FinalAuthorization.generation_id == generation_id)
    )
    if authorization is None:
        raise final_confirmation_required()
    generation = await session.get(Generation, authorization.generation_id)
    job = await session.get(GenerationJob, authorization.job_id)
    snapshot = await session.get(PreparedPrompt, authorization.snapshot_id)
    if (
        generation is None
        or job is None
        or snapshot is None
        or snapshot.status != "authorized"
        or snapshot.project_id != authorization.project_id
        or snapshot.intent_id != authorization.intent_id
        or snapshot.run_id != authorization.run_id
        or snapshot.request_digest != authorization.snapshot_digest
        or generation.workflow_job_id != job.id
        or job.generation_id != generation.id
        or generation.intent_id != authorization.intent_id
        or job.intent_id != authorization.intent_id
        or generation.project_id != authorization.project_id
        or job.project_id != authorization.project_id
        or job.run_id != authorization.run_id
        or generation.owner_account_id != authorization.owner_account_id
        or job.owner_account_id != authorization.owner_account_id
        or generation.operator_account_id != authorization.operator_account_id
        or job.operator_account_id != authorization.operator_account_id
    ):
        raise final_confirmation_required()
    request = json.loads(snapshot.request_json)
    canonical = json.loads(generation.request_canonical)
    if (
        digest_payload(request) != snapshot.request_digest
        or canonical.get("request") != request
        or canonical.get("authorization_id") != authorization.id
        or digest_payload(canonical) != generation.request_digest
        or generation.request_digest != job.request_digest
        or generation.prompt != request["prompt"]
        or json.loads(generation.brief_snapshot_json) != frozen_brief(request)
        or generation.image_size != request["output"]["size"]
        or generation.reference_asset_id != frozen_brief(request)["reference_asset_id"]
        or generation.adapter_id != request["service"]["adapter_id"]
        or generation.model != request["service"]["model"]
        or job.prompt_artifact_id != snapshot.artifact_id
        or job.confirmed_snapshot_digest != request["requirements_digest"]
    ):
        raise final_confirmation_required()
    from app.prompt_preparation import historical_prompt_readable

    artifact = await session.get(Artifact, snapshot.artifact_id)
    confirmation = await session.get(Approval, request["requirements_approval_id"])
    project = await session.get(Project, authorization.project_id)
    run = await session.get(WorkflowRun, authorization.run_id)
    if (
        not await historical_prompt_readable(session, job, artifact)
        or confirmation is None
        or confirmation.revoked_at is not None
        or confirmation.approved_by != "user"
        or confirmation.approval_type != "requirements_card"
        or confirmation.project_id != authorization.project_id
        or confirmation.artifact_id != request["requirements_artifact_id"]
        or confirmation.artifact_digest != request["requirements_digest"]
        or confirmation.requirement_snapshot_digest != request["requirements_digest"]
        or project is None
        or project.owner_account_id != authorization.owner_account_id
        or project.revision != request["project_revision"]
        or project.confirmed_revision != request["project_revision"]
        or run is None
        or run.requirement_revision != request["requirement_revision"]
        or generation.request_id != job.request_id
        or generation.confirmed_revision != request["project_revision"]
    ):
        raise final_confirmation_required()
    return authorization, snapshot, request


async def require_authorized_job(factory, job_id):
    async with read_session(factory) as session:
        job = await session.get(GenerationJob, job_id)
        if job is None or job.generation_id is None:
            raise final_confirmation_required()
        await require_authorized_attempt(session, job.generation_id)


async def execution_request(app, generation_id):
    from app.workflow.state import load_context

    async with read_session(app.state.session_factory) as session:
        authorization, snapshot, _ = await require_authorized_attempt(session, generation_id)
        ctx = await load_context(app, authorization.project_id)
        if ctx.run is None or ctx.run.id != authorization.run_id:
            raise final_confirmation_required()
        return await validated_request(ctx, session, snapshot, authorized=True)


async def confirm_prompt(ctx, payload):
    try:
        command = FinalCommand.model_validate(payload).model_dump(mode="json")
    except ValidationError:
        raise validation_error("请提供当前登录上下文、提示词快照及稳定最终命令 ID。") from None
    if ctx.principal is None:
        raise final_confirmation_required()
    command_digest = digest_payload(
        {"domain": "final-prompt-v1", "revision": ctx.revision, **command}
    )
    created = False
    async with write_transaction(ctx.session_factory) as session:
        project = await require_owned_project(session, ctx.project_id, ctx.principal)
        login = await session.get(AuthSession, ctx.principal.session_id)
        now = ctx.app.state.clock.now()
        if login is None or any(
            datetime.fromisoformat(value.replace("Z", "+00:00")) <= now
            for value in (login.idle_expires_at, login.absolute_expires_at)
        ):
            raise final_confirmation_required()
        snapshot = await session.get(PreparedPrompt, command["snapshot_id"])
        if (
            snapshot is None
            or snapshot.project_id != project.id
            or ctx.run is None
            or snapshot.run_id != ctx.run_id
            or snapshot.status not in {"awaiting_confirmation", "authorized"}
            or snapshot.request_digest != command["snapshot_digest"]
        ):
            raise conflict("PROMPT_STALE", "当前提示词快照不可用于最终确认。")
        expected_context = await command_context(ctx, snapshot, session)
        if expected_context is None or not hmac.compare_digest(
            expected_context, command["command_context"]
        ):
            raise conflict("FINAL_CONTEXT_CHANGED", "登录上下文已变化，请重新读取并明确确认。")
        existing_command = await session.scalar(
            select(FinalAuthorization).where(
                FinalAuthorization.project_id == project.id,
                FinalAuthorization.command_id == command["command_id"],
            )
        )
        if existing_command is not None and existing_command.command_digest != command_digest:
            raise conflict("FINAL_COMMAND_CONFLICT", "最终命令 ID 已用于不同的参数。")
        request = await validated_request(
            ctx, session, snapshot, authorized=snapshot.status == "authorized"
        )
        if project.revision != ctx.revision:
            raise conflict("REVISION_CONFLICT", "项目已变化，请刷新后重试。")
        existing = await session.scalar(
            select(FinalAuthorization).where(FinalAuthorization.snapshot_id == snapshot.id)
        )
        if existing is not None:
            if (
                existing_command is None
                or existing.id != existing_command.id
                or existing.session_id != login.id
            ):
                raise conflict("FINAL_ALREADY_AUTHORIZED", "该快照已有唯一执行任务，不可重复提交。")
            await require_authorized_attempt(session, existing.generation_id)
            job = await session.get(GenerationJob, existing.job_id)
            generation = await session.get(Generation, existing.generation_id)
        else:
            intent = await session.get(GenerationIntent, snapshot.intent_id)
            run = await session.get(WorkflowRun, snapshot.run_id)
            slots = (
                await session.scalars(
                    select(GenerationSlot).where(
                        GenerationSlot.intent_id == snapshot.intent_id,
                        GenerationSlot.released_at.is_(None),
                    )
                )
            ).all()
            scopes = {(slot.scope, slot.scope_id) for slot in slots}
            if (
                intent is None
                or intent.status != "accepted"
                or snapshot.status != "awaiting_confirmation"
                or run is None
                or run.stage != "S07"
                or run.status != "needs_input"
                or len(slots) != 3
                or scopes
                != {
                    ("personal", intent.operator_account_id),
                    ("instance", "instance"),
                    ("project", project.id),
                }
            ):
                raise conflict("INTENT_STATE_INVALID", "原准备意图或占用不可用于最终执行。")
            occupied = await session.scalar(
                select(GenerationJob.id).where(GenerationJob.intent_id == intent.id)
            )
            if occupied is not None:
                raise final_confirmation_required()
            authorization_id = str(uuid4())
            request_id = str(uuid4())
            canonical = {
                "authorization_id": authorization_id,
                "snapshot_id": snapshot.id,
                "request": request,
            }
            canonical_digest = digest_payload(canonical)
            highest = await session.scalar(
                select(func.max(Generation.version_no)).where(Generation.project_id == project.id)
            )
            timestamp = format_timestamp(now)
            generation = Generation(
                id=str(uuid4()),
                project_id=project.id,
                owner_account_id=intent.owner_account_id,
                operator_account_id=ctx.principal.account_id,
                intent_id=intent.id,
                version_no=(highest or 0) + 1,
                request_id=request_id,
                request_canonical=json.dumps(
                    canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                request_digest=canonical_digest,
                confirmed_revision=request["project_revision"],
                authorization_ref=generation_authorization_ref(
                    project_id=project.id,
                    request_id=request_id,
                    request_digest=canonical_digest,
                    confirmed_revision=request["project_revision"],
                ),
                status="queued",
                stage="waiting_slot",
                brief_snapshot_json=json.dumps(frozen_brief(request), ensure_ascii=False),
                prompt=request["prompt"],
                image_size=request["output"]["size"],
                reference_asset_id=frozen_brief(request)["reference_asset_id"],
                adapter_id=request["service"]["adapter_id"],
                model=request["service"]["model"],
                service_fingerprint=request["service"]["fingerprint"],
                submit_attempted=0,
                created_at=timestamp,
            )
            job = GenerationJob(
                id=str(uuid4()),
                project_id=project.id,
                run_id=run.id,
                owner_account_id=intent.owner_account_id,
                operator_account_id=ctx.principal.account_id,
                intent_id=intent.id,
                request_id=request_id,
                request_digest=canonical_digest,
                authorization_ref=job_authorization_ref(
                    project_id=project.id,
                    run_id=run.id,
                    request_id=request_id,
                    request_digest=canonical_digest,
                    confirmed_snapshot_digest=request["requirements_digest"],
                ),
                prompt_artifact_id=snapshot.artifact_id,
                confirmed_snapshot_digest=request["requirements_digest"],
                status="submit_pending",
                submit_attempted=0,
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add_all([generation, job])
            await session.flush()
            job.generation_id = generation.id
            generation.workflow_job_id = job.id
            session.add(
                FinalAuthorization(
                    id=authorization_id,
                    project_id=project.id,
                    run_id=run.id,
                    intent_id=intent.id,
                    snapshot_id=snapshot.id,
                    job_id=job.id,
                    generation_id=generation.id,
                    owner_account_id=intent.owner_account_id,
                    operator_account_id=ctx.principal.account_id,
                    session_id=login.id,
                    command_id=command["command_id"],
                    command_digest=command_digest,
                    snapshot_digest=snapshot.request_digest,
                    created_at=timestamp,
                )
            )
            snapshot.status = "authorized"
            intent.status = "executing"
            intent.updated_at = timestamp
            run.stage = "S08"
            run.status = "active"
            run.updated_at = timestamp
            write_audit(
                session,
                event_type="prompt.final_authorized",
                result="success",
                now=now,
                actor_kind="account",
                actor_account_id=ctx.principal.account_id,
                target_kind="generation_job",
                target_id=job.id,
                after={
                    "authorization_id": authorization_id,
                    "snapshot_id": snapshot.id,
                    "snapshot_digest": snapshot.request_digest,
                    "generation_id": generation.id,
                },
            )
            await session.flush()
            created = True
    if created:
        from app.workflow.stages.s08 import _schedule

        _schedule(
            ctx.app,
            job.id,
            generation.id,
            snapshot.artifact_id,
            request["prompt_artifact_digest"],
            request["requirements_digest"],
        )

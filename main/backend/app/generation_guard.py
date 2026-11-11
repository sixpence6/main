from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.models import Account
from app.db import read_session
from app.errors import ApiError
from app.models import Generation, Project
from app.workflow.tables import GenerationJob


class GenerationGuardError(Exception):
    """A persisted task can no longer act for its original owner."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class JobAuthorization:
    job_id: str
    project_id: str
    owner_account_id: str
    operator_account_id: str | None


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def job_authorization_ref(
    *,
    project_id: str,
    run_id: str,
    request_id: str,
    request_digest: str,
    confirmed_snapshot_digest: str,
) -> str:
    return _digest(
        {
            "kind": "generation-job-v1",
            "project_id": project_id,
            "run_id": run_id,
            "request_id": request_id,
            "request_digest": request_digest,
            "confirmed_snapshot_digest": confirmed_snapshot_digest,
        }
    )


def generation_authorization_ref(
    *, project_id: str, request_id: str, request_digest: str, confirmed_revision: int
) -> str:
    return _digest(
        {
            "kind": "generation-v1",
            "project_id": project_id,
            "request_id": request_id,
            "request_digest": request_digest,
            "confirmed_revision": confirmed_revision,
        }
    )


def _account_unavailable(account: Account | None) -> GenerationGuardError:
    if account is not None and account.is_disabled:
        return GenerationGuardError("ACCOUNT_DISABLED", "账号已停用，后台任务已安全停止。")
    return GenerationGuardError("ACCOUNT_INACTIVE", "账号当前不可用，后台任务已安全停止。")


async def _active_account(session: AsyncSession, account_id: str | None) -> Account:
    if not account_id:
        raise GenerationGuardError("OWNER_MISSING", "后台任务缺少持久归属，已拒绝继续。")
    account = await session.get(Account, account_id)
    if account is None or not account.can_login:
        raise _account_unavailable(account)
    return account


async def ensure_job_active_in_session(
    session: AsyncSession, job_id: str
) -> tuple[GenerationJob, JobAuthorization]:
    job = await session.get(GenerationJob, job_id)
    if job is None:
        raise GenerationGuardError("JOB_NOT_FOUND", "后台任务不存在。")
    project = await session.get(Project, job.project_id)
    if project is None:
        raise GenerationGuardError("OWNER_MISSING", "后台任务缺少项目归属，已拒绝继续。")
    owner_id = job.owner_account_id or project.owner_account_id
    if not owner_id or (
        project.owner_account_id is not None and project.owner_account_id != owner_id
    ):
        raise GenerationGuardError("OWNERSHIP_MISMATCH", "后台任务归属不一致，已拒绝继续。")
    await _active_account(session, owner_id)
    if job.operator_account_id is not None:
        await _active_account(session, job.operator_account_id)
    if job.authorization_ref is not None:
        expected = job_authorization_ref(
            project_id=job.project_id,
            run_id=job.run_id,
            request_id=job.request_id,
            request_digest=job.request_digest,
            confirmed_snapshot_digest=job.confirmed_snapshot_digest,
        )
        if job.authorization_ref != expected:
            raise GenerationGuardError(
                "AUTHORIZATION_INVALID", "后台任务授权关联不一致，已拒绝继续。"
            )
    from app.final_confirmation import FinalAuthorization, require_authorized_attempt
    if await session.scalar(select(FinalAuthorization.id).where(
            FinalAuthorization.job_id == job.id)) is not None:
        try:
            await require_authorized_attempt(session, job.generation_id)
        except ApiError as exc:
            raise GenerationGuardError(exc.code, exc.message) from None
    return job, JobAuthorization(job.id, job.project_id, owner_id, job.operator_account_id)


async def ensure_job_active(
    factory: async_sessionmaker[AsyncSession], job_id: str
) -> JobAuthorization:
    async with read_session(factory) as session:
        _job, authorization = await ensure_job_active_in_session(session, job_id)
        return authorization


async def ensure_generation_active_in_session(
    session: AsyncSession, generation_id: str
) -> tuple[Generation, JobAuthorization]:
    generation = await session.get(Generation, generation_id)
    if generation is None:
        raise GenerationGuardError("JOB_NOT_FOUND", "生成任务不存在。")
    project = await session.get(Project, generation.project_id)
    if project is None:
        raise GenerationGuardError("OWNER_MISSING", "生成任务缺少项目归属，已拒绝继续。")
    owner_id = generation.owner_account_id or project.owner_account_id
    if not owner_id or (
        project.owner_account_id is not None and project.owner_account_id != owner_id
    ):
        raise GenerationGuardError("OWNERSHIP_MISMATCH", "生成任务归属不一致，已拒绝继续。")
    await _active_account(session, owner_id)
    if generation.operator_account_id is not None:
        await _active_account(session, generation.operator_account_id)
    if generation.authorization_ref is not None:
        expected = generation_authorization_ref(
            project_id=generation.project_id,
            request_id=generation.request_id,
            request_digest=generation.request_digest,
            confirmed_revision=generation.confirmed_revision,
        )
        if generation.authorization_ref != expected:
            raise GenerationGuardError(
                "AUTHORIZATION_INVALID", "生成任务授权关联不一致，已拒绝继续。"
            )
    from app.final_confirmation import FinalAuthorization, require_authorized_attempt
    if await session.scalar(select(FinalAuthorization.id).where(
            FinalAuthorization.generation_id == generation.id)) is not None:
        try:
            await require_authorized_attempt(session, generation.id)
        except ApiError as exc:
            raise GenerationGuardError(exc.code, exc.message) from None
    return generation, JobAuthorization(
        generation.workflow_job_id or generation.id,
        generation.project_id,
        owner_id,
        generation.operator_account_id,
    )


async def ensure_generation_active(
    factory: async_sessionmaker[AsyncSession], generation_id: str
) -> JobAuthorization:
    async with read_session(factory) as session:
        _generation, authorization = await ensure_generation_active_in_session(
            session, generation_id
        )
        return authorization


async def ensure_project_owner_active(
    factory: async_sessionmaker[AsyncSession],
    project_id: str,
    *,
    principal_account_id: str | None = None,
) -> None:
    """Revalidate the business owner before an interactive model call.

    Internal legacy tests may create an unowned project; those rows are never
    servable through the API, but keeping this no-op preserves their fixtures.
    """

    async with read_session(factory) as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise GenerationGuardError("OWNER_MISSING", "项目归属不存在，已拒绝继续。")
        if project.owner_account_id is None:
            return
        await _active_account(session, project.owner_account_id)
        if principal_account_id is not None and principal_account_id != project.owner_account_id:
            raise GenerationGuardError("OWNERSHIP_MISMATCH", "项目归属不一致，已拒绝继续。")


__all__ = [
    "GenerationGuardError",
    "JobAuthorization",
    "ensure_generation_active",
    "ensure_generation_active_in_session",
    "ensure_job_active",
    "ensure_job_active_in_session",
    "ensure_project_owner_active",
    "generation_authorization_ref",
    "job_authorization_ref",
]

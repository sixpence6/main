from __future__ import annotations

import logging

from fastapi import FastAPI
from sqlalchemy import select

from app.db import write_transaction
from app.models import Generation, Project
from app.timeutil import format_timestamp, utc_now

logger = logging.getLogger("app.recovery")


async def repair_leftover_state(app: FastAPI) -> None:
    """Repair persisted in-flight state before the app accepts requests.

    Repeated runs are no-ops: repaired rows no longer match the filters.
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
            generation.status = "unknown"
            generation.error_code = "PROCESS_INTERRUPTED"
            generation.error_message = "生成过程因服务重启中断，可稍后重新生成"
            generation.completed_at = now

    if organizing or interrupted:
        logger.info(
            "repaired leftover state: organizing=%s generations=%s",
            len(organizing),
            len(interrupted),
        )

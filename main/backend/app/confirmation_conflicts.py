"""Map final-authorization uniqueness races to stable conflicts."""

from __future__ import annotations

from sqlalchemy import select

from app.db import read_session
from app.errors import ApiError, conflict

FINAL_COMMAND_CONFLICT = "FINAL_COMMAND_CONFLICT"
FINAL_ALREADY_AUTHORIZED = "FINAL_ALREADY_AUTHORIZED"


def is_unique_violation(error: BaseException) -> bool:
    """Only real uniqueness races map to a conflict; injected storage faults stay failures."""
    message = str(getattr(error, "orig", error)).lower()
    return "unique constraint failed" in message or "uq_final_command" in message


async def resolve_final_integrity_conflict(
    session_factory,
    *,
    project_id: str,
    command_id: str,
    command_digest: str,
    snapshot_id: str,
) -> ApiError:
    """Re-read the durable rows after an IntegrityError and name the real conflict."""
    from app.final_confirmation import FinalAuthorization

    async with read_session(session_factory) as session:
        by_command = await session.scalar(
            select(FinalAuthorization).where(
                FinalAuthorization.project_id == project_id,
                FinalAuthorization.command_id == command_id,
            )
        )
        if by_command is not None:
            if by_command.command_digest != command_digest:
                return conflict(FINAL_COMMAND_CONFLICT, "同一命令 ID 已绑定不同的参数。")
            return conflict(FINAL_ALREADY_AUTHORIZED, "该快照已有唯一执行任务，不能重复提交。")
        by_snapshot = await session.scalar(
            select(FinalAuthorization).where(FinalAuthorization.snapshot_id == snapshot_id)
        )
        if by_snapshot is not None:
            return conflict(FINAL_ALREADY_AUTHORIZED, "该快照已有唯一执行任务，不能重复提交。")
        return conflict(FINAL_COMMAND_CONFLICT, "同一命令 ID 已绑定不同的参数。")

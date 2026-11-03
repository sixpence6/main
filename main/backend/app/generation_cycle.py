"""Background driver for one accepted generation intent.

The HTTP confirmation path only writes durable facts. This module advances that
intent after the transaction commits: S07 prompt compilation + independent
review, then persists a frozen request and waits. It creates no image attempt
and never submits, authorizes execution, approves delivery, or publishes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy import select

from app.acceptance import GenerationIntent
from app.db import read_session
from app.workflow import state
from app.workflow.stages import s07

logger = logging.getLogger(__name__)


def schedule_confirmed_cycle(
    app: Any, project_id: str, run_id: str, intent_id: str
) -> None:
    """Start the post-confirmation driver once per accepted intent."""

    tasks = getattr(app.state, "generation_tasks", None)
    if tasks is None:
        tasks = set()
        app.state.generation_tasks = tasks
    cycles = getattr(app.state, "generation_cycles", None)
    if cycles is None:
        cycles = set()
        app.state.generation_cycles = cycles
    if intent_id in cycles:
        return
    cycles.add(intent_id)
    task = asyncio.create_task(_run_cycle(app, project_id, run_id, intent_id))
    tasks.add(task)

    def _done(finished: asyncio.Task[None]) -> None:
        tasks.discard(finished)
        cycles.discard(intent_id)
        if not finished.cancelled():
            with contextlib.suppress(BaseException):
                finished.exception()

    task.add_done_callback(_done)


async def _intent_state(app: Any, intent_id: str) -> tuple[str, str] | None:
    async with read_session(app.state.session_factory) as session:
        intent = await session.scalar(
            select(GenerationIntent).where(GenerationIntent.id == intent_id)
        )
        if intent is None:
            return None
        return intent.project_id, intent.status


async def _run_cycle(
    app: Any, project_id: str, run_id: str, intent_id: str
) -> None:
    try:
        fact = await _intent_state(app, intent_id)
        if fact is None or fact[0] != project_id:
            return
        if fact[1] not in {"accepted", "executing"}:
            return
        context = await state.load_context(app, project_id)
        if context.run is None or context.run.id != run_id:
            return
        if context.run.stage != "S07" or context.run.status != "active":
            return
        await s07.handle(context, "compile_prompt", {})
        # Independent prompt approval commits the durable waiting state.
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        # The durable workflow state is authoritative. A driver failure must
        # not invent a terminal fact, release usage or trigger a resend.
        logger.error(
            "generation preparation stopped for intent %s (%s)", intent_id, type(exc).__name__
        )


__all__ = ["schedule_confirmed_cycle"]

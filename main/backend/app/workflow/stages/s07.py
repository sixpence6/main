from __future__ import annotations

from typing import Any

from app.errors import ApiError
from app.workflow import templates
from app.workflow.state import WorkflowContext

STAGE = "S07"
ACTIONS = [
    "compile_prompt",
    "revise_prompt"
]


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    return []


async def handle(
    ctx: WorkflowContext, action: str, payload: dict[str, Any]
) -> None:
    raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))

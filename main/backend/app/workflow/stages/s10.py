from __future__ import annotations

from typing import Any

from app.errors import ApiError
from app.workflow import templates
from app.workflow.state import WorkflowContext

STAGE = "S10"
ACTIONS = [
    "prepare_delivery",
    "approve_delivery",
    "publish_delivery",
    "request_new_version"
]


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    return []


async def handle(
    ctx: WorkflowContext, action: str, payload: dict[str, Any]
) -> None:
    raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))

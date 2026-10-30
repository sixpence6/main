from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from app.db import write_transaction
from app.errors import ApiError, conflict, validation_error
from app.models import Message, Project
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates
from app.workflow.roles import RoleOutput, run_role
from app.workflow.state import WorkflowContext, set_intent, set_stage

STAGE = "S01"
ACTIONS = [
    "submit_request",
    "answer_intent_clarification"
]

_ROUTE_ACTIONS = {
    "new_request",
    "edit_request",
    "feedback",
    "confirm_brief",
    "submit_generation",
    "upload_help",
    "status_query",
    "download",
    "none",
}

# The router classifies an intent, but the program owns which action is legal
# for that intent. A model can never turn a knowledge question into a workflow
# action, or attach a confirmation action to an image request.
_PAIR_ACTIONS = {
    "image_request": {"new_request", "edit_request", "feedback"},
    "workflow_action": {
        "confirm_brief",
        "submit_generation",
        "upload_help",
        "status_query",
        "download",
    },
    "out_of_scope": {"none"},
    "mixed": {"none"},
    "unclear": {"none"},
}

# The reviewer must attest each of these program-owned routing rules. A pass
# with a missing, duplicate, extra, failed or unknown check never opens S01.
_REQUIRED_RULES = ("G01_SCOPE", "G01_ACTION")


def available(ctx: WorkflowContext) -> list[dict[str, Any]]:
    if ctx.run is None:
        return ["submit_request"]
    if ctx.run.stage != STAGE:
        return []
    if ctx.run.status in {"needs_input", "blocked"}:
        return ["answer_intent_clarification"]
    return []


def validate_text(payload: dict[str, Any]) -> str:
    value = payload.get("text")
    if not isinstance(value, str) or not value.strip():
        raise validation_error("消息内容不能为空")
    if len(value) > 20_000:
        raise validation_error("消息内容过长")
    return value


async def _append_message(
    ctx: WorkflowContext,
    *,
    role: str,
    content: str,
    revision: int,
) -> Message:
    async with write_transaction(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        if project is None:
            raise conflict("PROJECT_NOT_FOUND", "项目不存在")
        highest = await session.scalar(
            select(func.max(Message.sequence_no)).where(
                Message.project_id == ctx.project_id
            )
        )
        message = Message(
            id=str(uuid4()),
            project_id=ctx.project_id,
            sequence_no=(highest or 0) + 1,
            role=role,
            content=content,
            project_revision=revision,
            brief_snapshot_json=None,
            operation_id=None,
            created_at=format_timestamp(utc_now()),
        )
        session.add(message)
        await session.flush()
    return message


async def _record_user_turn(ctx: WorkflowContext, text: str) -> tuple[Message, int]:
    """Persist the exact user text before any model call.

    The text is never rewritten or trimmed in storage. A revision increment is
    performed under the project lock so an older client cannot append against a
    stale view.
    """
    async with write_transaction(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        if project is None:
            raise conflict("PROJECT_NOT_FOUND", "项目不存在")
        project.revision += 1
        project.updated_at = format_timestamp(utc_now())
        if project.revision == 1 and not project.title:
            project.title = " ".join(text.split())[:32]
        revision = project.revision
        highest = await session.scalar(
            select(func.max(Message.sequence_no)).where(
                Message.project_id == ctx.project_id
            )
        )
        message = Message(
            id=str(uuid4()),
            project_id=ctx.project_id,
            sequence_no=(highest or 0) + 1,
            role="user",
            content=text,
            project_revision=revision,
            brief_snapshot_json=None,
            operation_id=None,
            created_at=format_timestamp(utc_now()),
        )
        session.add(message)
        await session.flush()
    ctx.project.revision = revision
    return message, revision


async def _append_assistant(ctx: WorkflowContext, content: str, revision: int) -> None:
    await _append_message(ctx, role="assistant", content=content, revision=revision)


def _invalid_service() -> ApiError:
    return ApiError(
        503, "SERVICE_UNAVAILABLE", templates.render("SERVICE_UNAVAILABLE")
    )


def _invalid_review() -> ApiError:
    return ApiError(
        503, "REVIEW_UNAVAILABLE", templates.render("REVIEW_UNAVAILABLE")
    )


def _validate_intent(output: RoleOutput, evidence_id: str) -> tuple[str, str]:
    if output.status != "ready" or output.data is None:
        raise _invalid_service()
    route = output.data.get("route")
    action = output.data.get("action")
    evidence = output.data.get("evidence_ids")
    if route not in _PAIR_ACTIONS or action not in _ROUTE_ACTIONS:
        raise _invalid_service()
    if not isinstance(evidence, list) or not evidence:
        raise _invalid_service()
    if any(not isinstance(item, str) or item != evidence_id for item in evidence):
        raise _invalid_service()
    if action not in _PAIR_ACTIONS[str(route)]:
        raise _invalid_service()
    return str(route), str(action)


def _allowed_evidence(value: object, allowed: set[str]) -> bool:
    if not isinstance(value, list) or not value:
        return False
    return all(isinstance(item, str) and item in allowed for item in value)


def _validate_review(output: RoleOutput, target_id: str) -> None:
    if output.status != "ready" or output.data is None:
        raise _invalid_review()
    results = output.data.get("results")
    if not isinstance(results, list) or len(results) != 1:
        raise _invalid_review()
    result = results[0]
    if not isinstance(result, dict) or result.get("target_id") != target_id:
        raise _invalid_review()
    if result.get("decision") != "pass":
        raise _invalid_review()
    checks = result.get("checks")
    if not isinstance(checks, list) or not checks:
        raise _invalid_review()
    allowed = {target_id}
    seen: list[str] = []
    for check in checks:
        if not isinstance(check, dict):
            raise _invalid_review()
        rule_id = check.get("rule_id")
        if rule_id not in _REQUIRED_RULES or check.get("outcome") != "pass":
            raise _invalid_review()
        if not _allowed_evidence(check.get("evidence_ids"), allowed):
            raise _invalid_review()
        note = check.get("note")
        if isinstance(note, str) and "://" in note.lower():
            raise _invalid_review()
        seen.append(str(rule_id))
    if set(seen) != set(_REQUIRED_RULES) or len(seen) != len(set(seen)):
        raise _invalid_review()


async def _route(ctx: WorkflowContext, text: str) -> None:
    user_message, revision = await _record_user_turn(ctx, text)
    evidence_id = user_message.id
    available_actions = (
        ["answer_intent_clarification"]
        if ctx.run is not None and ctx.run.status in {"needs_input", "blocked"}
        else ["submit_request"]
    )
    phase = "intent"
    try:
        intent = await run_role(
            ctx,
            "intent_router",
            STAGE,
            {
                "user_text": text,
                "current_stage": ctx.run.stage if ctx.run else STAGE,
                "current_status": ctx.run.status if ctx.run else "none",
                "reference_asset_id": ctx.project.reference_asset_id,
                "available_actions": available_actions,
            },
            required_rule_ids=_REQUIRED_RULES,
            allowed_evidence_ids=(evidence_id,),
        )
        route, action = _validate_intent(intent, evidence_id)

        phase = "review"
        review = await run_role(
            ctx,
            "output_reviewer",
            STAGE,
            {
                "reviewed_role": "intent_router",
                "target_kind": "intent_route",
            },
            review_targets=[
                {
                    "target_id": evidence_id,
                    "target_kind": "intent_route",
                    "user_text": text,
                    "route": route,
                    "action": action,
                }
            ],
            required_rule_ids=_REQUIRED_RULES,
            allowed_evidence_ids=(evidence_id,),
            attempt=1,
        )
        _validate_review(review, evidence_id)
    except ApiError:
        # Technical failure never turns into a guessed route or an auto retry.
        template_id = "REVIEW_UNAVAILABLE" if phase == "review" else "SERVICE_UNAVAILABLE"
        await set_stage(
            ctx,
            STAGE,
            "failed",
            issue_code=template_id,
            template_id=template_id,
        )
        raise

    await set_intent(ctx, route, action)

    if route in {"out_of_scope", "mixed"}:
        await _append_assistant(ctx, templates.render("SCOPE_REFUSAL"), revision)
        await set_stage(
            ctx,
            STAGE,
            "blocked",
            issue_code="MIXED_SCOPE" if route == "mixed" else "OUT_OF_SCOPE",
            template_id="SCOPE_REFUSAL",
        )
        return

    if route == "unclear":
        await _append_assistant(ctx, templates.render("INTENT_UNCLEAR"), revision)
        await set_stage(
            ctx,
            STAGE,
            "needs_input",
            issue_code="MISSING_INPUT",
            template_id="INTENT_UNCLEAR",
        )
        return

    # S01 only offers the initial request and clarification actions. A
    # downstream workflow action has no valid target here; the fixed
    # clarification prevents a bare "confirm"/"continue" from becoming an
    # authorization or skipping stages.
    if route == "workflow_action":
        await _append_assistant(ctx, templates.render("INTENT_UNCLEAR"), revision)
        await set_stage(
            ctx,
            STAGE,
            "needs_input",
            issue_code="UNAUTHORIZED_ACTION",
            template_id="INTENT_UNCLEAR",
        )
        return

    next_stage = "S02" if ctx.project.reference_asset_id else "S03"
    await set_stage(ctx, next_stage, "active")


async def handle(
    ctx: WorkflowContext, action: str, payload: dict[str, Any]
) -> None:
    if action not in ACTIONS:
        raise ApiError(409, "ACTION_NOT_AVAILABLE", templates.render("ACTION_UNAVAILABLE"))
    text = validate_text(payload)
    await _route(ctx, text)


__all__ = ["STAGE", "ACTIONS", "available", "handle", "validate_text"]

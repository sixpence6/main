from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import (
    BriefData,
    OrganizeImage,
    OrganizeMessage,
    OrganizeRequest,
    OrganizeResult,
)
from app.adapters.registry import AdapterRegistry
from app.auth.principal import Principal
from app.config import Settings
from app.db import ProjectLocks, read_session, write_transaction
from app.errors import ApiError, conflict, validation_error
from app.models import Asset, Generation, Message, Project
from app.ownership import require_project_scope
from app.presenters import brief_from_project, snapshot_json
from app.services.assets import resolve_asset_file
from app.services.projects import DEFAULT_PROJECT_TITLE
from app.timeutil import format_timestamp, utc_now
from app.workflow import templates as workflow_templates
from app.workflow.access import ensure_legacy_entry_available
from app.workflow.tables import WorkflowRun

logger = logging.getLogger("app.messages")

MAX_MESSAGE_CHARS = 20_000
MAX_BRIEF_FIELD_CHARS = 8_000
MAX_PROMPT_CHARS = 32_000
TITLE_SOURCE_CHARS = 32
BRIEF_FIELDS = ("product_description", "purpose", "style", "composition", "text_content")


@dataclass
class MessageExchange:
    project: Project
    user_message: Message
    assistant_message: Message
    reference_asset: Asset | None = None
    workflow: Any | None = None
    kind: str | None = None


def ensure_llm_ready(settings: Settings, registry: AdapterRegistry) -> Any:
    role = settings.llm()
    missing = role.missing_fields()
    if missing:
        logger.info("llm configuration incomplete: %s", ",".join(missing))
        raise ApiError(503, "MODEL_NOT_CONFIGURED", "语言模型尚未配置完整，请检查本地配置")
    if (role.adapter_id or "") not in registry.known_llm_ids():
        raise ApiError(503, "ADAPTER_UNAVAILABLE", "语言模型适配器不可用，请检查本地配置")
    return role


def _collapsed_title(content: str) -> str:
    collapsed = " ".join(content.split())
    return collapsed[:TITLE_SOURCE_CHARS] or DEFAULT_PROJECT_TITLE


async def _next_sequence(session: AsyncSession, project_id: str) -> int:
    current = await session.scalar(
        select(func.max(Message.sequence_no)).where(Message.project_id == project_id)
    )
    return (current or 0) + 1


def _invalid_response(message: str) -> ApiError:
    return ApiError(502, "LLM_RESPONSE_INVALID", message)


def _validate_result(result: OrganizeResult) -> BriefData:
    if result.outcome not in ("needs_input", "review"):
        raise _invalid_response("整理结果状态无效")
    if not isinstance(result.assistant_message, str):
        raise _invalid_response("整理结果缺少助手消息")
    message = result.assistant_message.strip()
    if not message or len(message) > MAX_MESSAGE_CHARS:
        raise _invalid_response("整理结果助手消息为空或过长")
    brief = result.brief
    if not isinstance(brief, BriefData):
        raise _invalid_response("整理结果需求卡无效")
    for name in BRIEF_FIELDS:
        value = getattr(brief, name)
        if not isinstance(value, str) or len(value) > MAX_BRIEF_FIELD_CHARS:
            raise _invalid_response(f"整理结果字段 {name} 无效")
    if not isinstance(brief.prompt, str) or len(brief.prompt) > MAX_PROMPT_CHARS:
        raise _invalid_response("整理结果提示词无效")
    if result.outcome == "review" and not brief.prompt.strip():
        raise _invalid_response("整理结果缺少完整提示词")
    return brief


def _injected_image_size(settings: Settings, project: Project) -> str | None:
    """The server owns the selected size; the model can never choose it."""
    visual = settings.visual()
    if visual.configured and visual.image_size:
        return visual.image_size
    return project.image_size


def _build_request(
    project: Project,
    messages: list[Message],
    user_text: str,
    has_reference: bool,
    image_size: str | None,
) -> OrganizeRequest:
    brief = brief_from_project(project).model_copy(update={"image_size": image_size})
    return OrganizeRequest(
        brief=BriefData(**brief.model_dump()),
        messages=tuple(
            OrganizeMessage(role=message.role, content=message.content) for message in messages
        ),
        user_text=user_text,
        has_reference_image=has_reference,
    )


async def _mark_failure(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_id: str,
    start_revision: int,
    operation_id: str,
    error_code: str,
    error_message: str,
) -> None:
    async with write_transaction(session_factory) as session:
        project = await require_project_scope(session, project_id, None)
        if (
            project.revision != start_revision
            or project.brief_status != "organizing"
            or project.organize_operation_id != operation_id
        ):
            return
        project.brief_status = "draft"
        project.organize_operation_id = None
        project.prompt_revision = None
        project.confirmed_revision = None
        project.brief_error_code = error_code
        project.brief_error_message = error_message
        project.updated_at = format_timestamp(utc_now())


async def _request_messages(
    session: AsyncSession, project_id: str, request_id: str
) -> list[Message]:
    return list(
        (
            await session.scalars(
                select(Message)
                .where(
                    Message.project_id == project_id,
                    Message.operation_id == request_id,
                )
                .order_by(Message.sequence_no.asc())
            )
        ).all()
    )


async def _existing_request_exchange(
    ctx: Any,
    request_id: str,
    content: str,
    principal: Principal | None,
) -> MessageExchange | None:
    from app.workflow import state as workflow_state

    async with read_session(ctx.session_factory) as session:
        rows = await _request_messages(session, ctx.project_id, request_id)
        users = [row for row in rows if row.role == "user"]
        assistants = [row for row in rows if row.role == "assistant"]
        if not users:
            return None
        if users[0].content != content:
            raise conflict(
                "COMMAND_ID_CONFLICT",
                "同一请求标识不能用于不同消息内容，请使用新的请求标识。",
            )
        if not assistants:
            raise conflict(
                "REQUEST_INCOMPLETE",
                "该请求已受理但尚未完成，请刷新工作流后重试；系统不会自动重试。",
            )
        project = await require_project_scope(session, ctx.project_id, principal)
        reference_asset = (
            await session.get(Asset, project.reference_asset_id)
            if project.reference_asset_id
            else None
        )
        view = await workflow_state.build_workflow_view(ctx)
        return MessageExchange(
            project=project,
            user_message=users[0],
            assistant_message=assistants[-1],
            reference_asset=reference_asset,
            workflow=view,
            kind=assistants[-1].turn_kind,
        )


async def _append_workflow_message(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_id: str,
    role: str,
    content: str,
    project_revision: int,
    operation_id: str,
    turn_kind: str,
) -> Message:
    now = format_timestamp(utc_now())
    async with write_transaction(session_factory) as session:
        message = Message(
            id=str(uuid4()),
            project_id=project_id,
            sequence_no=await _next_sequence(session, project_id),
            role=role,
            content=content,
            project_revision=project_revision,
            brief_snapshot_json=None,
            operation_id=operation_id,
            turn_kind=turn_kind,
            created_at=now,
        )
        session.add(message)
        await session.flush()
        return message


async def _max_sequence(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> int:
    async with read_session(session_factory) as session:
        current = await session.scalar(
            select(func.max(Message.sequence_no)).where(Message.project_id == project_id)
        )
        return int(current or 0)


async def _settle_pending_intent(ctx: Any, reason: str) -> None:
    from app.acceptance import latest_intent, settle_unsubmitted_intent

    if ctx.project.owner_account_id is None:
        return
    pending = await latest_intent(
        ctx.session_factory, project_id=ctx.project_id, run_id=ctx.run_id
    )
    if pending is None or pending.status != "accepted":
        return
    settled = await settle_unsubmitted_intent(
        ctx.session_factory,
        intent_id=pending.id,
        reason=reason,
        operator_account_id=(
            ctx.principal.account_id if ctx.principal is not None else None
        ),
        detail={"project_revision": ctx.project.revision},
    )
    if not settled:
        raise conflict(
            "INTENT_ATTEMPT_EXISTS",
            "原任务已有图片尝试或结果未知，不能按未提交任务结算；"
            "本次受理计数与占用仍保留，请先处理原任务。",
        )


async def _classify_message(ctx: Any, content: str) -> Any:
    from app.workflow.contracts import ConversationData
    from app.workflow.roles import run_role

    output = await run_role(
        ctx,
        "conversation_router",
        ctx.run.stage,
        {
            "user_text": content,
            "current_stage": ctx.run.stage,
            "current_status": ctx.run.status,
            "intent_route": ctx.run.intent_route,
            "intent_action": ctx.run.intent_action,
            "reference_asset_id": ctx.project.reference_asset_id,
            "available_actions": [],
        },
    )
    if output.status != "ready" or output.data is None:
        raise conflict(
            "MESSAGE_NOT_UNDERSTOOD",
            workflow_templates.render("INTENT_UNCLEAR"),
        )
    return ConversationData.model_validate(output.data)


async def _plan_message_action(ctx: Any, decision: Any) -> tuple[str | None, str | None, str]:
    from app.workflow import dispatch
    from app.workflow.stages import s02

    kind = decision.kind
    stage = ctx.run.stage
    status = ctx.run.status
    if kind == "conversation":
        return None, None, "CONVERSATION_ACK"
    if kind == "clarification":
        if stage == "S01":
            action = (
                "answer_intent_clarification"
                if status in {"needs_input", "blocked"}
                else "submit_request"
                if ctx.run.intent_route is None
                else None
            )
            return action, None, "CONVERSATION_CLARIFY"
        if stage == "S03":
            action = (
                "answer_requirements_clarification"
                if status in {"needs_input", "blocked"}
                else "revise_brief_draft"
            )
            return action, None, "CONVERSATION_CLARIFY"
        if stage == "S04":
            ids = {item.id for item in dispatch.available_actions(ctx)}
            return (
                ("revise_creative" if "revise_creative" in ids else None),
                None,
                "CONVERSATION_CLARIFY",
            )
        if stage == "S05":
            ids = {item.id for item in dispatch.available_actions(ctx)}
            return (
                ("revise_visual" if "revise_visual" in ids else None),
                None,
                "CONVERSATION_CLARIFY",
            )
        return None, None, "CONVERSATION_CLARIFY"
    if kind == "requirements_change":
        if stage in {"S06", "S07"}:
            return "revise_requirements", None, "CONVERSATION_CHANGE_ACCEPTED"
        if stage == "S03":
            return "revise_brief_draft", None, "CONVERSATION_CHANGE_ACCEPTED"
        if stage == "S01":
            action = (
                "answer_intent_clarification"
                if status in {"needs_input", "blocked"}
                else "submit_request"
            )
            return action, None, "CONVERSATION_CHANGE_ACCEPTED"
        if stage in {"S02", "S04", "S05"}:
            return None, "requirements", "CONVERSATION_CHANGE_ACCEPTED"
        return None, None, "CONVERSATION_ACK"
    if kind == "prompt_change":
        if stage == "S07" and status in {"active", "needs_input"}:
            return "revise_prompt", None, "CONVERSATION_PROMPT_CHANGE"
        return None, None, "CONVERSATION_ACK"
    if kind == "reference_usage_change":
        if ctx.project.reference_asset_id is None:
            return None, None, "CONVERSATION_ACK"
        current = await s02.current_reference_usage(ctx)
        if current == decision.reference_usage:
            return None, None, "CONVERSATION_ACK"
        return "reference_usage", None, "REFERENCE_USAGE_UPDATED"
    return None, None, "CONVERSATION_ACK"


async def _write_conversation_exchange(
    ctx: Any,
    *,
    content: str,
    request_id: str,
    principal: Principal | None,
    template: str = "CONVERSATION_ACK",
) -> MessageExchange:
    from app.workflow import state as workflow_state

    user_message = await _append_workflow_message(
        ctx.session_factory,
        project_id=ctx.project_id,
        role="user",
        content=content,
        project_revision=ctx.project.revision,
        operation_id=request_id,
        turn_kind="conversation",
    )
    assistant_message = await _append_workflow_message(
        ctx.session_factory,
        project_id=ctx.project_id,
        role="assistant",
        content=workflow_templates.render(template),
        project_revision=ctx.project.revision,
        operation_id=request_id,
        turn_kind="conversation",
    )
    async with read_session(ctx.session_factory) as session:
        project = await require_project_scope(session, ctx.project_id, principal)
        reference_asset = (
            await session.get(Asset, project.reference_asset_id)
            if project.reference_asset_id
            else None
        )
    view = await workflow_state.build_workflow_view(ctx)
    return MessageExchange(
        project=project,
        user_message=user_message,
        assistant_message=assistant_message,
        reference_asset=reference_asset,
        workflow=view,
        kind="conversation",
    )


async def _finish_action_exchange(
    ctx: Any,
    *,
    request_id: str,
    kind: str,
    start_sequence: int,
    fixed_template: str,
    principal: Principal | None,
) -> MessageExchange:
    from app.workflow import state as workflow_state

    async with write_transaction(ctx.session_factory) as session:
        project = await session.get(Project, ctx.project_id)
        if project is None:
            raise conflict("PROJECT_NOT_FOUND", "项目不存在")
        rows = list(
            (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.project_id == ctx.project_id,
                        Message.sequence_no > start_sequence,
                    )
                    .order_by(Message.sequence_no.asc())
                )
            ).all()
        )
        for row in rows:
            if row.role in {"user", "assistant"}:
                row.operation_id = request_id
                if row.role == "user":
                    row.turn_kind = "prompt" if kind == "prompt_change" else "workflow"
        users = [row for row in rows if row.role == "user"]
        assistants = [row for row in rows if row.role == "assistant"]
        if not users:
            # Every successful action either writes its own user turn or
            # receives a prewritten one; keep the invariant explicit.
            raise conflict("MESSAGE_NOT_RECORDED", "消息未能记录，请重新发送。")
        user_message = users[-1]
        if not assistants:
            assistant_message = Message(
                id=str(uuid4()),
                project_id=ctx.project_id,
                sequence_no=await _next_sequence(session, ctx.project_id),
                role="assistant",
                content=workflow_templates.render(fixed_template),
                project_revision=project.revision,
                brief_snapshot_json=None,
                operation_id=request_id,
                turn_kind="workflow",
                created_at=format_timestamp(utc_now()),
            )
            session.add(assistant_message)
        else:
            assistant_message = assistants[-1]
        await session.flush()
    async with read_session(ctx.session_factory) as session:
        project = await require_project_scope(session, ctx.project_id, principal)
        reference_asset = (
            await session.get(Asset, project.reference_asset_id)
            if project.reference_asset_id
            else None
        )
    fresh = await workflow_state.reload_context(ctx)
    view = await workflow_state.build_workflow_view(fresh)
    return MessageExchange(
        project=project,
        user_message=user_message,
        assistant_message=assistant_message,
        reference_asset=reference_asset,
        workflow=view,
        kind=kind,
    )


async def _stamp_partial_action_messages(
    ctx: Any, *, request_id: str, kind: str, start_sequence: int
) -> None:
    async with write_transaction(ctx.session_factory) as session:
        rows = list(
            (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.project_id == ctx.project_id,
                        Message.sequence_no > start_sequence,
                    )
                    .order_by(Message.sequence_no.asc())
                )
            ).all()
        )
        for row in rows:
            if row.role in {"user", "assistant"}:
                row.operation_id = request_id
                if row.role == "user":
                    row.turn_kind = "prompt" if kind == "prompt_change" else "workflow"


async def _send_workflow_message(
    session_factory: async_sessionmaker[AsyncSession],
    locks: ProjectLocks,
    settings: Settings,
    *,
    app: Any | None,
    project_id: str,
    content: str,
    expected_revision: int,
    request_id: str | None,
    principal: Principal | None,
) -> MessageExchange:
    from app.workflow import dispatch
    from app.workflow import state as workflow_state
    from app.workflow.stages import s02, s03
    from app.workflow.tables import GenerationJob

    if app is None:
        raise ApiError(503, "WORKFLOW_UNAVAILABLE", "工作流服务不可用")
    if len(content) > MAX_MESSAGE_CHARS:
        raise validation_error("消息内容过长")
    async with locks.hold(project_id):
        ctx = await workflow_state.load_context(app, project_id, principal=principal)
        if ctx.run is None:
            raise conflict(
                "LEGACY_FLOW_DISABLED",
                workflow_templates.render("LEGACY_FLOW_DISABLED"),
            )
        effective_request_id = request_id or str(uuid4())
        existing = await _existing_request_exchange(
            ctx, effective_request_id, content, principal
        )
        if existing is not None:
            return existing
        if ctx.run.recovery_claim_id:
            raise conflict("PROJECT_BUSY", "项目正在恢复处理，请稍后再试")
        if ctx.project.revision != expected_revision:
            raise conflict("REVISION_CONFLICT", "项目已被其他操作更新，请刷新后重试")
        async with read_session(session_factory) as session:
            active = await session.scalar(
                select(func.count())
                .select_from(Generation)
                .where(
                    Generation.project_id == project_id,
                    Generation.status.in_(("queued", "running")),
                )
            )
            unconverged = await session.scalar(
                select(func.count())
                .select_from(GenerationJob)
                .where(
                    GenerationJob.project_id == project_id,
                    GenerationJob.status.in_(
                        (
                            "created",
                            "submit_pending",
                            "submitted",
                            "remote_pending",
                            "unknown",
                            "quarantined",
                        )
                    ),
                )
            )
        if active or unconverged:
            raise conflict("ACTIVE_GENERATION_EXISTS", "项目有正在进行的生成，请稍后再试")
        decision = await _classify_message(ctx, content)
        action, direct, fixed_template = await _plan_message_action(ctx, decision)
        if action is None and direct is None:
            return await _write_conversation_exchange(
                ctx,
                content=content,
                request_id=effective_request_id,
                principal=principal,
                template=fixed_template,
            )
        start_sequence = await _max_sequence(session_factory, project_id)
        try:
            if direct == "requirements":
                await _settle_pending_intent(ctx, "requirements_revision")
                await s03.start_requirements_revision(ctx, content)
            elif action == "reference_usage":
                await _settle_pending_intent(ctx, "reference_usage_changed")
                await _append_workflow_message(
                    session_factory,
                    project_id=project_id,
                    role="user",
                    content=content,
                    project_revision=ctx.project.revision,
                    operation_id=effective_request_id,
                    turn_kind="workflow",
                )
                await s02.handle(
                    ctx, "confirm_reference_usage", {"usage": decision.reference_usage}
                )
            elif action == "revise_prompt":
                await _append_workflow_message(
                    session_factory,
                    project_id=project_id,
                    role="user",
                    content=content,
                    project_revision=ctx.project.revision,
                    operation_id=effective_request_id,
                    turn_kind="prompt",
                )
                await dispatch.handle_action(ctx, "revise_prompt", {"text": content})
            elif action == "revise_visual":
                await _append_workflow_message(
                    session_factory,
                    project_id=project_id,
                    role="user",
                    content=content,
                    project_revision=ctx.project.revision,
                    operation_id=effective_request_id,
                    turn_kind="workflow",
                )
                await dispatch.handle_action(ctx, action, {"text": content})
            else:
                await dispatch.handle_action(ctx, action, {"text": content})
        except ApiError:
            await _stamp_partial_action_messages(
                ctx,
                request_id=effective_request_id,
                kind=decision.kind,
                start_sequence=start_sequence,
            )
            raise
        return await _finish_action_exchange(
            ctx,
            request_id=effective_request_id,
            kind=decision.kind,
            start_sequence=start_sequence,
            fixed_template=fixed_template,
            principal=principal,
        )


async def send_message(
    session_factory: async_sessionmaker[AsyncSession],
    locks: ProjectLocks,
    registry: AdapterRegistry,
    settings: Settings,
    http_client: Any,
    *,
    project_id: str,
    content: str,
    expected_revision: int,
    principal: Principal | None = None,
    app: Any | None = None,
    request_id: str | None = None,
) -> MessageExchange:
    if not content.strip():
        raise validation_error("消息内容不能为空")
    async with read_session(session_factory) as session:
        await require_project_scope(session, project_id, principal)
        workflow_started = await session.scalar(
            select(WorkflowRun.id)
            .where(WorkflowRun.project_id == project_id)
            .limit(1)
        )
        if workflow_started is not None:
            return await _send_workflow_message(
                session_factory,
                locks,
                settings,
                app=app,
                project_id=project_id,
                content=content,
                expected_revision=expected_revision,
                request_id=request_id,
                principal=principal,
            )
        # 旧项目的模型整理入口必须由后端统一暂停，前端禁用不具备授权效力。
        await ensure_legacy_entry_available(session, project_id)
    role = ensure_llm_ready(settings, registry)

    async with locks.hold(project_id):
        now = format_timestamp(utc_now())
        operation_id = str(uuid4())
        async with write_transaction(session_factory) as session:
            project = await require_project_scope(session, project_id, principal)
            if project.revision != expected_revision:
                raise conflict("REVISION_CONFLICT", "项目已被其他操作更新，请刷新后重试")
            active = await session.scalar(
                select(func.count())
                .select_from(Generation)
                .where(
                    Generation.project_id == project_id,
                    Generation.status.in_(("queued", "running")),
                )
            )
            if active:
                raise conflict("ACTIVE_GENERATION_EXISTS", "项目有正在进行的生成，请稍后再试")
            if project.brief_status == "organizing":
                raise conflict("STALE_ORGANIZATION", "项目正在整理中，请稍后重试")

            start_revision = project.revision + 1
            user_message = Message(
                id=str(uuid4()),
                project_id=project_id,
                sequence_no=await _next_sequence(session, project_id),
                role="user",
                content=content,
                project_revision=start_revision,
                brief_snapshot_json=None,
                operation_id=operation_id,
                created_at=now,
            )
            session.add(user_message)
            project.revision = start_revision
            project.prompt_revision = None
            project.confirmed_revision = None
            project.brief_status = "organizing"
            project.organize_operation_id = operation_id
            project.brief_error_code = None
            project.brief_error_message = None
            if start_revision == 1 and project.title == DEFAULT_PROJECT_TITLE:
                project.title = _collapsed_title(content)
            project.updated_at = now
            await session.flush()

            messages = list(
                (
                    await session.scalars(
                        select(Message)
                        .where(Message.project_id == project_id)
                        .order_by(Message.sequence_no)
                    )
                ).all()
            )
            reference_asset = (
                await session.get(Asset, project.reference_asset_id)
                if project.reference_asset_id
                else None
            )
            image_size = _injected_image_size(settings, project)
            request = _build_request(
                project,
                messages,
                content,
                reference_asset is not None,
                image_size,
            )

        if role.vision_model and reference_asset is not None:
            reference_path = resolve_asset_file(settings, reference_asset)
            if reference_path is not None:
                try:
                    reference_bytes = await asyncio.to_thread(reference_path.read_bytes)
                except OSError:
                    logger.warning(
                        "reference image unreadable project=%s", project_id
                    )
                else:
                    if reference_bytes:
                        request = replace(
                            request,
                            reference_image=OrganizeImage(
                                data=reference_bytes,
                                mime_type=reference_asset.mime_type,
                            ),
                        )

        adapter = registry.build_llm(role, http_client)
        if adapter is None:
            raise ApiError(503, "ADAPTER_UNAVAILABLE", "语言模型适配器不可用，请检查本地配置")

        try:
            async with asyncio.timeout(role.timeout_seconds):
                result = await adapter.organize(request)
        except TimeoutError:
            await _mark_failure(
                session_factory,
                project_id=project_id,
                start_revision=start_revision,
                operation_id=operation_id,
                error_code="ORGANIZATION_TIMEOUT",
                error_message="整理超时，请重试",
            )
            raise ApiError(504, "LLM_TIMEOUT", "整理超时，请重试") from None
        except Exception:
            logger.exception("llm organize failed project=%s", project_id)
            await _mark_failure(
                session_factory,
                project_id=project_id,
                start_revision=start_revision,
                operation_id=operation_id,
                error_code="ORGANIZATION_FAILED",
                error_message="整理失败，请重试",
            )
            raise ApiError(502, "LLM_CALL_FAILED", "整理失败，请重试") from None

        try:
            brief = _validate_result(result)
        except ApiError:
            await _mark_failure(
                session_factory,
                project_id=project_id,
                start_revision=start_revision,
                operation_id=operation_id,
                error_code="ORGANIZATION_FAILED",
                error_message="整理结果不可用，请重试",
            )
            raise

        async with write_transaction(session_factory) as session:
            project = await require_project_scope(session, project_id, principal)
            if (
                project.revision != start_revision
                or project.brief_status != "organizing"
                or project.organize_operation_id != operation_id
            ):
                raise conflict("STALE_ORGANIZATION", "整理结果已过期，请刷新后重试")

            new_revision = start_revision + 1
            if result.outcome == "review":
                for name in BRIEF_FIELDS:
                    setattr(project, name, getattr(brief, name))
                project.prompt = brief.prompt
                project.prompt_revision = new_revision
            else:
                for name in BRIEF_FIELDS:
                    value = getattr(brief, name)
                    if value:
                        setattr(project, name, value)
                project.prompt_revision = None
            project.image_size = image_size
            project.confirmed_revision = None
            project.brief_status = "review" if result.outcome == "review" else "needs_input"
            project.organize_operation_id = None
            project.brief_error_code = None
            project.brief_error_message = None
            project.revision = new_revision
            project.updated_at = format_timestamp(utc_now())

            assistant_message = Message(
                id=str(uuid4()),
                project_id=project_id,
                sequence_no=await _next_sequence(session, project_id),
                role="assistant",
                content=result.assistant_message,
                project_revision=new_revision,
                brief_snapshot_json=snapshot_json(brief_from_project(project)),
                operation_id=operation_id,
                created_at=project.updated_at,
            )
            session.add(assistant_message)
            reference_asset = (
                await session.get(Asset, project.reference_asset_id)
                if project.reference_asset_id
                else None
            )
            await session.flush()

        return MessageExchange(
            project=project,
            user_message=user_message,
            assistant_message=assistant_message,
            reference_asset=reference_asset,
        )

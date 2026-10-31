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
            raise conflict(
                "LEGACY_FLOW_DISABLED",
                workflow_templates.render("LEGACY_FLOW_DISABLED"),
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

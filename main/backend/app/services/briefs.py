from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.registry import AdapterRegistry
from app.config import Settings
from app.db import ProjectLocks, write_transaction
from app.errors import ApiError, conflict
from app.models import Asset, Generation, Project
from app.services.projects import require_project
from app.timeutil import format_timestamp, utc_now

EDITABLE_FIELDS = ("product_description", "purpose", "style", "composition", "text_content")


@dataclass
class BriefMutation:
    project: Project
    reference_asset: Asset | None


async def _active_generation_count(session: AsyncSession, project_id: str) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Generation)
            .where(
                Generation.project_id == project_id,
                Generation.status.in_(("queued", "running")),
            )
        )
        or 0
    )


async def _load_reference_asset(session: AsyncSession, project: Project) -> Asset | None:
    if not project.reference_asset_id:
        return None
    return await session.get(Asset, project.reference_asset_id)


async def update_brief(
    session_factory: async_sessionmaker[AsyncSession],
    locks: ProjectLocks,
    *,
    project_id: str,
    expected_revision: int,
    changes: dict[str, str],
) -> BriefMutation:
    invalid = set(changes) - set(EDITABLE_FIELDS)
    if invalid:
        raise ApiError(422, "VALIDATION_ERROR", "包含不可编辑字段")

    async with locks.hold(project_id):
        async with write_transaction(session_factory) as session:
            project = await require_project(session, project_id)
            if project.revision != expected_revision:
                raise conflict("REVISION_CONFLICT", "项目已被其他操作更新，请刷新后重试")
            if await _active_generation_count(session, project_id):
                raise conflict("ACTIVE_GENERATION_EXISTS", "项目有正在进行的生成，请稍后再试")
            if project.brief_status == "organizing":
                raise conflict("STALE_ORGANIZATION", "项目正在整理中，请稍后重试")

            if all(getattr(project, name) == value for name, value in changes.items()):
                return BriefMutation(project, await _load_reference_asset(session, project))

            for name, value in changes.items():
                setattr(project, name, value)
            project.revision += 1
            project.prompt_revision = None
            project.confirmed_revision = None
            project.brief_status = "draft"
            project.brief_error_code = None
            project.brief_error_message = None
            project.updated_at = format_timestamp(utc_now())
            await session.flush()
            return BriefMutation(project, await _load_reference_asset(session, project))


def _validate_image_size(project: Project, settings: Settings, registry: AdapterRegistry) -> None:
    role = settings.visual()
    if not role.configured or not role.image_size:
        raise conflict("IMAGE_SIZE_CHANGED", "生成尺寸尚未核实，请重新整理需求")
    if project.image_size != role.image_size:
        raise conflict("IMAGE_SIZE_CHANGED", "当前生成尺寸与配置不一致，请重新整理需求")
    capabilities = registry.visual_capabilities(role.adapter_id or "")
    supported = capabilities.supported_image_sizes if capabilities else None
    if supported and project.image_size not in supported:
        raise conflict("IMAGE_SIZE_CHANGED", "当前生成尺寸不受支持，请重新整理需求")


async def confirm_brief(
    session_factory: async_sessionmaker[AsyncSession],
    locks: ProjectLocks,
    settings: Settings,
    registry: AdapterRegistry,
    *,
    project_id: str,
    revision: int,
) -> BriefMutation:
    async with locks.hold(project_id):
        async with write_transaction(session_factory) as session:
            project = await require_project(session, project_id)
            if project.revision != revision:
                raise conflict("REVISION_CONFLICT", "项目已被其他操作更新，请刷新后重试")
            if project.confirmed_revision == revision and project.brief_status == "confirmed":
                return BriefMutation(project, await _load_reference_asset(session, project))
            if await _active_generation_count(session, project_id):
                raise conflict("ACTIVE_GENERATION_EXISTS", "项目有正在进行的生成，请稍后再试")
            if project.brief_status == "organizing":
                raise conflict("STALE_ORGANIZATION", "项目正在整理中，请稍后重试")
            if (
                project.brief_status != "review"
                or project.prompt_revision != revision
                or not project.prompt.strip()
            ):
                raise conflict("BRIEF_NOT_READY", "需求尚未整理完成，无法确认")
            if not project.product_description.strip():
                raise conflict("BRIEF_NOT_READY", "缺少可识别的商品主体，无法确认")
            _validate_image_size(project, settings, registry)

            project.confirmed_revision = revision
            project.brief_status = "confirmed"
            project.brief_error_code = None
            project.brief_error_message = None
            project.updated_at = format_timestamp(utc_now())
            await session.flush()
            return BriefMutation(project, await _load_reference_asset(session, project))

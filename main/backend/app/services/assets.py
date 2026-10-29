from __future__ import annotations

import asyncio
import io
import warnings
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.registry import AdapterRegistry
from app.config import (
    LOCAL_REFERENCE_MAX_BYTES,
    Settings,
)
from app.db import ProjectLocks, write_transaction
from app.errors import ApiError, conflict
from app.models import Asset, Generation, Project
from app.services.projects import require_project
from app.timeutil import format_timestamp, utc_now

MIME_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg"}


class ImageValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ImageInfo:
    mime_type: str
    width: int
    height: int


@dataclass
class ReferenceMutation:
    project: Project
    asset: Asset | None


def effective_reference_max_bytes(settings: Settings, registry: AdapterRegistry) -> int:
    visual = settings.visual()
    capabilities = registry.visual_capabilities(visual.adapter_id or "") if visual.configured else None
    provider_limit = capabilities.provider_max_reference_bytes if capabilities else None
    if provider_limit is not None and provider_limit > 0:
        return min(LOCAL_REFERENCE_MAX_BYTES, provider_limit)
    return LOCAL_REFERENCE_MAX_BYTES


def inspect_image(data: bytes) -> ImageInfo:
    if not data:
        raise ImageValidationError("INVALID_REFERENCE_IMAGE", "参考图内容为空")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as probe:
                image_format = probe.format
                if image_format not in ("PNG", "JPEG"):
                    raise ImageValidationError(
                        "UNSUPPORTED_REFERENCE_FORMAT", "仅支持 PNG 或 JPEG 参考图"
                    )
                probe.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                width, height = image.size
    except ImageValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
    ):
        raise ImageValidationError("INVALID_REFERENCE_IMAGE", "参考图无法解析") from None
    if width <= 0 or height <= 0:
        raise ImageValidationError("INVALID_REFERENCE_IMAGE", "参考图尺寸无效")
    mime_type = "image/png" if image_format == "PNG" else "image/jpeg"
    return ImageInfo(mime_type=mime_type, width=width, height=height)


def _write_file(settings: Settings, relative_path: str, data: bytes) -> None:
    destination = settings.resolved_data_dir() / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.temp_dir / f"{uuid4()}.part"
    temporary.write_bytes(data)
    temporary.replace(destination)


def _delete_file(settings: Settings, relative_path: str) -> None:
    try:
        (settings.resolved_data_dir() / relative_path).unlink()
    except OSError:
        pass


def resolve_asset_file(settings: Settings, asset: Asset) -> Path | None:
    base = settings.resolved_data_dir().resolve()
    candidate = (base / asset.relative_path).resolve()
    if not candidate.is_relative_to(base):
        return None
    return candidate if candidate.is_file() else None


async def _bump_revision(session: AsyncSession, project: Project, now: str) -> None:
    project.revision += 1
    project.prompt_revision = None
    project.confirmed_revision = None
    project.brief_status = "draft"
    project.brief_error_code = None
    project.brief_error_message = None
    project.updated_at = now


async def _active_count(session: AsyncSession, project_id: str) -> int:
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


async def attach_reference(
    session_factory: async_sessionmaker[AsyncSession],
    locks: ProjectLocks,
    settings: Settings,
    *,
    project_id: str,
    expected_revision: int,
    data: bytes,
    image: ImageInfo,
) -> ReferenceMutation:
    async with locks.hold(project_id):
        asset_id = str(uuid4())
        extension = MIME_EXTENSIONS[image.mime_type]
        relative_path = f"assets/{asset_id}.{extension}"
        await asyncio.to_thread(_write_file, settings, relative_path, data)
        try:
            async with write_transaction(session_factory) as session:
                project = await require_project(session, project_id)
                if project.revision != expected_revision:
                    raise conflict("REVISION_CONFLICT", "项目已被其他操作更新，请刷新后重试")
                if await _active_count(session, project_id):
                    raise conflict("ACTIVE_GENERATION_EXISTS", "项目有正在进行的生成，请稍后再试")
                if project.brief_status == "organizing":
                    raise conflict("STALE_ORGANIZATION", "项目正在整理中，请稍后重试")

                now = format_timestamp(utc_now())
                asset = Asset(
                    id=asset_id,
                    project_id=project_id,
                    kind="reference",
                    relative_path=relative_path,
                    mime_type=image.mime_type,
                    width=image.width,
                    height=image.height,
                    byte_size=len(data),
                    created_at=now,
                )
                session.add(asset)
                project.reference_asset_id = asset_id
                await _bump_revision(session, project, now)
                await session.flush()
                return ReferenceMutation(project, asset)
        except BaseException:
            await asyncio.to_thread(_delete_file, settings, relative_path)
            raise


async def remove_reference(
    session_factory: async_sessionmaker[AsyncSession],
    locks: ProjectLocks,
    *,
    project_id: str,
    expected_revision: int,
) -> ReferenceMutation:
    async with locks.hold(project_id):
        async with write_transaction(session_factory) as session:
            project = await require_project(session, project_id)
            if project.revision != expected_revision:
                raise conflict("REVISION_CONFLICT", "项目已被其他操作更新，请刷新后重试")
            if not project.reference_asset_id:
                return ReferenceMutation(project, None)
            if await _active_count(session, project_id):
                raise conflict("ACTIVE_GENERATION_EXISTS", "项目有正在进行的生成，请稍后再试")
            if project.brief_status == "organizing":
                raise conflict("STALE_ORGANIZATION", "项目正在整理中，请稍后重试")

            project.reference_asset_id = None
            now = format_timestamp(utc_now())
            await _bump_revision(session, project, now)
            await session.flush()
            return ReferenceMutation(project, None)


async def load_asset(session: AsyncSession, asset_id: str | None) -> Asset | None:
    if not asset_id:
        return None
    return await session.get(Asset, asset_id)


async def download_name(session: AsyncSession, asset: Asset) -> str:
    extension = MIME_EXTENSIONS.get(asset.mime_type, "bin")
    if asset.kind == "reference":
        return f"{asset.id}.{extension}"
    generation = await session.scalar(
        select(Generation).where(Generation.result_asset_id == asset.id)
    )
    if generation is None:
        return f"{asset.id}.{extension}"
    return f"{generation.project_id}-v{generation.version_no}.{extension}"


def require_asset_file(settings: Settings, asset: Asset) -> Path:
    path = resolve_asset_file(settings, asset)
    if path is None:
        raise ApiError(404, "ASSET_FILE_MISSING", "资源文件不存在")
    return path

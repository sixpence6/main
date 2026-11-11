from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.principal import Principal
from app.config import Settings
from app.db import ProjectLocks, read_session, write_transaction
from app.errors import conflict, not_found_asset
from app.models import Asset, Message, MessageAttachment
from app.ownership import require_project_scope
from app.services.assets import (
    MIME_EXTENSIONS,
    ImageInfo,
    delete_asset_file,
    write_asset_file,
)
from app.timeutil import format_timestamp, utc_now


@dataclass
class StagedAttachment:
    asset: Asset
    content_version: str


def preview_url(asset_id: str) -> str:
    return f"/api/assets/{asset_id}"


async def stage_message_attachment(
    session_factory: async_sessionmaker[AsyncSession],
    locks: ProjectLocks,
    settings: Settings,
    *,
    project_id: str,
    data: bytes,
    image: ImageInfo,
    principal: Principal,
) -> StagedAttachment:
    """Persist an uploaded image without making it the business reference yet."""
    asset_id = str(uuid4())
    extension = MIME_EXTENSIONS[image.mime_type]
    relative_path = f"attachments/{asset_id}.{extension}"
    digest = hashlib.sha256(data).hexdigest()
    async with locks.hold(project_id):
        write_asset_file(settings, relative_path, data)
        try:
            async with write_transaction(session_factory) as session:
                await require_project_scope(session, project_id, principal)
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
                    sha256=digest,
                    review_status="quarantined",
                    review_note=None,
                    created_at=now,
                )
                session.add(asset)
                await session.flush()
        except Exception:
            delete_asset_file(settings, relative_path)
            raise
    return StagedAttachment(asset=asset, content_version=digest)


async def discard_staged_attachment(
    session_factory: async_sessionmaker[AsyncSession],
    locks: ProjectLocks,
    settings: Settings,
    *,
    project_id: str,
    asset_id: str,
    principal: Principal,
) -> Asset:
    async with locks.hold(project_id):
        async with write_transaction(session_factory) as session:
            project = await require_project_scope(session, project_id, principal)
            asset = await session.get(Asset, asset_id)
            if asset is None or asset.project_id != project_id or asset.kind != "reference":
                raise not_found_asset()
            bound = await session.scalar(
                select(func.count())
                .select_from(MessageAttachment)
                .where(MessageAttachment.asset_id == asset.id)
            )
            if bound:
                raise conflict(
                    "ATTACHMENT_ALREADY_SENT",
                    "该图片已经随消息发送，不能作为暂存附件删除。",
                )
            if project.reference_asset_id == asset.id:
                raise conflict(
                    "ATTACHMENT_IN_USE",
                    "该图片仍是项目当前业务引用，请先替换或移除后再删除暂存附件。",
                )
            relative_path = asset.relative_path
            await session.delete(asset)
            await session.flush()
    delete_asset_file(settings, relative_path)
    return asset


async def bind_message_attachment(
    session: AsyncSession,
    *,
    project_id: str,
    message_id: str,
    asset: Asset,
    usage: str,
    now: str,
) -> MessageAttachment:
    existing = await session.scalar(
        select(MessageAttachment).where(MessageAttachment.asset_id == asset.id)
    )
    if existing is not None:
        raise conflict(
            "ATTACHMENT_ALREADY_SENT",
            "该图片已经随消息发送，不能重复使用。",
        )
    content_version = asset.sha256
    if not content_version:
        raise conflict("ATTACHMENT_NOT_AVAILABLE", "图片缺少稳定内容版本，不能发送。")
    attachment = MessageAttachment(
        id=str(uuid4()),
        project_id=project_id,
        message_id=message_id,
        asset_id=asset.id,
        usage=usage,
        content_version=content_version,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        byte_size=asset.byte_size,
        created_at=now,
    )
    session.add(attachment)
    await session.flush()
    return attachment


async def load_message_attachment(
    session_factory: async_sessionmaker[AsyncSession], message_id: str
) -> MessageAttachment | None:
    async with read_session(session_factory) as session:
        return await session.scalar(
            select(MessageAttachment).where(MessageAttachment.message_id == message_id)
        )


async def load_attachments_for_project(
    session: AsyncSession, project_id: str
) -> dict[str, MessageAttachment]:
    rows = (
        await session.scalars(
            select(MessageAttachment).where(MessageAttachment.project_id == project_id)
        )
    ).all()
    return {row.message_id: row for row in rows}


async def load_pending_attachment(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> tuple[MessageAttachment, Message] | None:
    async with read_session(session_factory) as session:
        row = (
            await session.execute(
                select(MessageAttachment, Message)
                .join(Message, Message.id == MessageAttachment.message_id)
                .where(
                    MessageAttachment.project_id == project_id,
                    MessageAttachment.usage == "none",
                )
                .order_by(Message.sequence_no.desc())
                .limit(1)
            )
        ).first()
        return (row[0], row[1]) if row is not None else None


async def set_attachment_usage(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    message_id: str,
    usage: str,
) -> None:
    async with write_transaction(session_factory) as session:
        attachment = await session.scalar(
            select(MessageAttachment).where(MessageAttachment.message_id == message_id)
        )
        if attachment is None:
            raise conflict("ATTACHMENT_NOT_FOUND", "消息附件不存在。")
        attachment.usage = usage
        await session.flush()


async def require_attachment_asset(
    session: AsyncSession, *, project_id: str, asset_id: str
) -> Asset:
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.project_id != project_id or asset.kind != "reference":
        raise not_found_asset()
    return asset

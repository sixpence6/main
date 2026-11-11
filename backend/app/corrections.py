from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import conflict
from app.models import Asset, ImageCorrection
from app.services.assets import resolve_asset_file
from app.timeutil import format_timestamp, utc_now


async def correction_for_intent(
    session: AsyncSession, intent_id: str
) -> ImageCorrection | None:
    return await session.scalar(
        select(ImageCorrection).where(ImageCorrection.intent_id == intent_id)
    )


async def source_bytes(
    session: AsyncSession, settings, correction: ImageCorrection
) -> tuple[Asset, bytes]:
    asset = await session.get(Asset, correction.source_asset_id)
    if (
        asset is None
        or asset.project_id != correction.project_id
        or asset.kind != "generated"
        or asset.sha256 != correction.source_asset_sha256
    ):
        raise conflict("CORRECTION_NOT_REGISTERED", "修正图来源不可用。")
    path = resolve_asset_file(settings, asset)
    if path is None:
        raise conflict("CORRECTION_NOT_REGISTERED", "修正图来源不可用。")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != correction.source_asset_sha256:
        raise conflict("CORRECTION_NOT_REGISTERED", "修正图来源已被替换。")
    return asset, data


async def set_status(
    session: AsyncSession,
    correction: ImageCorrection,
    status: str,
    *,
    result_asset_id: str | None = None,
    result_asset_sha256: str | None = None,
) -> None:
    correction.status = status
    if result_asset_id is not None:
        correction.result_asset_id = result_asset_id
    if result_asset_sha256 is not None:
        correction.result_asset_sha256 = result_asset_sha256
    correction.updated_at = format_timestamp(utc_now())
    await session.flush()

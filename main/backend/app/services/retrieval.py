from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import ProviderOutcome
from app.adapters.registry import AdapterRegistry
from app.auth.principal import Principal
from app.config import Settings
from app.db import read_session, write_transaction
from app.errors import ApiError, conflict, not_found_generation
from app.models import Asset, Generation, Project
from app.ownership import revalidate_principal
from app.services.assets import delete_asset_file
from app.services.generations import (
    _decode_outcome_image,
    _validate_result_bytes,
    parse_image_size,
    service_fingerprint,
)
from app.timeutil import format_timestamp, utc_now
from app.workflow.access import ensure_legacy_entry_available

logger = logging.getLogger("app.retrieval")

_RESULT_NOT_RETRIEVABLE = "RESULT_NOT_RETRIEVABLE"
_RETRIEVE_CONFIG_CHANGED = "RETRIEVE_CONFIG_CHANGED"
_ADAPTER_UNAVAILABLE = "ADAPTER_UNAVAILABLE"
_PROJECT_BUSY = "PROJECT_BUSY"

_REASON_NOT_FAILED = "只有结果文件获取失败的记录可以重取。"
_REASON_NO_LOCATOR = "原生成结果没有保存可重取的定位信息，无法重取。"
_REASON_INVALID_LOCATOR = "原生成结果的定位信息无效，无法重取。"
_REASON_EXPIRED = "原生成结果的可重取期限已过，无法重取。"
_REASON_CONFIG_CHANGED = "图片模型配置已更改，无法确认原始结果来源，不能重取。"
_REASON_ADAPTER_UNAVAILABLE = "原生成使用的图片模型适配器当前不可用，无法重取。"
_REASON_ORGANIZING = "项目正在整理需求，暂时不能重取结果。"
_REASON_ACTIVE = "项目有正在进行的生成，暂时不能重取结果。"


@dataclass(frozen=True)
class RetrievalInfo:
    can_retrieve: bool
    reason: str | None = None
    locator: str | None = None
    expires_at: str | None = None
    failure_code: str | None = None


def _decode_locator(raw: str | None) -> tuple[str | None, str | None, str | None]:
    if raw is None:
        return None, None, _REASON_NO_LOCATOR
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None, None, _REASON_INVALID_LOCATOR
    if isinstance(value, str):
        locator = value
        expires_at = None
    elif isinstance(value, dict) and set(value) == {"locator", "expires_at"}:
        locator = value["locator"]
        expires_at = value["expires_at"]
    else:
        return None, None, _REASON_INVALID_LOCATOR
    if not isinstance(locator, str) or not locator.strip():
        return None, None, _REASON_INVALID_LOCATOR
    if expires_at is not None and not isinstance(expires_at, str):
        return None, None, _REASON_INVALID_LOCATOR
    return locator, expires_at, None


def _expiry_is_valid(value: str | None) -> bool:
    if value is None:
        return True
    candidate = value.strip()
    if not candidate:
        return False
    try:
        moment = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment > utc_now()


def evaluate_retrieval(
    generation: Generation,
    *,
    settings: Settings,
    registry: AdapterRegistry,
    project: Project,
    active_generation_id: str | None,
) -> RetrievalInfo:
    if generation.status == "succeeded":
        return RetrievalInfo(can_retrieve=False, failure_code=_RESULT_NOT_RETRIEVABLE)
    if generation.status != "failed" or generation.error_code != "RESULT_FETCH_FAILED":
        return RetrievalInfo(
            can_retrieve=False,
            reason=_REASON_NOT_FAILED,
            failure_code=_RESULT_NOT_RETRIEVABLE,
        )

    current_role = settings.visual()
    if (
        not current_role.configured
        or service_fingerprint(current_role) != generation.service_fingerprint
    ):
        return RetrievalInfo(
            can_retrieve=False,
            reason=_REASON_CONFIG_CHANGED,
            failure_code=_RETRIEVE_CONFIG_CHANGED,
        )
    if generation.adapter_id not in registry.known_visual_ids():
        return RetrievalInfo(
            can_retrieve=False,
            reason=_REASON_ADAPTER_UNAVAILABLE,
            failure_code=_ADAPTER_UNAVAILABLE,
        )

    locator, expires_at, locator_reason = _decode_locator(generation.result_locator_json)
    if locator_reason is not None:
        return RetrievalInfo(
            can_retrieve=False,
            reason=locator_reason,
            failure_code=_RESULT_NOT_RETRIEVABLE,
        )
    if not _expiry_is_valid(expires_at):
        return RetrievalInfo(
            can_retrieve=False,
            reason=_REASON_EXPIRED,
            failure_code=_RESULT_NOT_RETRIEVABLE,
        )
    if project.brief_status == "organizing":
        return RetrievalInfo(
            can_retrieve=False,
            reason=_REASON_ORGANIZING,
            failure_code=_PROJECT_BUSY,
        )
    if active_generation_id is not None:
        return RetrievalInfo(
            can_retrieve=False,
            reason=_REASON_ACTIVE,
            failure_code=_PROJECT_BUSY,
        )
    return RetrievalInfo(can_retrieve=True, locator=locator, expires_at=expires_at)


def evaluate_generations_retrieval(
    generations: list[Generation],
    *,
    settings: Settings,
    registry: AdapterRegistry,
    project: Project,
) -> dict[str, RetrievalInfo]:
    active_generation_id = next(
        (
            generation.id
            for generation in generations
            if generation.status in ("queued", "running")
        ),
        None,
    )
    return {
        generation.id: evaluate_retrieval(
            generation,
            settings=settings,
            registry=registry,
            project=project,
            active_generation_id=active_generation_id,
        )
        for generation in generations
    }


async def load_retrieval_info(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    registry: AdapterRegistry,
    generation: Generation,
) -> RetrievalInfo:
    async with read_session(session_factory) as session:
        project = await session.get(Project, generation.project_id)
        if project is None:
            return RetrievalInfo(can_retrieve=False, failure_code=_RESULT_NOT_RETRIEVABLE)
        active_generation_id = await session.scalar(
            select(Generation.id).where(
                Generation.project_id == generation.project_id,
                Generation.status.in_(("queued", "running")),
            )
        )
    return evaluate_retrieval(
        generation,
        settings=settings,
        registry=registry,
        project=project,
        active_generation_id=active_generation_id,
    )


def _raise_not_retrievable(info: RetrievalInfo) -> None:
    if info.failure_code == _RETRIEVE_CONFIG_CHANGED:
        raise conflict(_RETRIEVE_CONFIG_CHANGED, info.reason or _REASON_CONFIG_CHANGED)
    if info.failure_code == _ADAPTER_UNAVAILABLE:
        raise ApiError(503, _ADAPTER_UNAVAILABLE, info.reason or _REASON_ADAPTER_UNAVAILABLE)
    if info.failure_code == _PROJECT_BUSY:
        raise conflict(_PROJECT_BUSY, info.reason or "项目正在处理其他写操作，请稍后重试")
    raise conflict(_RESULT_NOT_RETRIEVABLE, info.reason or _REASON_NOT_FAILED)


async def _load_generation(
    session_factory: async_sessionmaker[AsyncSession],
    generation_id: str,
    principal: Principal | None = None,
) -> tuple[Generation, Asset | None, Project, str | None]:
    async with read_session(session_factory) as session:
        generation = await session.get(Generation, generation_id)
        if generation is None:
            raise not_found_generation()
        if principal is not None:
            await revalidate_principal(session, principal)
        project = await session.get(Project, generation.project_id)
        if project is None or (
            principal is not None and project.owner_account_id != principal.account_id
        ):
            raise not_found_generation()
        asset = (
            await session.get(Asset, generation.result_asset_id)
            if generation.result_asset_id
            else None
        )
        if asset is not None and asset.project_id != generation.project_id:
            asset = None
        active_generation_id = await session.scalar(
            select(Generation.id).where(
                Generation.project_id == generation.project_id,
                Generation.status.in_(("queued", "running")),
            )
        )
    return generation, asset, project, active_generation_id


def _outcome_image(outcome: ProviderOutcome) -> bytes | None:
    try:
        return _decode_outcome_image(outcome)
    except Exception as exc:
        logger.warning("retrieval result could not be decoded for %s", type(exc).__name__)
        return None


async def retrieve_result(
    app: FastAPI,
    generation_id: str,
    *,
    principal: Principal | None = None,
) -> tuple[Generation, Asset | None]:
    state = app.state
    session_factory = state.session_factory
    settings: Settings = state.settings
    registry: AdapterRegistry = state.registry

    generation, asset, project, active_generation_id = await _load_generation(
        session_factory, generation_id, principal
    )
    async with read_session(session_factory) as session:
        await ensure_legacy_entry_available(session, generation.project_id)
    if generation.status == "succeeded":
        return generation, asset

    async with state.locks.hold(generation.project_id):
        generation, asset, project, active_generation_id = await _load_generation(
            session_factory, generation_id, principal
        )
        if generation.status == "succeeded":
            return generation, asset
        info = evaluate_retrieval(
            generation,
            settings=settings,
            registry=registry,
            project=project,
            active_generation_id=active_generation_id,
        )
        if not info.can_retrieve or info.locator is None:
            _raise_not_retrievable(info)

        role = settings.visual()
        adapter = registry.build_visual(role, state.http)
        if adapter is None:
            raise ApiError(503, _ADAPTER_UNAVAILABLE, _REASON_ADAPTER_UNAVAILABLE)

        timeout_seconds = settings.visual().timeout_seconds
        try:
            async with asyncio.timeout(timeout_seconds):
                fetched = await adapter.fetch_result(info.locator)
        except TimeoutError:
            raise ApiError(504, "RESULT_FETCH_TIMEOUT", "重取结果超时，请稍后重试") from None
        except Exception as exc:
            logger.warning(
                "retrieving result for generation %s failed with %s",
                generation_id,
                type(exc).__name__,
            )
            raise ApiError(502, "RESULT_FETCH_FAILED", "重取结果失败，请稍后重试") from None

        if fetched.state != "succeeded":
            raise ApiError(502, "RESULT_FETCH_FAILED", "重取结果失败，请稍后重试")
        data = _outcome_image(fetched)
        if data is None:
            raise ApiError(502, "RESULT_FETCH_FAILED", "重取结果失败，请稍后重试")
        expected = parse_image_size(generation.image_size)
        if expected is None:
            raise ApiError(502, "RESULT_FETCH_FAILED", "重取结果与提交快照不一致")
        try:
            _normalized, info_image, relative_path, asset_id = await asyncio.to_thread(
                _validate_result_bytes, settings, data, expected
            )
        except Exception:
            raise ApiError(502, "RESULT_FETCH_FAILED", "重取结果无法作为图片保存") from None

        try:
            async with write_transaction(session_factory) as session:
                if principal is not None:
                    await revalidate_principal(session, principal)
                current = await session.get(Generation, generation_id)
                if (
                    current is None
                    or current.status != "failed"
                    or current.error_code != "RESULT_FETCH_FAILED"
                ):
                    raise conflict(
                        "RESULT_NOT_RETRIEVABLE", "生成记录状态已变化，请重新读取"
                    )
                now = format_timestamp(utc_now())
                asset = Asset(
                    id=asset_id,
                    project_id=current.project_id,
                    kind="generated",
                    relative_path=relative_path,
                    mime_type=info_image.mime_type,
                    width=info_image.width,
                    height=info_image.height,
                    byte_size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    created_at=now,
                )
                session.add(asset)
                current.result_asset_id = asset_id
                current.status = "succeeded"
                current.stage = "done"
                current.error_code = None
                current.error_message = None
                current.completed_at = now
                project = await session.get(Project, current.project_id)
                if project is None or (
                    principal is not None
                    and project.owner_account_id != principal.account_id
                ):
                    raise not_found_generation()
                project.updated_at = now
                await session.flush()
        except BaseException:
            await asyncio.to_thread(delete_asset_file, settings, relative_path)
            raise

    async with read_session(session_factory) as session:
        current = await session.get(Generation, generation_id)
        if current is None:
            raise not_found_generation()
        result_asset = (
            await session.get(Asset, current.result_asset_id)
            if current.result_asset_id
            else None
        )
        if result_asset is not None and result_asset.project_id != current.project_id:
            result_asset = None
    return current, result_asset

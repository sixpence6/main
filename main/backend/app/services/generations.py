from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import BriefData, GenerationRequest, ProviderOutcome
from app.adapters.registry import AdapterRegistry
from app.config import LOCAL_RESULT_MAX_BYTES, RoleConfig, Settings
from app.db import ProjectLocks, read_session, write_transaction
from app.errors import ApiError, conflict, not_found_generation
from app.models import Asset, Generation, Project
from app.presenters import brief_from_project, dumps_json, snapshot_json
from app.services.assets import (
    ImageInfo,
    ImageValidationError,
    delete_asset_file,
    inspect_image,
    resolve_asset_file,
    write_asset_file,
)
from app.services.projects import require_project
from app.timeutil import format_timestamp, utc_now

logger = logging.getLogger("app.generations")

_IMAGE_SIZE_PATTERN = re.compile(r"^(\d+)x(\d+)$")


@dataclass
class SubmissionResult:
    generation: Generation
    result_asset: Asset | None
    created: bool


@dataclass(frozen=True)
class ClaimedGeneration:
    id: str
    project_id: str
    version_no: int
    prompt: str
    brief_snapshot_json: str
    image_size: str | None
    reference_asset_id: str | None
    adapter_id: str
    model: str


class ResultValidationError(Exception):
    """A result that cannot be delivered as a verified local image."""


def canonical_request(
    *,
    project_id: str,
    request_id: str,
    confirmed_revision: int,
    acknowledge_present: bool,
    acknowledge_unknown: bool | None,
) -> str:
    payload: dict[str, Any] = {
        "project_id": project_id,
        "request_id": request_id,
        "confirmed_revision": confirmed_revision,
        "acknowledge_unknown_present": acknowledge_present,
    }
    if acknowledge_present:
        payload["acknowledge_unknown"] = acknowledge_unknown
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def request_digest(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalized_base_url(value: str) -> str:
    candidate = value.strip()
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return candidate.rstrip("/")
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", "")
    )


def service_fingerprint(role: RoleConfig) -> str:
    payload = {
        "adapter_id": role.adapter_id,
        "base_url": _normalized_base_url(role.base_url or ""),
        "model": role.model,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def parse_image_size(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    match = _IMAGE_SIZE_PATTERN.fullmatch(value)
    if match is None:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0 or width != height:
        return None
    return width, height


def _require_visual_ready(
    settings: Settings, registry: AdapterRegistry
) -> tuple[RoleConfig, Any]:
    visual = settings.visual()
    if not visual.configured:
        raise ApiError(
            503, "MODEL_NOT_CONFIGURED", "生图模型尚未配置完整，请检查本地配置"
        )
    if not visual.adapter_id or visual.adapter_id not in registry.known_visual_ids():
        raise ApiError(
            503, "ADAPTER_UNAVAILABLE", "生图模型适配器不可用，请检查本地配置"
        )
    capabilities = registry.visual_capabilities(visual.adapter_id)
    if capabilities is None or capabilities.text_to_image is not True:
        raise ApiError(
            503, "MODEL_CAPABILITY_MISMATCH", "生图模型能力未核实或暂不支持文生图"
        )
    if parse_image_size(visual.image_size) is None:
        raise conflict("IMAGE_SIZE_CHANGED", "生成尺寸尚未核实，请重新整理需求")
    media_size = capabilities.supported_image_sizes
    if media_size and visual.image_size not in media_size:
        raise conflict("IMAGE_SIZE_CHANGED", "当前生成尺寸不受支持，请重新整理需求")
    return visual, capabilities


async def _find_by_request(
    session: AsyncSession, project_id: str, request_id: str
) -> Generation | None:
    return await session.scalar(
        select(Generation).where(
            Generation.project_id == project_id,
            Generation.request_id == request_id,
        )
    )


async def _load_result_asset(
    session: AsyncSession, generation: Generation
) -> Asset | None:
    if not generation.result_asset_id:
        return None
    return await session.get(Asset, generation.result_asset_id)


async def _existing_result(
    session_factory: async_sessionmaker[AsyncSession],
    generation: Generation,
    *,
    canonical: str,
) -> SubmissionResult:
    if generation.request_canonical != canonical:
        raise conflict("IDEMPOTENCY_CONFLICT", "请求标识已被不同的原参数使用")
    async with read_session(session_factory) as session:
        result_asset = await _load_result_asset(session, generation)
    return SubmissionResult(
        generation=generation, result_asset=result_asset, created=False
    )


_SUBMIT_RETRY_ATTEMPTS = 100
_SUBMIT_RETRY_DELAY_SECONDS = 0.01


async def _read_contending_result(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    project_id: str,
    request_id: str,
    canonical: str,
) -> SubmissionResult | None:
    """Re-read state after a lost write race without surfacing a raw 500."""

    existing: Generation | None = None
    active: Generation | None = None
    async with read_session(session_factory) as session:
        existing = await _find_by_request(session, project_id, request_id)
        if existing is None:
            active = await session.scalar(
                select(Generation).where(
                    Generation.project_id == project_id,
                    Generation.status.in_(("queued", "running")),
                )
            )
    if existing is not None:
        return await _existing_result(session_factory, existing, canonical=canonical)
    if active is not None:
        if active.request_id == request_id:
            return await _existing_result(session_factory, active, canonical=canonical)
        raise conflict("ACTIVE_GENERATION_EXISTS", "项目有正在进行的生成，请稍后再试")
    return None


async def submit_generation(
    session_factory: async_sessionmaker[AsyncSession],
    locks: ProjectLocks,
    registry: AdapterRegistry,
    settings: Settings,
    *,
    project_id: str,
    request_id: str,
    confirmed_revision: int,
    acknowledge_present: bool,
    acknowledge_unknown: bool | None,
) -> SubmissionResult:
    canonical = canonical_request(
        project_id=project_id,
        request_id=request_id,
        confirmed_revision=confirmed_revision,
        acknowledge_present=acknowledge_present,
        acknowledge_unknown=acknowledge_unknown,
    )

    async with read_session(session_factory) as session:
        existing = await _find_by_request(session, project_id, request_id)
    if existing is not None:
        return await _existing_result(session_factory, existing, canonical=canonical)

    for attempt in range(_SUBMIT_RETRY_ATTEMPTS):
        try:
            async with (
                locks.hold(project_id),
                write_transaction(session_factory) as session,
            ):
                duplicate = await _find_by_request(session, project_id, request_id)
                if duplicate is not None:
                    if duplicate.request_canonical != canonical:
                        raise conflict(
                            "IDEMPOTENCY_CONFLICT", "请求标识已被不同的原参数使用"
                        )
                    result_asset = await _load_result_asset(session, duplicate)
                    return SubmissionResult(duplicate, result_asset, created=False)

                project = await require_project(session, project_id)
                if project.brief_status == "organizing":
                    raise conflict("STALE_ORGANIZATION", "项目正在整理中，请稍后重试")
                active = await session.scalar(
                    select(Generation).where(
                        Generation.project_id == project_id,
                        Generation.status.in_(("queued", "running")),
                    )
                )
                if active is not None:
                    if active.request_id == request_id:
                        if active.request_canonical != canonical:
                            raise conflict(
                                "IDEMPOTENCY_CONFLICT", "请求标识已被不同的原参数使用"
                            )
                        result_asset = await _load_result_asset(session, active)
                        return SubmissionResult(active, result_asset, created=False)
                    raise conflict(
                        "ACTIVE_GENERATION_EXISTS", "项目有正在进行的生成，请稍后再试"
                    )

                if (
                    project.revision is None
                    or project.prompt_revision is None
                    or project.confirmed_revision is None
                    or not (
                        project.revision
                        == project.prompt_revision
                        == project.confirmed_revision
                    )
                    or not project.prompt.strip()
                ):
                    raise conflict("BRIEF_NOT_READY", "需求尚未整理并确认，无法生成")

                visual, capabilities = _require_visual_ready(settings, registry)
                if (
                    project.image_size is None
                    or project.image_size != visual.image_size
                ):
                    raise conflict(
                        "IMAGE_SIZE_CHANGED", "当前生成尺寸与配置不一致，请重新整理需求"
                    )

                reference_asset: Asset | None = None
                if project.reference_asset_id:
                    reference_asset = await session.get(
                        Asset, project.reference_asset_id
                    )
                    if (
                        reference_asset is None
                        or reference_asset.project_id != project_id
                        or reference_asset.kind != "reference"
                    ):
                        raise ApiError(
                            422, "REFERENCE_NOT_SUPPORTED", "参考图不可用于当前项目"
                        )
                    if capabilities.reference_image is not True:
                        raise ApiError(
                            422, "REFERENCE_NOT_SUPPORTED", "当前生图模型不支持参考图"
                        )
                    if (
                        capabilities.reference_mime_types is not None
                        and reference_asset.mime_type
                        not in capabilities.reference_mime_types
                    ):
                        raise ApiError(
                            422,
                            "REFERENCE_NOT_SUPPORTED",
                            "参考图格式不受当前生图模型支持",
                        )
                    provider_limit = capabilities.provider_max_reference_bytes
                    if (
                        provider_limit is not None
                        and reference_asset.byte_size > provider_limit
                    ):
                        raise ApiError(
                            422, "REFERENCE_NOT_SUPPORTED", "参考图大小超过生图模型限制"
                        )
                    if resolve_asset_file(settings, reference_asset) is None:
                        raise ApiError(
                            422, "REFERENCE_NOT_SUPPORTED", "参考图文件不可用"
                        )

                latest = await session.scalar(
                    select(Generation)
                    .where(Generation.project_id == project_id)
                    .order_by(Generation.version_no.desc())
                    .limit(1)
                )
                if (
                    latest is not None
                    and latest.status == "unknown"
                    and acknowledge_unknown is not True
                ):
                    raise conflict(
                        "UNKNOWN_ACK_REQUIRED",
                        "上次生成结果未确认，请明确确认风险后再试",
                    )

                now = format_timestamp(utc_now())
                version_no = 1 if latest is None else latest.version_no + 1
                brief = brief_from_project(project)
                generation = Generation(
                    id=str(uuid4()),
                    project_id=project_id,
                    version_no=version_no,
                    request_id=request_id,
                    request_canonical=canonical,
                    request_digest=request_digest(canonical),
                    confirmed_revision=confirmed_revision,
                    status="queued",
                    stage="waiting_slot",
                    brief_snapshot_json=snapshot_json(brief),
                    prompt=project.prompt,
                    image_size=project.image_size,
                    reference_asset_id=project.reference_asset_id,
                    adapter_id=visual.adapter_id or "",
                    model=visual.model or "",
                    service_fingerprint=service_fingerprint(visual),
                    remote_task_id=None,
                    result_locator_json=None,
                    result_asset_id=None,
                    error_code=None,
                    error_message=None,
                    submit_attempted=0,
                    created_at=now,
                    started_at=None,
                    completed_at=None,
                )
                session.add(generation)
                project.updated_at = now
                await session.flush()
                return SubmissionResult(
                    generation=generation, result_asset=None, created=True
                )

        except IntegrityError:
            recovered = await _read_contending_result(
                session_factory,
                project_id=project_id,
                request_id=request_id,
                canonical=canonical,
            )
            if recovered is not None:
                return recovered
        except OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            recovered = await _read_contending_result(
                session_factory,
                project_id=project_id,
                request_id=request_id,
                canonical=canonical,
            )
            if recovered is not None:
                return recovered
        except ApiError as exc:
            if exc.code != "PROJECT_BUSY":
                raise
            recovered = await _read_contending_result(
                session_factory,
                project_id=project_id,
                request_id=request_id,
                canonical=canonical,
            )
            if recovered is not None:
                return recovered
        if attempt + 1 < _SUBMIT_RETRY_ATTEMPTS:
            await asyncio.sleep(_SUBMIT_RETRY_DELAY_SECONDS)
    raise conflict("PROJECT_BUSY", "项目正在处理其他写操作，请稍后重试")


async def load_generation(
    session_factory: async_sessionmaker[AsyncSession], generation_id: str
) -> tuple[Generation, Asset | None]:
    async with read_session(session_factory) as session:
        generation = await session.get(Generation, generation_id)
        if generation is None:
            raise not_found_generation()
        return generation, await _load_result_asset(session, generation)


def _claim_generation(
    session_factory: async_sessionmaker[AsyncSession], generation_id: str
) -> Any:
    async def claim() -> ClaimedGeneration | None:
        async with write_transaction(session_factory) as session:
            generation = await session.get(Generation, generation_id)
            if generation is None or generation.status != "queued":
                return None
            now = format_timestamp(utc_now())
            generation.status = "running"
            generation.stage = "submitting"
            generation.started_at = now
            generation.submit_attempted = (generation.submit_attempted or 0) + 1
            project = await session.get(Project, generation.project_id)
            if project is not None:
                project.updated_at = now
            await session.flush()
            return ClaimedGeneration(
                id=generation.id,
                project_id=generation.project_id,
                version_no=generation.version_no,
                prompt=generation.prompt,
                brief_snapshot_json=generation.brief_snapshot_json,
                image_size=generation.image_size,
                reference_asset_id=generation.reference_asset_id,
                adapter_id=generation.adapter_id,
                model=generation.model,
            )

    return claim()


async def _set_stage(
    session_factory: async_sessionmaker[AsyncSession], generation_id: str, stage: str
) -> None:
    async with write_transaction(session_factory) as session:
        generation = await session.get(Generation, generation_id)
        if generation is None or generation.status != "running":
            return
        generation.stage = stage
        await session.flush()


async def _finish_generation(
    session_factory: async_sessionmaker[AsyncSession],
    generation_id: str,
    *,
    status: str,
    stage: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    remote_task_id: str | None = None,
    result_locator: str | None = None,
    result_asset_id: str | None = None,
) -> bool:
    async with write_transaction(session_factory) as session:
        generation = await session.get(Generation, generation_id)
        if generation is None or generation.status != "running":
            return False
        now = format_timestamp(utc_now())
        generation.status = status
        if stage is not None:
            generation.stage = stage
        generation.error_code = error_code
        generation.error_message = error_message
        if remote_task_id is not None:
            generation.remote_task_id = remote_task_id
        if result_locator is not None:
            generation.result_locator_json = dumps_json(result_locator)
        if result_asset_id is not None:
            generation.result_asset_id = result_asset_id
        generation.completed_at = now
        project = await session.get(Project, generation.project_id)
        if project is not None:
            project.updated_at = now
        await session.flush()
        return True


def _decode_outcome_image(outcome: ProviderOutcome) -> bytes | None:
    if isinstance(outcome.image_bytes, bytes):
        return outcome.image_bytes
    encoded = outcome.extra.get("image_base64")
    if isinstance(encoded, str):
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ResultValidationError("结果图片内容无法解码") from None
    return None


def _validate_result_bytes(
    settings: Settings,
    data: bytes,
    expected: tuple[int, int],
) -> tuple[ImageInfo, str, str]:
    if len(data) > LOCAL_RESULT_MAX_BYTES:
        raise ResultValidationError("结果图片超过本机大小上限")
    try:
        info = inspect_image(data)
    except ImageValidationError:
        raise ResultValidationError("结果图片无法解析或格式不受支持") from None
    if (info.width, info.height) != expected:
        raise ResultValidationError("结果图片尺寸与提交快照不一致")
    asset_id = str(uuid4())
    extension = "png" if info.mime_type == "image/png" else "jpg"
    relative_path = f"generated/{asset_id}.{extension}"
    try:
        write_asset_file(settings, relative_path, data)
    except OSError:
        raise ResultValidationError("结果图片无法写入本机存储") from None
    return info, relative_path, asset_id


async def _deliver_result(
    app: FastAPI,
    claimed: ClaimedGeneration,
    adapter: Any,
    outcome: ProviderOutcome,
) -> None:
    settings: Settings = app.state.settings
    session_factory = app.state.session_factory
    locator = outcome.result_locator
    remote_task_id = outcome.remote_task_id
    try:
        data = _decode_outcome_image(outcome)
    except ResultValidationError as exc:
        await _finish_generation(
            session_factory,
            claimed.id,
            status="failed",
            error_code="RESULT_FETCH_FAILED",
            error_message=str(exc),
            remote_task_id=remote_task_id,
            result_locator=locator,
        )
        return

    if data is None and locator:
        await _set_stage(session_factory, claimed.id, "fetching_result")
        try:
            fetched = await adapter.fetch_result(locator)
        except Exception:
            logger.exception("fetching result for generation %s failed", claimed.id)
            await _finish_generation(
                session_factory,
                claimed.id,
                status="failed",
                error_code="RESULT_FETCH_FAILED",
                error_message="生图结果下载失败",
                remote_task_id=remote_task_id,
                result_locator=locator,
            )
            return
        remote_task_id = fetched.remote_task_id or remote_task_id
        locator = fetched.result_locator or locator
        if fetched.state == "failed":
            await _finish_generation(
                session_factory,
                claimed.id,
                status="failed",
                error_code=fetched.error_code or "VISUAL_TASK_FAILED",
                error_message=fetched.error_message or "生成任务失败",
                remote_task_id=remote_task_id,
                result_locator=locator,
            )
            return
        if fetched.state == "unknown":
            await _finish_generation(
                session_factory,
                claimed.id,
                status="unknown",
                error_code=fetched.error_code or "VISUAL_OUTCOME_UNKNOWN",
                error_message=fetched.error_message or "生图结果未确认",
                remote_task_id=remote_task_id,
                result_locator=locator,
            )
            return
        if fetched.state != "succeeded":
            await _finish_generation(
                session_factory,
                claimed.id,
                status="failed",
                error_code="RESULT_FETCH_FAILED",
                error_message="生图结果尚未可用",
                remote_task_id=remote_task_id,
                result_locator=locator,
            )
            return
        try:
            data = _decode_outcome_image(fetched)
        except ResultValidationError as exc:
            await _finish_generation(
                session_factory,
                claimed.id,
                status="failed",
                error_code="RESULT_FETCH_FAILED",
                error_message=str(exc),
                remote_task_id=remote_task_id,
                result_locator=locator,
            )
            return

    if data is None:
        await _finish_generation(
            session_factory,
            claimed.id,
            status="failed",
            error_code="RESULT_FETCH_FAILED",
            error_message="生图结果不可用",
            remote_task_id=remote_task_id,
            result_locator=locator,
        )
        return

    expected = parse_image_size(claimed.image_size)
    if expected is None:
        await _finish_generation(
            session_factory,
            claimed.id,
            status="failed",
            error_code="RESULT_FETCH_FAILED",
            error_message="提交快照尺寸无效",
            remote_task_id=remote_task_id,
            result_locator=locator,
        )
        return

    try:
        info, relative_path, asset_id = await asyncio.to_thread(
            _validate_result_bytes, settings, data, expected
        )
    except ResultValidationError as exc:
        await _finish_generation(
            session_factory,
            claimed.id,
            status="failed",
            error_code="RESULT_FETCH_FAILED",
            error_message=str(exc),
            remote_task_id=remote_task_id,
            result_locator=locator,
        )
        return

    try:
        async with write_transaction(session_factory) as session:
            generation = await session.get(Generation, claimed.id)
            if generation is None or generation.status != "running":
                raise LookupError("generation is no longer running")
            now = format_timestamp(utc_now())
            asset = Asset(
                id=asset_id,
                project_id=claimed.project_id,
                kind="result",
                relative_path=relative_path,
                mime_type=info.mime_type,
                width=info.width,
                height=info.height,
                byte_size=len(data),
                created_at=now,
            )
            session.add(asset)
            generation.result_asset_id = asset_id
            generation.status = "succeeded"
            generation.stage = "done"
            generation.error_code = None
            generation.error_message = None
            generation.completed_at = now
            if remote_task_id is not None:
                generation.remote_task_id = remote_task_id
            if locator is not None:
                generation.result_locator_json = dumps_json(locator)
            project = await session.get(Project, claimed.project_id)
            if project is not None:
                project.updated_at = now
            await session.flush()
    except BaseException:
        await asyncio.to_thread(delete_asset_file, settings, relative_path)
        raise


async def _execute_claimed(app: FastAPI, claimed: ClaimedGeneration) -> None:
    settings: Settings = app.state.settings
    registry: AdapterRegistry = app.state.registry
    session_factory = app.state.session_factory
    role = settings.visual()
    adapter = registry.build_visual(role, app.state.http)
    if adapter is None:
        await _finish_generation(
            session_factory,
            claimed.id,
            status="failed",
            error_code="ADAPTER_UNAVAILABLE",
            error_message="生图模型适配器不可用",
        )
        return

    reference_path: Path | None = None
    reference_mime_type: str | None = None
    if claimed.reference_asset_id:
        async with read_session(session_factory) as session:
            reference = await session.get(Asset, claimed.reference_asset_id)
        if (
            reference is None
            or reference.project_id != claimed.project_id
            or reference.kind != "reference"
        ):
            await _finish_generation(
                session_factory,
                claimed.id,
                status="failed",
                error_code="REFERENCE_FILE_MISSING",
                error_message="参考图文件不可用",
            )
            return
        reference_path = resolve_asset_file(settings, reference)
        reference_mime_type = reference.mime_type
        if reference_path is None:
            await _finish_generation(
                session_factory,
                claimed.id,
                status="failed",
                error_code="REFERENCE_FILE_MISSING",
                error_message="参考图文件不可用",
            )
            return

    try:
        snapshot = BriefData(**json.loads(claimed.brief_snapshot_json))
    except (TypeError, ValueError):
        await _finish_generation(
            session_factory,
            claimed.id,
            status="failed",
            error_code="RESULT_FETCH_FAILED",
            error_message="提交快照不可用",
        )
        return

    request = GenerationRequest(
        project_id=claimed.project_id,
        version_no=claimed.version_no,
        prompt=claimed.prompt,
        brief=snapshot,
        image_size=claimed.image_size,
        reference_path=reference_path,
        reference_mime_type=reference_mime_type,
    )
    outcome = await adapter.submit(request)
    if outcome.state == "failed":
        await _finish_generation(
            session_factory,
            claimed.id,
            status="failed",
            error_code=outcome.error_code or "VISUAL_TASK_FAILED",
            error_message=outcome.error_message or "生成任务失败",
            remote_task_id=outcome.remote_task_id,
            result_locator=outcome.result_locator,
        )
        return
    if outcome.state == "unknown":
        await _finish_generation(
            session_factory,
            claimed.id,
            status="unknown",
            error_code=outcome.error_code or "VISUAL_OUTCOME_UNKNOWN",
            error_message=outcome.error_message or "生图结果未确认",
            remote_task_id=outcome.remote_task_id,
            result_locator=outcome.result_locator,
        )
        return
    if outcome.state == "pending":
        await _finish_generation(
            session_factory,
            claimed.id,
            status="unknown",
            error_code="VISUAL_OUTCOME_UNKNOWN",
            error_message="生图结果尚未确认",
            remote_task_id=outcome.remote_task_id,
            result_locator=outcome.result_locator,
        )
        return
    await _deliver_result(app, claimed, adapter, outcome)


async def execute_generation(app: FastAPI, generation_id: str) -> None:
    slots: asyncio.Semaphore = app.state.generation_slots
    session_factory = app.state.session_factory
    async with slots:
        claimed = await _claim_generation(session_factory, generation_id)
        if claimed is None:
            return
        settings: Settings = app.state.settings
        try:
            async with asyncio.timeout(settings.visual().timeout_seconds):
                await _execute_claimed(app, claimed)
        except TimeoutError:
            await _finish_generation(
                session_factory,
                generation_id,
                status="unknown",
                error_code="VISUAL_OUTCOME_UNKNOWN",
                error_message="生图等待超时，结果未确认",
            )
        except Exception:
            logger.exception("generation %s failed or became uncertain", generation_id)
            await _finish_generation(
                session_factory,
                generation_id,
                status="unknown",
                error_code="VISUAL_OUTCOME_UNKNOWN",
                error_message="生图结果未确认",
            )


async def mark_scheduling_failed(
    session_factory: async_sessionmaker[AsyncSession], generation_id: str
) -> None:
    async with write_transaction(session_factory) as session:
        generation = await session.get(Generation, generation_id)
        if generation is None or generation.status != "queued":
            return
        now = format_timestamp(utc_now())
        generation.status = "unknown"
        generation.error_code = "BACKGROUND_SCHEDULE_FAILED"
        generation.error_message = "后台任务调度失败，结果未确认"
        generation.completed_at = now
        project = await session.get(Project, generation.project_id)
        if project is not None:
            project.updated_at = now
        await session.flush()


def schedule_generation(app: FastAPI, generation_id: str) -> None:
    tasks: set[asyncio.Task[None]] = app.state.generation_tasks
    task = asyncio.create_task(
        execute_generation(app, generation_id), name=f"generation-{generation_id}"
    )
    tasks.add(task)

    def _done(finished: asyncio.Task[None]) -> None:
        tasks.discard(finished)
        if finished.cancelled():
            return
        try:
            finished.result()
        except Exception:
            logger.exception("background generation task %s crashed", generation_id)

    task.add_done_callback(_done)

from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.config import REFERENCE_MULTIPART_OVERHEAD_BYTES
from app.errors import ApiError, validation_error
from app.models import Asset
from app.presenters import project_view
from app.schemas import ERROR_RESPONSE, ProjectView
from app.services.assets import (
    ImageValidationError,
    attach_reference,
    download_name,
    effective_reference_max_bytes,
    inspect_image,
    remove_reference,
    require_asset_file,
)

router = APIRouter(tags=["assets"])

_REVISION_PATTERN = re.compile(r"^\d+$")


async def _parse_multipart(request: Request, limit: int):
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise validation_error("请求必须使用 multipart/form-data")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ApiError(413, "UPLOAD_TOO_LARGE", "参考图文件过大")

    async def limited_stream():
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > limit:
                raise ApiError(413, "UPLOAD_TOO_LARGE", "参考图文件过大")
            yield chunk

    parser = MultiPartParser(
        request.headers,
        limited_stream(),
        max_files=2,
        max_fields=2,
        max_part_size=limit,
    )
    try:
        return await parser.parse()
    except MultiPartException:
        raise ApiError(422, "INVALID_REFERENCE_IMAGE", "参考图上传格式不正确") from None


@router.post(
    "/api/projects/{project_id}/reference",
    response_model=ProjectView,
    responses={
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        413: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
    },
)
async def upload_reference(project_id: str, request: Request) -> ProjectView:
    state = request.app.state
    settings = state.settings
    max_bytes = effective_reference_max_bytes(settings, state.registry)
    form = await _parse_multipart(request, max_bytes + REFERENCE_MULTIPART_OVERHEAD_BYTES)
    try:
        items = form.multi_items()
        files = [value for key, value in items if key == "file"]
        revisions = [value for key, value in items if key == "expected_revision"]
        keys = {key for key, _value in items}
        if (
            len(items) != 2
            or keys != {"file", "expected_revision"}
            or len(files) != 1
            or len(revisions) != 1
            or not isinstance(files[0], UploadFile)
            or isinstance(revisions[0], UploadFile)
        ):
            raise validation_error("仅支持一个参考图文件与 expected_revision")
        revision_text = str(revisions[0]).strip()
        if not _REVISION_PATTERN.fullmatch(revision_text):
            raise validation_error("expected_revision 不合法")
        expected_revision = int(revision_text)
        data = await files[0].read()
    finally:
        await form.close()

    if len(data) > max_bytes:
        raise ApiError(413, "UPLOAD_TOO_LARGE", "参考图文件过大")
    try:
        image = await asyncio.to_thread(inspect_image, data)
    except ImageValidationError as exc:
        raise ApiError(422, exc.code, exc.message) from None

    result = await attach_reference(
        state.session_factory,
        state.locks,
        settings,
        project_id=project_id,
        expected_revision=expected_revision,
        data=data,
        image=image,
    )
    return project_view(result.project, result.asset, settings.resolved_data_dir())


@router.delete(
    "/api/projects/{project_id}/reference",
    response_model=ProjectView,
    responses={404: ERROR_RESPONSE, 409: ERROR_RESPONSE, 422: ERROR_RESPONSE},
)
async def delete_reference(
    project_id: str,
    request: Request,
    expected_revision: int = Query(ge=0),
) -> ProjectView:
    state = request.app.state
    result = await remove_reference(
        state.session_factory,
        state.locks,
        project_id=project_id,
        expected_revision=expected_revision,
    )
    return project_view(result.project, result.asset, state.settings.resolved_data_dir())


@router.get(
    "/api/assets/{asset_id}",
    response_class=FileResponse,
    responses={
        200: {
            "description": "图片文件",
            "content": {"image/png": {}, "image/jpeg": {}},
        },
        404: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
    },
)
async def read_asset(
    asset_id: str,
    request: Request,
    download: bool = Query(default=False),
) -> FileResponse:
    state = request.app.state
    async with state.session_factory() as session:
        asset = await session.get(Asset, asset_id)
        if asset is None:
            raise ApiError(404, "ASSET_NOT_FOUND", "资源不存在")
        filename = await download_name(session, asset)
    path = require_asset_file(state.settings, asset)
    return FileResponse(
        path,
        media_type=asset.mime_type,
        filename=filename if download else None,
        content_disposition_type="attachment" if download else "inline",
    )

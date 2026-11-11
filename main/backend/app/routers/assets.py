from __future__ import annotations

import asyncio
import re
from contextlib import suppress

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.auth.principal import require_principal
from app.config import REFERENCE_MULTIPART_OVERHEAD_BYTES
from app.errors import ApiError, validation_error
from app.models import Asset, Project
from app.operator_audit import record_operator_event_safely
from app.ownership import require_project_owner, revalidate_principal
from app.presenters import asset_view, project_view
from app.request_source import client_ip
from app.schemas import ERROR_RESPONSE, ProjectView, StagedMessageAttachmentView
from app.services.assets import (
    ImageValidationError,
    attach_reference,
    download_name,
    effective_reference_max_bytes,
    inspect_image,
    remove_reference,
    require_asset_file,
)
from app.services.message_attachments import (
    discard_staged_attachment,
    preview_url,
    stage_message_attachment,
)
from app.services.retention import asset_is_retention_expired
from app.workflow import templates
from app.workflow.access import (
    can_access_asset,
    load_project_access_policy,
)

router = APIRouter(tags=["assets"])

_REVISION_PATTERN = re.compile(r"^\d+$")


async def _sync_reference_workflow(app, project_id: str, *, removed: bool, principal=None) -> None:
    """Keep an existing v2 run aligned with an upload or an explicit removal.

    Fresh projects keep the legacy upload-only behaviour until the client asks
    for S02; this avoids creating a workflow run that would silently block the
    v1 message endpoint for clients that never entered v2. Once a run exists,
    a replacement immediately invalidates old reports and runs the new visual
    analysis exactly once. Any technical failure is reflected in the run state
    and never fails the already-persisted upload.
    """
    from app.workflow import state as workflow_state
    from app.workflow.stages import s02

    async with app.state.locks.hold(project_id):
        ctx = await workflow_state.load_context(app, project_id, principal=principal)
        if ctx.run is None:
            return
        if ctx.run.status == "completed":
            # A published version is immutable history. The new file becomes
            # the current project reference; the next feedback version will
            # decide whether it needs a fresh S02 analysis.
            return
        if removed:
            await s02.prepare_after_reference_removal(ctx)
            return
        await s02.prepare_after_reference_change(ctx)
        if not ctx.project.reference_asset_id:
            return
        # The upload itself succeeded; the workflow view carries the fixed
        # failure template and the user's input is preserved.
        with suppress(ApiError):
            await s02.analyze(ctx)


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
        401: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        413: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
    },
)
async def upload_reference(project_id: str, request: Request) -> ProjectView:
    state = request.app.state
    principal = require_principal(request)
    # 归属检查先于解析和写文件，避免为无权项目留下孤立资产文件。
    await require_project_owner(state.session_factory, project_id, principal)
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

    # 旧项目的写入口必须在落库和写文件之前暂停，不能依赖前端禁用。
    # 已启动 v2 流程的项目允许参考图变更（T04 的 S02 输入），但仍由
    # access.py 的统一策略决定旧产物访问；这里只放行参考图写入口。
    async with state.session_factory() as session:
        policy = await load_project_access_policy(session, project_id)
        if policy.old_entries_paused and not policy.has_workflow_run:
            raise ApiError(409, "LEGACY_FLOW_DISABLED", templates.render("LEGACY_FLOW_DISABLED"))

    result = await attach_reference(
        state.session_factory,
        state.locks,
        settings,
        project_id=project_id,
        expected_revision=expected_revision,
        data=data,
        image=image,
        principal=principal,
    )
    await record_operator_event_safely(
        request.app,
        event_type="reference_uploaded",
        principal=principal,
        project_id=project_id,
        source=client_ip(request),
        detail={
            "mime_type": image.mime_type,
            "width": image.width,
            "height": image.height,
            "bytes": len(data),
        },
    )
    await _sync_reference_workflow(request.app, project_id, removed=False, principal=principal)
    async with state.session_factory() as session:
        policy = await load_project_access_policy(session, project_id)
    return project_view(result.project, result.asset, settings.resolved_data_dir(), policy=policy)


@router.delete(
    "/api/projects/{project_id}/reference",
    response_model=ProjectView,
    responses={
        401: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
    },
)
async def delete_reference(
    project_id: str,
    request: Request,
    expected_revision: int = Query(ge=0),
) -> ProjectView:
    state = request.app.state
    principal = require_principal(request)
    await require_project_owner(state.session_factory, project_id, principal)
    async with state.session_factory() as session:
        policy = await load_project_access_policy(session, project_id)
        if policy.old_entries_paused and not policy.has_workflow_run:
            raise ApiError(409, "LEGACY_FLOW_DISABLED", templates.render("LEGACY_FLOW_DISABLED"))
    result = await remove_reference(
        state.session_factory,
        state.locks,
        project_id=project_id,
        expected_revision=expected_revision,
        principal=principal,
    )
    await record_operator_event_safely(
        request.app,
        event_type="reference_removed",
        principal=principal,
        project_id=project_id,
        source=client_ip(request),
        detail={"had_reference": result.removed},
    )
    await _sync_reference_workflow(request.app, project_id, removed=True, principal=principal)
    async with state.session_factory() as session:
        policy = await load_project_access_policy(session, project_id)
    return project_view(
        result.project,
        result.asset,
        state.settings.resolved_data_dir(),
        policy=policy,
    )



@router.post(
    "/api/projects/{project_id}/message-attachments",
    response_model=StagedMessageAttachmentView,
    responses={
        401: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        413: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
    },
)
async def stage_message_attachment_endpoint(
    project_id: str, request: Request
) -> StagedMessageAttachmentView:
    state = request.app.state
    principal = require_principal(request)
    await require_project_owner(state.session_factory, project_id, principal)
    settings = state.settings
    max_bytes = effective_reference_max_bytes(settings, state.registry)
    form = await _parse_multipart(request, max_bytes + REFERENCE_MULTIPART_OVERHEAD_BYTES)
    try:
        items = form.multi_items()
        files = [value for key, value in items if key == "file"]
        if (
            len(items) != 1
            or {key for key, _value in items} != {"file"}
            or len(files) != 1
            or not isinstance(files[0], UploadFile)
        ):
            raise validation_error("仅支持一个待发送图片文件")
        data = await files[0].read()
    finally:
        await form.close()
    if len(data) > max_bytes:
        raise ApiError(413, "UPLOAD_TOO_LARGE", "参考图文件过大")
    try:
        image = await asyncio.to_thread(inspect_image, data)
    except ImageValidationError as exc:
        raise ApiError(422, exc.code, exc.message) from None
    staged = await stage_message_attachment(
        state.session_factory,
        state.locks,
        settings,
        project_id=project_id,
        data=data,
        image=image,
        principal=principal,
    )
    await record_operator_event_safely(
        request.app,
        event_type="message_attachment_staged",
        principal=principal,
        project_id=project_id,
        source=client_ip(request),
        detail={
            "asset_id": staged.asset.id,
            "content_version": staged.content_version,
            "mime_type": image.mime_type,
            "width": image.width,
            "height": image.height,
            "byte_size": len(data),
        },
    )
    return StagedMessageAttachmentView(
        asset=asset_view(staged.asset, settings.resolved_data_dir()),
        usage="none",
        content_version=staged.content_version,
        sha256=staged.content_version,
        preview_url=preview_url(staged.asset.id),
    )


@router.delete(
    "/api/projects/{project_id}/message-attachments/{asset_id}",
    responses={
        401: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
    },
)
async def discard_message_attachment_endpoint(
    project_id: str, asset_id: str, request: Request
) -> dict[str, object]:
    state = request.app.state
    principal = require_principal(request)
    await require_project_owner(state.session_factory, project_id, principal)
    await discard_staged_attachment(
        state.session_factory,
        state.locks,
        state.settings,
        project_id=project_id,
        asset_id=asset_id,
        principal=principal,
    )
    await record_operator_event_safely(
        request.app,
        event_type="message_attachment_discarded",
        principal=principal,
        project_id=project_id,
        source=client_ip(request),
        detail={"asset_id": asset_id},
    )
    return {"asset_id": asset_id, "discarded": True}


@router.get(
    "/api/assets/{asset_id}",
    response_class=FileResponse,
    responses={
        200: {
            "description": "图片文件",
            "content": {"image/png": {}, "image/jpeg": {}},
        },
        401: ERROR_RESPONSE,
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
    principal = require_principal(request)
    async with state.session_factory() as session:
        asset = await session.get(Asset, asset_id)
        if asset is None:
            raise ApiError(404, "ASSET_NOT_FOUND", "资源不存在")
        await revalidate_principal(session, principal)
        project = await session.get(Project, asset.project_id)
        if project is None or project.owner_account_id != principal.account_id:
            raise ApiError(404, "ASSET_NOT_FOUND", "资源不存在")
        # T20：即使候选文件尚未被物理清理，到期候选也不能通过任何资产出口读取。
        if await asset_is_retention_expired(request.app, asset):
            raise ApiError(409, "ASSET_UNAVAILABLE", templates.render("ASSET_UNAVAILABLE"))
        decision = await can_access_asset(session, state.settings, asset, download=download)
        if not decision.allow:
            code = decision.reason_code or "ASSET_UNAVAILABLE"
            if code == "ASSET_FILE_MISSING":
                # 文件缺失是资源不存在，不是内容未获批准；保持既有外部契约。
                raise ApiError(404, code, "资源文件不存在")
            message = (
                templates.render("LEGACY_UNREVIEWED")
                if code == "LEGACY_UNREVIEWED"
                else templates.render("ASSET_UNAVAILABLE")
            )
            # 门禁拒绝发生在读取文件之前，绝不返回任何旧产物字节。
            raise ApiError(409, code, message)
        filename = await download_name(session, asset)
    path = require_asset_file(state.settings, asset)
    return FileResponse(
        path,
        media_type=asset.mime_type,
        filename=filename if download else None,
        content_disposition_type="attachment" if download else "inline",
    )

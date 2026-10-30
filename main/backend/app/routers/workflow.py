from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.errors import ApiError, conflict
from app.schemas import ERROR_RESPONSE
from app.workflow import dispatch, state
from app.workflow.stages.s01 import validate_text
from app.workflow.schemas_workflow import WorkflowActionRequest, WorkflowView

router = APIRouter(prefix="/api/projects/{project_id}/workflow", tags=["workflow"])


@router.get(
    "",
    response_model=WorkflowView,
    responses={404: ERROR_RESPONSE, 422: ERROR_RESPONSE},
)
async def get_workflow(project_id: str, request: Request) -> WorkflowView:
    ctx = await state.load_context(request.app, project_id)
    return await state.build_workflow_view(ctx)


@router.post(
    "/actions/{action_id}",
    response_model=WorkflowView,
    responses={
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def run_action(
    project_id: str,
    action_id: str,
    body: WorkflowActionRequest,
    request: Request,
) -> WorkflowView:
    if dispatch.owner(action_id) is None:
        raise ApiError(404, "ACTION_NOT_FOUND", "操作不存在")

    app = request.app
    async with app.state.locks.hold(project_id):
        ctx = await state.load_context(app, project_id)
        if ctx.project.revision != body.expected_revision:
            raise conflict("REVISION_CONFLICT", "项目已被其他操作更新，请刷新后重试")

        data: dict[str, Any] = dict(body.payload or {})
        if body.text is not None:
            data["text"] = body.text

        if ctx.run is None:
            if action_id != "submit_request":
                raise ApiError(
                    409, "ACTION_NOT_AVAILABLE", "当前状态不支持此操作，请先完成提示的步骤。"
                )
            # Validate before creating the run so a malformed first message
            # cannot leave a workflow with no request and no available action.
            validate_text(data)
            await state.create_run(ctx, "S01")
        else:
            module = dispatch.owner_for_stage(ctx.run.stage)
            if module is None or action_id not in module.ACTIONS:
                raise ApiError(
                    409, "ACTION_NOT_AVAILABLE", "当前状态不支持此操作，请先完成提示的步骤。"
                )
        await dispatch.handle_action(ctx, action_id, data)
        fresh = await state.reload_context(ctx)
        return await state.build_workflow_view(fresh)

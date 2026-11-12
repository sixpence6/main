from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.auth.principal import require_principal
from app.errors import ApiError, conflict
from app.operator_audit import record_operator_event_safely
from app.request_source import client_ip
from app.schemas import ERROR_RESPONSE
from app.workflow import dispatch, state
from app.workflow.schemas_workflow import WorkflowActionRequest, WorkflowView
from app.workflow.stages.s01 import validate_text

router = APIRouter(prefix="/api/projects/{project_id}/workflow", tags=["workflow"])


@router.get(
    "",
    response_model=WorkflowView,
    responses={401: ERROR_RESPONSE, 404: ERROR_RESPONSE, 422: ERROR_RESPONSE},
)
async def get_workflow(project_id: str, request: Request) -> WorkflowView:
    principal = require_principal(request)
    ctx = await state.load_context(request.app, project_id, principal=principal)
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
    principal = require_principal(request)
    source = client_ip(request)
    async with app.state.locks.hold(project_id):
        ctx = await state.load_context(app, project_id, principal=principal)
        if ctx.project.revision != body.expected_revision:
            raise conflict("REVISION_CONFLICT", "项目已被其他操作更新，请刷新后重试")

        data: dict[str, Any] = dict(body.payload or {})
        if body.text is not None:
            data["text"] = body.text

        if ctx.run is None:
            if action_id == "analyze_reference":
                # Independent upload path: a valid reference file is the only
                # requirement for opening S02. No text is needed and no model
                # call happens until this explicit action.
                if not ctx.project.reference_asset_id:
                    raise ApiError(
                        409,
                        "ACTION_NOT_AVAILABLE",
                        "当前状态不支持此操作，请先完成提示的步骤。",
                    )
                await state.create_run(ctx, "S02")
            elif action_id == "submit_request":
                # Validate before creating the run so a malformed first message
                # cannot leave a workflow with no request and no available action.
                validate_text(data)
                await state.create_run(ctx, "S01")
            elif action_id in {"review_legacy_item", "retrieve_original_result"}:
                # A legacy project has no workflow run until the user explicitly
                # selects one historical target; s11 creates the review run.
                pass
            else:
                raise ApiError(
                    409, "ACTION_NOT_AVAILABLE", "当前状态不支持此操作，请先完成提示的步骤。"
                )
        else:
            module = dispatch.owner_for_stage(ctx.run.stage)
            if action_id not in {"review_legacy_item", "retrieve_original_result"} and (
                module is None or action_id not in module.ACTIONS
            ):
                # A repeated confirmation may arrive after the first click has
                # already advanced S06 to S07. Replaying it through S06 is safe
                # only while S07 is current and an active requirements-card
                # approval exists; S06 then verifies the exact bound snapshot.
                replay_confirmation = (
                    action_id == "confirm_requirements"
                    and ctx.run.stage == "S07"
                    and await state.latest_approval(ctx, "requirements_card") is not None
                )
                # The requirements card may also be revised after confirmation.
                # S06 validates that the active confirmation still matches the
                # current project revision and card before starting the S03 rerun.
                revise_after_confirmation = (
                    action_id == "revise_requirements"
                    and ctx.run.stage == "S07"
                    and await state.latest_approval(ctx, "requirements_card") is not None
                )
                # Technical failures keep the failed stage current; only the
                # explicit S12 continue action may reopen that same checkpoint.
                recovery_action = dispatch.recovery_action_available(ctx, action_id)
                if not (replay_confirmation or revise_after_confirmation or recovery_action
                        or action_id == "confirm_prompt"):
                    raise ApiError(
                        409, "ACTION_NOT_AVAILABLE", "当前状态不支持此操作，请先完成提示的步骤。"
                    )
        audit_detail: dict[str, Any] = {
            "action_id": action_id,
            "expected_revision": body.expected_revision,
        }
        if ctx.run is not None:
            audit_detail["run_id"] = ctx.run.id
            audit_detail["stage"] = ctx.run.stage
        try:
            await dispatch.handle_action(ctx, action_id, data)
        except ApiError as exc:
            audit_detail["stage"] = ctx.run.stage if ctx.run is not None else None
            await record_operator_event_safely(
                app,
                event_type="workflow_action",
                principal=principal,
                project_id=project_id,
                result="denied" if exc.status_code in {401, 403} else "failure",
                reason_code=exc.code,
                source=source,
                detail=audit_detail,
            )
            raise
        except Exception:
            await record_operator_event_safely(
                app,
                event_type="workflow_action",
                principal=principal,
                project_id=project_id,
                result="failure",
                reason_code="INTERNAL",
                source=source,
                detail=audit_detail,
            )
            raise
        await record_operator_event_safely(
            app,
            event_type="workflow_action",
            principal=principal,
            project_id=project_id,
            result="success",
            source=source,
            detail=audit_detail,
        )
        fresh = await state.reload_context(ctx)
        return await state.build_workflow_view(fresh)

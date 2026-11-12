from __future__ import annotations

from fastapi import APIRouter, Request

from app.auth.principal import require_principal
from app.ownership import require_project_owner
from app.presenters import project_view
from app.schemas import ERROR_RESPONSE, ProjectView, UpdateBriefRequest
from app.services.briefs import update_brief
from app.workflow.access import legacy_flow_disabled, load_project_access_policy

router = APIRouter(prefix="/api/projects", tags=["briefs"])


@router.patch(
    "/{project_id}/brief",
    response_model=ProjectView,
    responses={401: ERROR_RESPONSE, 404: ERROR_RESPONSE, 409: ERROR_RESPONSE, 422: ERROR_RESPONSE},
)
async def patch_brief(
    project_id: str, payload: UpdateBriefRequest, request: Request
) -> ProjectView:
    state = request.app.state
    principal = require_principal(request)
    changes = {
        name: getattr(payload, name)
        for name in (
            "product_description",
            "purpose",
            "style",
            "composition",
            "text_content",
        )
        if name in payload.model_fields_set
    }
    result = await update_brief(
        state.session_factory,
        state.locks,
        project_id=project_id,
        expected_revision=payload.expected_revision,
        changes=changes,  # type: ignore[arg-type]
        principal=principal,
    )
    async with state.session_factory() as session:
        policy = await load_project_access_policy(session, project_id)
    return project_view(
        result.project,
        result.reference_asset,
        state.settings.resolved_data_dir(),
        policy=policy,
    )


@router.post(
    "/{project_id}/confirm",
    response_model=None,
    deprecated=True,
    responses={
        401: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
    },
)
async def post_confirm(project_id: str, request: Request) -> None:
    """Retired v1 confirmation entrance: authorize nothing, mutate nothing."""
    state = request.app.state
    principal = require_principal(request)
    await require_project_owner(state.session_factory, project_id, principal)
    raise legacy_flow_disabled()

from __future__ import annotations

from fastapi import APIRouter, Request

from app.presenters import project_view
from app.schemas import ERROR_RESPONSE, ConfirmRequest, ProjectView, UpdateBriefRequest
from app.services.briefs import confirm_brief, update_brief

router = APIRouter(prefix="/api/projects", tags=["briefs"])


def _settings_and_registry(request: Request):
    return request.app.state.settings, request.app.state.registry


@router.patch(
    "/{project_id}/brief",
    response_model=ProjectView,
    responses={404: ERROR_RESPONSE, 409: ERROR_RESPONSE, 422: ERROR_RESPONSE},
)
async def patch_brief(
    project_id: str, payload: UpdateBriefRequest, request: Request
) -> ProjectView:
    state = request.app.state
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
    )
    return project_view(result.project, result.reference_asset, state.settings.resolved_data_dir())


@router.post(
    "/{project_id}/confirm",
    response_model=ProjectView,
    responses={404: ERROR_RESPONSE, 409: ERROR_RESPONSE, 422: ERROR_RESPONSE},
)
async def post_confirm(project_id: str, payload: ConfirmRequest, request: Request) -> ProjectView:
    state = request.app.state
    settings, registry = _settings_and_registry(request)
    result = await confirm_brief(
        state.session_factory,
        state.locks,
        settings,
        registry,
        project_id=project_id,
        revision=payload.revision,
    )
    return project_view(result.project, result.reference_asset, settings.resolved_data_dir())

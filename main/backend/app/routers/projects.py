from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.presenters import generation_view, message_view, project_view
from app.schemas import (
    ERROR_RESPONSE,
    CreateProjectRequest,
    ProjectDetailResponse,
    ProjectListResponse,
    ProjectView,
)
from app.services.projects import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    create_project,
    decode_cursor,
    get_project_detail,
    list_projects,
)
from app.services.retrieval import evaluate_generations_retrieval

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectView,
    status_code=201,
    responses={422: ERROR_RESPONSE},
)
async def create_project_endpoint(request: Request, _payload: CreateProjectRequest) -> ProjectView:
    state = request.app.state
    project = await create_project(state.session_factory)
    return project_view(project, None, state.settings.resolved_data_dir())


@router.get("", response_model=ProjectListResponse, responses={422: ERROR_RESPONSE})
async def list_projects_endpoint(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=MAX_LIST_LIMIT)] = DEFAULT_LIST_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> ProjectListResponse:
    state = request.app.state
    items, next_cursor = await list_projects(
        state.session_factory, limit=limit, cursor=decode_cursor(cursor)
    )
    return ProjectListResponse(items=items, next_cursor=next_cursor)


@router.get(
    "/{project_id}",
    response_model=ProjectDetailResponse,
    responses={404: ERROR_RESPONSE, 422: ERROR_RESPONSE},
)
async def get_project_endpoint(request: Request, project_id: str) -> ProjectDetailResponse:
    state = request.app.state
    detail = await get_project_detail(state.session_factory, project_id)
    data_dir = state.settings.resolved_data_dir()
    retrieval = evaluate_generations_retrieval(
        detail.generations,
        settings=state.settings,
        registry=state.registry,
        project=detail.project,
    )
    generation_views = []
    for generation in detail.generations:
        info = retrieval[generation.id]
        generation_views.append(
            generation_view(
                generation,
                detail.result_assets.get(generation.result_asset_id or ""),
                data_dir,
                can_retrieve_result=info.can_retrieve,
                retrieval_unavailable_reason=info.reason,
                policy=detail.policy,
            )
        )
    return ProjectDetailResponse(
        project=project_view(
            detail.project, detail.reference_asset, data_dir, policy=detail.policy
        ),
        messages=[message_view(message, policy=detail.policy) for message in detail.messages],
        generations=generation_views,
    )

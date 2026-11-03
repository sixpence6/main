from __future__ import annotations

from fastapi import APIRouter, Request

from app.auth.principal import require_principal
from app.ownership import require_project_owner
from app.presenters import generation_view
from app.schemas import ERROR_RESPONSE, EmptyBody, GenerationView
from app.services.generations import load_generation
from app.services.retention import generation_retention
from app.services.retrieval import load_retrieval_info, retrieve_result
from app.workflow.access import legacy_flow_disabled, load_project_access_policy

router = APIRouter(tags=["generations"])


@router.post(
    "/api/projects/{project_id}/generations",
    response_model=None,
    deprecated=True,
    responses={
        401: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
    },
)
async def submit_generation_endpoint(request: Request, project_id: str) -> None:
    """Retired v1 generation entrance: authorize nothing, call no model."""
    state = request.app.state
    principal = require_principal(request)
    await require_project_owner(state.session_factory, project_id, principal)
    raise legacy_flow_disabled()


@router.get(
    "/api/generations/{generation_id}",
    response_model=GenerationView,
    responses={404: ERROR_RESPONSE, 422: ERROR_RESPONSE},
)
async def get_generation_endpoint(request: Request, generation_id: str) -> GenerationView:
    state = request.app.state
    principal = require_principal(request)
    generation, result_asset = await load_generation(
        state.session_factory, generation_id, principal=principal
    )
    async with state.session_factory() as session:
        policy = await load_project_access_policy(session, generation.project_id)
    info = await load_retrieval_info(
        state.session_factory, state.settings, state.registry, generation
    )
    retention = await generation_retention(request.app, generation)
    return generation_view(
        generation,
        result_asset,
        state.settings.resolved_data_dir(),
        can_retrieve_result=info.can_retrieve,
        retrieval_unavailable_reason=info.reason,
        policy=policy,
        retention=retention,
    )


@router.post(
    "/api/generations/{generation_id}/retrieve-result",
    response_model=GenerationView,
    responses={
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def retrieve_generation_result_endpoint(
    request: Request, generation_id: str, _payload: EmptyBody
) -> GenerationView:
    state = request.app.state
    principal = require_principal(request)
    generation, result_asset = await retrieve_result(
        request.app, generation_id, principal=principal
    )
    async with state.session_factory() as session:
        policy = await load_project_access_policy(session, generation.project_id)
    info = await load_retrieval_info(
        state.session_factory, state.settings, state.registry, generation
    )
    return generation_view(
        generation,
        result_asset,
        state.settings.resolved_data_dir(),
        can_retrieve_result=info.can_retrieve,
        retrieval_unavailable_reason=info.reason,
        policy=policy,
    )

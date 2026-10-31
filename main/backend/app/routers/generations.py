from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.auth.principal import require_principal
from app.presenters import generation_view
from app.schemas import (
    ERROR_RESPONSE,
    CreateGenerationRequest,
    EmptyBody,
    GenerationResponse,
    GenerationView,
)
from app.services.generations import (
    load_generation,
    submit_generation_and_schedule,
)
from app.services.retention import generation_retention
from app.services.retrieval import load_retrieval_info, retrieve_result
from app.workflow.access import load_project_access_policy

router = APIRouter(tags=["generations"])


@router.post(
    "/api/projects/{project_id}/generations",
    response_model=GenerationResponse,
    status_code=202,
    responses={
        200: {"model": GenerationResponse, "description": "幂等重放，返回原生成记录"},
        401: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def submit_generation_endpoint(
    request: Request,
    project_id: str,
    payload: CreateGenerationRequest,
    response: Response,
) -> GenerationResponse:
    state = request.app.state
    principal = require_principal(request)
    result = await submit_generation_and_schedule(
        request.app,
        project_id=project_id,
        request_id=str(payload.request_id),
        confirmed_revision=payload.confirmed_revision,
        acknowledge_present="acknowledge_unknown" in payload.model_fields_set,
        acknowledge_unknown=payload.acknowledge_unknown,
        principal=principal,
    )
    if not result.created:
        response.status_code = 200
    async with state.session_factory() as session:
        policy = await load_project_access_policy(session, project_id)
    return GenerationResponse(
        generation=generation_view(
            result.generation,
            result.result_asset,
            state.settings.resolved_data_dir(),
            policy=policy,
        )
    )


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

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.presenters import generation_view
from app.schemas import CreateGenerationRequest, EmptyBody, GenerationResponse, GenerationView
from app.services.generations import (
    load_generation,
    submit_generation_and_schedule,
)
from app.services.retrieval import load_retrieval_info, retrieve_result

router = APIRouter(tags=["generations"])


@router.post(
    "/api/projects/{project_id}/generations",
    response_model=GenerationResponse,
    status_code=202,
)
async def submit_generation_endpoint(
    request: Request,
    project_id: str,
    payload: CreateGenerationRequest,
    response: Response,
) -> GenerationResponse:
    state = request.app.state
    result = await submit_generation_and_schedule(
        request.app,
        project_id=project_id,
        request_id=str(payload.request_id),
        confirmed_revision=payload.confirmed_revision,
        acknowledge_present="acknowledge_unknown" in payload.model_fields_set,
        acknowledge_unknown=payload.acknowledge_unknown,
    )
    if not result.created:
        response.status_code = 200
    return GenerationResponse(
        generation=generation_view(
            result.generation,
            result.result_asset,
            state.settings.resolved_data_dir(),
        )
    )


@router.get("/api/generations/{generation_id}", response_model=GenerationView)
async def get_generation_endpoint(request: Request, generation_id: str) -> GenerationView:
    state = request.app.state
    generation, result_asset = await load_generation(state.session_factory, generation_id)
    info = await load_retrieval_info(
        state.session_factory, state.settings, state.registry, generation
    )
    return generation_view(
        generation,
        result_asset,
        state.settings.resolved_data_dir(),
        can_retrieve_result=info.can_retrieve,
        retrieval_unavailable_reason=info.reason,
    )


@router.post(
    "/api/generations/{generation_id}/retrieve-result",
    response_model=GenerationView,
)
async def retrieve_generation_result_endpoint(
    request: Request, generation_id: str, _payload: EmptyBody
) -> GenerationView:
    state = request.app.state
    generation, result_asset = await retrieve_result(request.app, generation_id)
    info = await load_retrieval_info(
        state.session_factory, state.settings, state.registry, generation
    )
    return generation_view(
        generation,
        result_asset,
        state.settings.resolved_data_dir(),
        can_retrieve_result=info.can_retrieve,
        retrieval_unavailable_reason=info.reason,
    )

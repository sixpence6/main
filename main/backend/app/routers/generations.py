from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from app.presenters import generation_view
from app.schemas import CreateGenerationRequest, EmptyBody, GenerationResponse, GenerationView
from app.services.generations import (
    SubmissionResult,
    load_generation,
    mark_scheduling_failed,
    schedule_generation,
    submit_generation,
)
from app.services.retrieval import load_retrieval_info, retrieve_result

logger = logging.getLogger("app.generations")

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
    request_id = str(payload.request_id)
    result = await submit_generation(
        state.session_factory,
        state.locks,
        state.registry,
        state.settings,
        project_id=project_id,
        request_id=request_id,
        confirmed_revision=payload.confirmed_revision,
        acknowledge_present="acknowledge_unknown" in payload.model_fields_set,
        acknowledge_unknown=payload.acknowledge_unknown,
    )
    if result.created:
        try:
            schedule_generation(request.app, result.generation.id)
        except Exception:  # noqa: BLE001 - scheduling failure must be observable
            logger.exception("could not schedule generation %s", result.generation.id)
            await mark_scheduling_failed(state.session_factory, result.generation.id)
            generation, asset = await load_generation(state.session_factory, result.generation.id)
            result = SubmissionResult(generation=generation, result_asset=asset, created=True)
    else:
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

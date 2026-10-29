from __future__ import annotations

from fastapi import APIRouter, Request

from app.presenters import message_view, project_view
from app.schemas import ERROR_RESPONSE, MessageExchangeResponse, SendMessageRequest
from app.services.messages import send_message

router = APIRouter(prefix="/api/projects", tags=["messages"])


@router.post(
    "/{project_id}/messages",
    response_model=MessageExchangeResponse,
    responses={
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        502: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
        504: ERROR_RESPONSE,
    },
)
async def send_message_endpoint(
    request: Request, project_id: str, payload: SendMessageRequest
) -> MessageExchangeResponse:
    state = request.app.state
    exchange = await send_message(
        state.session_factory,
        state.locks,
        state.registry,
        state.settings,
        state.http,
        project_id=project_id,
        content=payload.content,
        expected_revision=payload.expected_revision,
    )
    data_dir = state.settings.resolved_data_dir()
    return MessageExchangeResponse(
        project=project_view(exchange.project, exchange.reference_asset, data_dir),
        user_message=message_view(exchange.user_message),
        assistant_message=message_view(exchange.assistant_message),
    )

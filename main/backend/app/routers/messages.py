from __future__ import annotations

from fastapi import APIRouter, Request

from app.auth.principal import require_principal
from app.errors import ApiError
from app.operator_audit import record_operator_event_safely
from app.presenters import message_view, project_view
from app.request_source import client_ip
from app.schemas import ERROR_RESPONSE, MessageExchangeResponse, SendMessageRequest
from app.services.messages import send_message
from app.workflow.access import load_project_access_policy

router = APIRouter(prefix="/api/projects", tags=["messages"])


@router.post(
    "/{project_id}/messages",
    response_model=MessageExchangeResponse,
    responses={
        401: ERROR_RESPONSE,
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
    principal = require_principal(request)
    source = client_ip(request)
    request_id = str(payload.request_id) if payload.request_id is not None else None
    try:
        exchange = await send_message(
            state.session_factory,
            state.locks,
            state.registry,
            state.settings,
            state.http,
            project_id=project_id,
            content=payload.content,
            expected_revision=payload.expected_revision,
            principal=principal,
            app=request.app,
            request_id=request_id,
            attachment_asset_id=(
                str(payload.attachment_asset_id)
                if payload.attachment_asset_id is not None
                else None
            ),
        )
    except ApiError as exc:
        await record_operator_event_safely(
            request.app,
            event_type="message_send",
            principal=principal,
            project_id=project_id,
            result="denied" if exc.status_code in (401, 403) else "failure",
            reason_code=exc.code,
            source=source,
            detail={
                "request_id": request_id,
                "project_revision": payload.expected_revision,
            },
        )
        raise
    await record_operator_event_safely(
        request.app,
        event_type="message_send",
        principal=principal,
        project_id=project_id,
        result="success",
        reason_code=exchange.kind,
        source=source,
        detail={
            "request_id": request_id,
            "turn_kind": exchange.kind,
            "project_revision": exchange.project.revision,
            "user_message_id": exchange.user_message.id,
            "assistant_message_id": exchange.assistant_message.id,
        },
    )
    data_dir = state.settings.resolved_data_dir()
    async with state.session_factory() as session:
        policy = await load_project_access_policy(session, project_id)
    return MessageExchangeResponse(
        project=project_view(
            exchange.project, exchange.reference_asset, data_dir, policy=policy
        ),
        user_message=message_view(
            exchange.user_message, policy=policy, attachment=exchange.attachment
        ),
        assistant_message=message_view(exchange.assistant_message, policy=policy),
        workflow=exchange.workflow,
    )

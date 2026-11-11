from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.auth.links import consume_link, inspect_link
from app.request_source import client_ip
from app.schemas import ERROR_RESPONSE

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LinkStatusResponse(BaseModel):
    account_email: str
    purpose: Literal["setup", "reset"]
    expires_at: str


class LinkPasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str
    password: str
    purpose: Literal["setup", "reset"]


class LinkPasswordResponse(BaseModel):
    status: Literal["ok"]


@router.get(
    "/link",
    response_model=LinkStatusResponse,
    responses={400: ERROR_RESPONSE, 429: ERROR_RESPONSE},
)
async def link_status(request: Request) -> LinkStatusResponse:
    token = request.headers.get("x-link-token", "")
    info = await inspect_link(
        request.app.state.session_factory,
        raw_token=token,
        now=request.app.state.clock.now(),
    )
    return LinkStatusResponse(
        account_email=info.account_email,
        purpose=info.purpose,
        expires_at=info.expires_at,
    )


@router.post(
    "/link",
    response_model=LinkPasswordResponse,
    responses={400: ERROR_RESPONSE, 422: ERROR_RESPONSE, 429: ERROR_RESPONSE},
)
async def submit_link(request: Request, payload: LinkPasswordRequest) -> LinkPasswordResponse:
    await consume_link(
        request.app.state.session_factory,
        raw_token=payload.token,
        purpose=payload.purpose,
        password=payload.password,
        now=request.app.state.clock.now(),
        hash_gate=request.app.state.hash_gate,
        client_host=client_ip(request),
    )
    return LinkPasswordResponse(status="ok")

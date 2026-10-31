from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from app.auth.links import create_account, issue_link, list_accounts
from app.auth.principal import require_admin
from app.auth.views import AccountView, account_view
from app.schemas import ERROR_RESPONSE

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CreateAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str


class CreateAccountResponse(BaseModel):
    account: AccountView


class AccountListResponse(BaseModel):
    items: list[AccountView]


class IssueLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["setup", "reset"]


class IssueLinkResponse(BaseModel):
    account_id: str
    link: str
    purpose: Literal["setup", "reset"]
    expires_at: str


@router.get("/accounts", response_model=AccountListResponse)
async def accounts(request: Request) -> AccountListResponse:
    require_admin(request)
    accounts_list = await list_accounts(request.app.state.session_factory)
    return AccountListResponse(items=[account_view(account) for account in accounts_list])


@router.post(
    "/accounts",
    status_code=201,
    response_model=CreateAccountResponse,
    responses={409: ERROR_RESPONSE, 422: ERROR_RESPONSE},
)
async def create_account_endpoint(
    request: Request, payload: CreateAccountRequest
) -> CreateAccountResponse:
    principal = require_admin(request)
    account = await create_account(
        request.app.state.session_factory,
        admin_account_id=principal.account_id,
        email=payload.email,
        clock=request.app.state.clock,
    )
    return CreateAccountResponse(account=account_view(account))


@router.post(
    "/accounts/{account_id}/links",
    status_code=201,
    response_model=IssueLinkResponse,
    responses={
        404: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        429: ERROR_RESPONSE,
    },
)
async def issue_link_endpoint(
    request: Request, account_id: str, payload: IssueLinkRequest
) -> IssueLinkResponse:
    principal = require_admin(request)
    issued = await issue_link(
        request.app.state.session_factory,
        admin_account_id=principal.account_id,
        account_id=account_id,
        purpose=payload.purpose,
        app_public_url=request.app.state.settings.app_public_url,
        clock=request.app.state.clock,
    )
    return IssueLinkResponse(
        account_id=issued.account_id,
        link=issued.link,
        purpose=issued.purpose,
        expires_at=issued.expires_at,
    )

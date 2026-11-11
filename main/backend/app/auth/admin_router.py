from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from app.acceptance import usage_snapshot
from app.auth.account_admin import disable_account, restore_account
from app.auth.links import create_account, issue_link, list_accounts
from app.auth.principal import require_admin
from app.auth.security_events import AdminSecurityEventListResponse, list_admin_security_events
from app.auth.tokens import source_ref
from app.auth.views import AccountView, account_view
from app.generation_quota import (
    EffectiveLimits,
    QuotaPolicy,
    read_account_limits,
    read_instance_limits,
    update_account_limits,
    update_instance_limits,
)
from app.request_source import client_ip
from app.schemas import ERROR_RESPONSE

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CreateAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str


class CreateAccountResponse(BaseModel):
    account: AccountView


class AccountListResponse(BaseModel):
    items: list[AccountView]


class AccountResponse(BaseModel):
    account: AccountView


class IssueLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal["setup", "reset"]


class IssueLinkResponse(BaseModel):
    account_id: str
    link: str
    purpose: Literal["setup", "reset"]
    expires_at: str


class GenerationLimitPolicyView(BaseModel):
    daily_limit: int | None
    personal_slots: int | None
    instance_slots: int | None
    updated_at: str | None


class EffectiveLimitsView(BaseModel):
    daily_limit: int
    personal_slots: int
    instance_slots: int
    project_slots: int


class InstanceLimitsResponse(BaseModel):
    policy: GenerationLimitPolicyView
    effective: EffectiveLimitsView
    defaults: EffectiveLimitsView


class AccountUsageView(BaseModel):
    used: int
    remaining: int
    daily_limit: int
    personal_active: int
    personal_limit: int
    instance_active: int
    instance_limit: int
    window_started_at: str | None
    window_expires_at: str | None


class AccountLimitsResponse(BaseModel):
    account_id: str
    email: str
    policy: GenerationLimitPolicyView
    effective: EffectiveLimitsView
    usage: AccountUsageView


class InstanceLimitsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_limit: int | None = Field(default=None, ge=1)
    personal_slots: int | None = Field(default=None, ge=1)
    instance_slots: int | None = Field(default=None, ge=1)


class AccountLimitsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_limit: int | None = Field(default=None, ge=1)
    personal_slots: int | None = Field(default=None, ge=1)


def _policy_view(policy: QuotaPolicy) -> GenerationLimitPolicyView:
    return GenerationLimitPolicyView(
        daily_limit=policy.daily_limit,
        personal_slots=policy.personal_slots,
        instance_slots=policy.instance_slots,
        updated_at=policy.updated_at,
    )


def _effective_view(effective: EffectiveLimits) -> EffectiveLimitsView:
    return EffectiveLimitsView(
        daily_limit=effective.daily_limit,
        personal_slots=effective.personal_slots,
        instance_slots=effective.instance_slots,
        project_slots=effective.project_slots,
    )


async def _account_limits_response(
    request: Request,
    *,
    account_id: str,
    policy: QuotaPolicy,
    effective: EffectiveLimits,
    email: str,
) -> AccountLimitsResponse:
    snapshot = await usage_snapshot(
        request.app.state.session_factory,
        settings=request.app.state.settings,
        account_id=account_id,
        project_id=None,
        now=request.app.state.clock.now(),
    )
    return AccountLimitsResponse(
        account_id=account_id,
        email=email,
        policy=_policy_view(policy),
        effective=_effective_view(effective),
        usage=AccountUsageView(
            used=snapshot["used"],
            remaining=snapshot["remaining"],
            daily_limit=snapshot["daily_limit"],
            personal_active=snapshot["personal_active"],
            personal_limit=snapshot["personal_limit"],
            instance_active=snapshot["instance_active"],
            instance_limit=snapshot["instance_limit"],
            window_started_at=snapshot["window_started_at"],
            window_expires_at=snapshot["window_expires_at"],
        ),
    )


@router.get("/accounts", response_model=AccountListResponse)
async def accounts(request: Request) -> AccountListResponse:
    await require_admin(request)
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
    principal = await require_admin(request)
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
    principal = await require_admin(request)
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


def _request_source(request: Request) -> str:
    return source_ref(client_ip(request))


@router.post(
    "/accounts/{account_id}/disable",
    response_model=AccountResponse,
    responses={
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def disable_account_endpoint(request: Request, account_id: str) -> AccountResponse:
    principal = await require_admin(request)
    account = await disable_account(
        request.app.state.session_factory,
        admin_account_id=principal.account_id,
        account_id=account_id,
        now=request.app.state.clock.now(),
        source_ref_value=_request_source(request),
    )
    return AccountResponse(account=account_view(account))


@router.post(
    "/accounts/{account_id}/restore",
    response_model=AccountResponse,
    responses={
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def restore_account_endpoint(request: Request, account_id: str) -> AccountResponse:
    principal = await require_admin(request)
    account = await restore_account(
        request.app.state.session_factory,
        admin_account_id=principal.account_id,
        account_id=account_id,
        now=request.app.state.clock.now(),
        source_ref_value=_request_source(request),
    )
    return AccountResponse(account=account_view(account))


@router.get(
    "/generation-limits",
    response_model=InstanceLimitsResponse,
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
async def generation_limits(request: Request) -> InstanceLimitsResponse:
    await require_admin(request)
    policy, effective = await read_instance_limits(
        request.app.state.session_factory, settings=request.app.state.settings
    )
    return InstanceLimitsResponse(
        policy=_policy_view(policy),
        effective=_effective_view(effective),
        defaults=_effective_view(
            EffectiveLimits(
                daily_limit=request.app.state.settings.generation_daily_limit,
                personal_slots=request.app.state.settings.generation_personal_slots,
                instance_slots=request.app.state.settings.generation_instance_slots,
                project_slots=request.app.state.settings.generation_project_slots,
            )
        ),
    )


@router.put(
    "/generation-limits",
    response_model=InstanceLimitsResponse,
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def update_generation_limits(
    request: Request, payload: InstanceLimitsUpdate
) -> InstanceLimitsResponse:
    principal = await require_admin(request)
    policy, effective = await update_instance_limits(
        request.app.state.session_factory,
        settings=request.app.state.settings,
        admin_account_id=principal.account_id,
        daily_limit=payload.daily_limit,
        personal_slots=payload.personal_slots,
        instance_slots=payload.instance_slots,
        now=request.app.state.clock.now(),
        source_ref=_request_source(request),
    )
    return InstanceLimitsResponse(
        policy=_policy_view(policy),
        effective=_effective_view(effective),
        defaults=_effective_view(
            EffectiveLimits(
                daily_limit=request.app.state.settings.generation_daily_limit,
                personal_slots=request.app.state.settings.generation_personal_slots,
                instance_slots=request.app.state.settings.generation_instance_slots,
                project_slots=request.app.state.settings.generation_project_slots,
            )
        ),
    )


@router.get(
    "/accounts/{account_id}/generation-limits",
    response_model=AccountLimitsResponse,
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def account_generation_limits(
    request: Request, account_id: str
) -> AccountLimitsResponse:
    await require_admin(request)
    policy, effective, email = await read_account_limits(
        request.app.state.session_factory,
        settings=request.app.state.settings,
        account_id=account_id,
    )
    return await _account_limits_response(
        request, account_id=account_id, policy=policy, effective=effective, email=email
    )


@router.put(
    "/accounts/{account_id}/generation-limits",
    response_model=AccountLimitsResponse,
    responses={
        401: ERROR_RESPONSE,
        403: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def update_account_generation_limits(
    request: Request, account_id: str, payload: AccountLimitsUpdate
) -> AccountLimitsResponse:
    principal = await require_admin(request)
    policy, effective, email = await update_account_limits(
        request.app.state.session_factory,
        settings=request.app.state.settings,
        admin_account_id=principal.account_id,
        account_id=account_id,
        daily_limit=payload.daily_limit,
        personal_slots=payload.personal_slots,
        now=request.app.state.clock.now(),
        source_ref=_request_source(request),
    )
    return await _account_limits_response(
        request, account_id=account_id, policy=policy, effective=effective, email=email
    )


@router.get(
    "/security-events",
    response_model=AdminSecurityEventListResponse,
    responses={401: ERROR_RESPONSE, 403: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
async def admin_security_events(request: Request) -> AdminSecurityEventListResponse:
    await require_admin(request)
    items = await list_admin_security_events(request.app.state.session_factory)
    return AdminSecurityEventListResponse(items=items)

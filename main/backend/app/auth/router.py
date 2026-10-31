from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth.models import Account
from app.auth.principal import require_principal
from app.auth.service import login, logout
from app.auth.sessions import (
    PREAUTH_CSRF_COOKIE,
    SESSION_ABSOLUTE_SECONDS,
    SESSION_COOKIE,
)
from app.auth.tokens import constant_time_equals, new_token, token_digest
from app.config import Settings
from app.errors import ApiError
from app.schemas import ERROR_RESPONSE

router = APIRouter(prefix="/api/auth", tags=["auth"])

PREAUTH_CSRF_MAX_AGE = 15 * 60


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class AccountView(BaseModel):
    id: str
    email: str
    is_admin: bool
    status: Literal["pending_setup", "active", "disabled"]
    created_at: str
    last_login_at: str | None


class LoginResponse(BaseModel):
    account: AccountView
    csrf_token: str
    expires_at: str


class SessionResponse(BaseModel):
    account: AccountView
    csrf_token: str
    expires_at: str


class CsrfResponse(BaseModel):
    csrf_token: str


def account_view(account: Account) -> AccountView:
    return AccountView(
        id=account.id,
        email=account.email_normalized,
        is_admin=bool(account.is_admin),
        status=account.status,  # type: ignore[arg-type]
        created_at=account.created_at,
        last_login_at=account.last_login_at,
    )


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_ABSOLUTE_SECONDS,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.get("/csrf", response_model=CsrfResponse)
async def csrf(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    auth_session = getattr(request.state, "auth_session", None)
    if auth_session is not None:
        # GET must not rotate or clobber the session-bound CSRF token.
        current = request.cookies.get(PREAUTH_CSRF_COOKIE)
        if current and constant_time_equals(auth_session.csrf_digest, token_digest(current)):
            return JSONResponse(CsrfResponse(csrf_token=current).model_dump())
        raise ApiError(401, "AUTH_REQUIRED", "请先登录")
    token = new_token()
    response = JSONResponse(CsrfResponse(csrf_token=token).model_dump())
    response.set_cookie(
        key=PREAUTH_CSRF_COOKIE,
        value=token,
        max_age=PREAUTH_CSRF_MAX_AGE,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    return response


@router.post("/login", response_model=LoginResponse, responses={422: ERROR_RESPONSE})
async def login_endpoint(request: Request, payload: LoginRequest) -> JSONResponse:
    settings: Settings = request.app.state.settings
    client_host = request.client.host if request.client else "unknown"
    result = await login(
        request.app.state.session_factory,
        email=payload.email,
        password=payload.password,
        clock=request.app.state.clock,
        hash_gate=request.app.state.hash_gate,
        client_host=client_host,
        user_agent=request.headers.get("user-agent"),
    )
    response = JSONResponse(
        LoginResponse(
            account=account_view(result.account),
            csrf_token=result.csrf_token,
            expires_at=result.expires_at,
        ).model_dump()
    )
    _set_session_cookie(response, result.raw_token, settings)
    response.set_cookie(
        key=PREAUTH_CSRF_COOKIE,
        value=result.csrf_token,
        max_age=SESSION_ABSOLUTE_SECONDS,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="strict",
    )
    return response


@router.get("/session", response_model=SessionResponse)
async def session_endpoint(request: Request) -> SessionResponse:
    account: Account | None = getattr(request.state, "auth_account", None)
    auth_session = getattr(request.state, "auth_session", None)
    if account is None or auth_session is None:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录")
    csrf_token = request.cookies.get(PREAUTH_CSRF_COOKIE)
    from app.auth.tokens import constant_time_equals, token_digest

    if not csrf_token or not constant_time_equals(
        auth_session.csrf_digest, token_digest(csrf_token)
    ):
        raise ApiError(401, "AUTH_REQUIRED", "请先登录")
    return SessionResponse(
        account=account_view(account),
        csrf_token=csrf_token,
        expires_at=auth_session.absolute_expires_at,
    )


@router.post("/logout")
async def logout_endpoint(request: Request) -> Response:
    principal = require_principal(request)
    settings: Settings = request.app.state.settings
    await logout(
        request.app.state.session_factory,
        session_id=principal.session_id,
        account_id=principal.account_id,
        now=request.app.state.clock.now(),
        source_ref_value=None,
    )
    response = Response(status_code=204)
    _clear_session_cookie(response, settings)
    response.delete_cookie(
        key=PREAUTH_CSRF_COOKIE,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="strict",
    )
    return response

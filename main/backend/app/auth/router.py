from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.auth.models import Account
from app.auth.personal import (
    change_password,
    list_active_sessions,
    revoke_other_sessions,
    revoke_session,
)
from app.auth.principal import require_principal
from app.auth.service import login, logout
from app.auth.sessions import (
    PREAUTH_CSRF_COOKIE,
    SESSION_ABSOLUTE_SECONDS,
    SESSION_COOKIE,
)
from app.auth.tokens import constant_time_equals, new_token, source_ref, token_digest
from app.auth.views import AccountView, SessionView, account_view, session_view
from app.config import Settings
from app.errors import ApiError
from app.schemas import ERROR_RESPONSE

router = APIRouter(prefix="/api/auth", tags=["auth"])

PREAUTH_CSRF_MAX_AGE = 15 * 60


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


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


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: str


class SessionListResponse(BaseModel):
    items: list[SessionView]


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


def _clear_preauth_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=PREAUTH_CSRF_COOKIE,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="strict",
    )


def _clear_auth_cookies(response: Response, settings: Settings) -> None:
    _clear_session_cookie(response, settings)
    _clear_preauth_cookie(response, settings)


def _request_source(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return source_ref(host or "unknown")


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
    _clear_auth_cookies(response, settings)
    return response


@router.post(
    "/password/change",
    status_code=204,
    responses={
        400: ERROR_RESPONSE,
        401: ERROR_RESPONSE,
        409: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def change_password_endpoint(request: Request, payload: PasswordChangeRequest) -> Response:
    principal = require_principal(request)
    settings: Settings = request.app.state.settings
    await change_password(
        request.app.state.session_factory,
        session_id=principal.session_id,
        account_id=principal.account_id,
        current_password=payload.current_password,
        new_password=payload.new_password,
        clock=request.app.state.clock,
        hash_gate=request.app.state.hash_gate,
        source_ref_value=_request_source(request),
    )
    response = Response(status_code=204)
    _clear_auth_cookies(response, settings)
    return response


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    responses={401: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
async def sessions_endpoint(request: Request) -> SessionListResponse:
    principal = require_principal(request)
    records = await list_active_sessions(
        request.app.state.session_factory,
        account_id=principal.account_id,
        now=request.app.state.clock.now(),
    )
    return SessionListResponse(
        items=[
            session_view(record, current=record.id == principal.session_id)
            for record in records
        ]
    )


@router.delete(
    "/sessions/{session_id}",
    status_code=204,
    responses={
        401: ERROR_RESPONSE,
        404: ERROR_RESPONSE,
        422: ERROR_RESPONSE,
        503: ERROR_RESPONSE,
    },
)
async def revoke_session_endpoint(request: Request, session_id: str) -> Response:
    principal = require_principal(request)
    settings: Settings = request.app.state.settings
    is_current = await revoke_session(
        request.app.state.session_factory,
        actor_account_id=principal.account_id,
        actor_session_id=principal.session_id,
        target_session_id=session_id,
        now=request.app.state.clock.now(),
        source_ref_value=_request_source(request),
    )
    response = Response(status_code=204)
    if is_current:
        _clear_auth_cookies(response, settings)
    return response


@router.post(
    "/sessions/revoke-others",
    status_code=204,
    responses={401: ERROR_RESPONSE, 503: ERROR_RESPONSE},
)
async def revoke_other_sessions_endpoint(request: Request) -> Response:
    principal = require_principal(request)
    await revoke_other_sessions(
        request.app.state.session_factory,
        account_id=principal.account_id,
        current_session_id=principal.session_id,
        now=request.app.state.clock.now(),
        source_ref_value=_request_source(request),
    )
    return Response(status_code=204)

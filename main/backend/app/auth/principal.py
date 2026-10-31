from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.auth.tokens import source_ref
from app.errors import ApiError


@dataclass(frozen=True)
class Principal:
    account_id: str
    email: str
    is_admin: bool
    session_id: str
    account_epoch: int


def current_principal(request: Request) -> Principal | None:
    raw = getattr(request.state, "principal", None)
    if not raw:
        return None
    return Principal(**raw)


def require_principal(request: Request) -> Principal:
    principal = current_principal(request)
    if principal is None:
        raise ApiError(401, "AUTH_REQUIRED", "请先登录")
    return principal


async def require_admin(request: Request) -> Principal:
    principal = require_principal(request)
    if not principal.is_admin:
        await _audit_denial(request, principal)
        raise ApiError(403, "FORBIDDEN", "没有权限执行此操作")
    return principal


async def _audit_denial(request: Request, principal: Principal) -> None:
    """Best-effort audit for an authenticated role denial; never masks the 403."""
    try:
        from app.auth.security_events import record_permission_denied

        factory = getattr(request.app.state, "session_factory", None)
        clock = getattr(request.app.state, "clock", None)
        if factory is None or clock is None:
            return
        host = request.client.host if request.client else "unknown"
        await record_permission_denied(
            factory,
            account_id=principal.account_id,
            method=request.method,
            path=request.url.path,
            now=clock.now(),
            source_ref_value=source_ref(host or "unknown"),
        )
    except Exception:
        return

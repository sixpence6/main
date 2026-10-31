from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

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


def require_admin(request: Request) -> Principal:
    principal = require_principal(request)
    if not principal.is_admin:
        raise ApiError(403, "FORBIDDEN", "没有权限执行此操作")
    return principal

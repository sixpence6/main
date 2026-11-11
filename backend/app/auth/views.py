from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.auth.models import Account, AuthSession


class AccountView(BaseModel):
    id: str
    email: str
    is_admin: bool
    status: Literal["pending_setup", "active", "disabled"]
    created_at: str
    last_login_at: str | None


def account_view(account: Account) -> AccountView:
    return AccountView(
        id=account.id,
        email=account.email_normalized,
        is_admin=bool(account.is_admin),
        status=account.status,  # type: ignore[arg-type]
        created_at=account.created_at,
        last_login_at=account.last_login_at,
    )


class SessionView(BaseModel):
    id: str
    current: bool
    created_at: str
    last_activity_at: str
    idle_expires_at: str
    absolute_expires_at: str
    client_info: str | None


def session_view(record: AuthSession, *, current: bool) -> SessionView:
    return SessionView(
        id=record.id,
        current=current,
        created_at=record.created_at,
        last_activity_at=record.last_activity_at,
        idle_expires_at=record.idle_expires_at,
        absolute_expires_at=record.absolute_expires_at,
        client_info=record.client_info,
    )

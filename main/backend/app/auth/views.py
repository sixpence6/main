from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.auth.models import Account


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

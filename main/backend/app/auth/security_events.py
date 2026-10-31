from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.audit import write_audit
from app.auth.models import Account, AuditEvent
from app.auth.tokens import token_digest
from app.db import read_session, write_transaction

SECURITY_EVENT_TYPES = frozenset(
    {
        "bootstrap_admin_created",
        "login_success",
        "login_failure",
        "login_limited",
        "logout",
        "password_changed",
        "account_created",
        "account_link_issued",
        "account_link_issue_limited",
        "account_link_used",
        "account_link_verify_limited",
        "session_revoked",
        "sessions_revoked",
        "admin_recovered",
        "account_disabled",
        "account_restored",
        "generation_account_blocked",
        "legacy_projects_claimed",
        "permission_denied",
    }
)
SAFE_DETAIL_KEYS = frozenset(
    {
        "purpose",
        "expires_at",
        "current_session",
        "revoked_count",
        "scope",
        "sessions_revoked",
        "links_revoked",
        "method",
        "stage",
        "remote_task_recorded",
        "project_count",
    }
)
SAFE_STATE_KEYS = frozenset({"status", "is_admin", "is_disabled", "session_epoch"})
EVENT_RESULT = Literal["success", "failure", "denied", "limited"]
ACTOR_LABELS = {
    "anonymous": "匿名",
    "system": "系统",
    "local_maintainer": "本机维护",
    "admin": "管理员",
}


class SecurityEventView(BaseModel):
    id: str
    occurred_at: str
    event_type: str
    result: EVENT_RESULT
    reason_code: str | None
    actor_kind: str
    detail: dict[str, Any] | None


class SecurityEventListResponse(BaseModel):
    items: list[SecurityEventView]


class AdminSecurityEventView(BaseModel):
    id: str
    occurred_at: str
    event_type: str
    result: EVENT_RESULT
    reason_code: str | None
    actor_kind: str
    actor_ref: str | None
    actor_label: str | None
    target_kind: str | None
    target_ref: str | None
    target_label: str | None
    subject_ref: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    detail: dict[str, Any] | None


class AdminSecurityEventListResponse(BaseModel):
    items: list[AdminSecurityEventView]


def _decode(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _safe_object(raw: str | None, allowed: frozenset[str]) -> dict[str, Any] | None:
    value = _decode(raw)
    if value is None:
        return None
    safe = {
        key: item
        for key, item in value.items()
        if key in allowed and (item is None or isinstance(item, (str, int, float, bool)))
    }
    return safe or None


def _ref(value: str) -> str:
    return token_digest("security-event-ref-v1:" + value)[:12]


def _account_map(rows: list[AuditEvent], accounts: list[Account]) -> dict[str, Account]:
    known = {account.id: account for account in accounts}
    return {
        account_id: known[account_id]
        for account_id in {
            *[row.actor_account_id for row in rows if row.actor_account_id],
            *[row.target_account_id for row in rows if row.target_account_id],
        }
        if account_id in known
    }


async def _events(
    factory: async_sessionmaker[AsyncSession], *, account_id: str | None, limit: int
) -> tuple[list[AuditEvent], dict[str, Account]]:
    bounded = max(1, min(limit, 100))
    async with read_session(factory) as session:
        statement = select(AuditEvent).where(AuditEvent.event_type.in_(SECURITY_EVENT_TYPES))
        if account_id is not None:
            statement = statement.where(AuditEvent.target_account_id == account_id)
        rows = list(
            await session.scalars(
                statement.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(
                    bounded
                )
            )
        )
        account_ids = {
            *[row.actor_account_id for row in rows if row.actor_account_id],
            *[row.target_account_id for row in rows if row.target_account_id],
        }
        accounts = []
        if account_ids:
            accounts = list(
                await session.scalars(select(Account).where(Account.id.in_(account_ids)))
            )
    return rows, _account_map(rows, accounts)


def _personal_view(row: AuditEvent) -> SecurityEventView:
    return SecurityEventView(
        id=row.id,
        occurred_at=row.occurred_at,
        event_type=row.event_type,
        result=row.result,  # type: ignore[arg-type]
        reason_code=row.reason_code,
        actor_kind=row.actor_kind,
        detail=_safe_object(row.detail_json, SAFE_DETAIL_KEYS),
    )


def _admin_view(row: AuditEvent, known: dict[str, Account]) -> AdminSecurityEventView:
    actor = known.get(row.actor_account_id) if row.actor_account_id else None
    target = known.get(row.target_account_id) if row.target_account_id else None
    actor_label = actor.email_normalized if actor is not None else ACTOR_LABELS.get(row.actor_kind)
    if target is not None:
        target_label = target.email_normalized
    elif row.target_kind == "account" and row.target_id:
        target_label = f"未知账号（{_ref(row.target_id)}）"
    elif row.target_kind and row.target_id:
        target_label = f"{row.target_kind}（{_ref(row.target_id)}）"
    else:
        target_label = None
    return AdminSecurityEventView(
        id=row.id,
        occurred_at=row.occurred_at,
        event_type=row.event_type,
        result=row.result,  # type: ignore[arg-type]
        reason_code=row.reason_code,
        actor_kind=row.actor_kind,
        actor_ref=_ref(row.actor_account_id) if row.actor_account_id else None,
        actor_label=actor_label,
        target_kind=row.target_kind,
        target_ref=(
            _ref(row.target_account_id)
            if row.target_account_id
            else (_ref(row.target_id) if row.target_id else None)
        ),
        target_label=target_label,
        subject_ref=row.subject_ref,
        before=_safe_object(row.before_json, SAFE_STATE_KEYS),
        after=_safe_object(row.after_json, SAFE_STATE_KEYS),
        detail=_safe_object(row.detail_json, SAFE_DETAIL_KEYS),
    )


async def record_permission_denied(
    factory: async_sessionmaker[AsyncSession],
    *,
    account_id: str,
    method: str,
    path: str,
    now: Any,
    source_ref_value: str | None = None,
) -> None:
    """Record an authenticated role denial without storing request bodies or credentials."""
    async with write_transaction(factory) as session:
        write_audit(
            session,
            event_type="permission_denied",
            result="denied",
            now=now,
            actor_kind="account",
            actor_account_id=account_id,
            target_account_id=account_id,
            target_kind="route",
            target_id=path[:64],
            reason_code="ADMIN_REQUIRED",
            source_ref=source_ref_value,
            detail={"method": method[:16]},
        )


async def list_personal_security_events(
    factory: async_sessionmaker[AsyncSession], *, account_id: str, limit: int = 100
) -> list[SecurityEventView]:
    rows, _ = await _events(factory, account_id=account_id, limit=limit)
    return [_personal_view(row) for row in rows]


async def list_admin_security_events(
    factory: async_sessionmaker[AsyncSession], *, limit: int = 100
) -> list[AdminSecurityEventView]:
    rows, known = await _events(factory, account_id=None, limit=limit)
    return [_admin_view(row, known) for row in rows]

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import AuditEvent
from app.auth.tokens import token_digest
from app.timeutil import format_timestamp


def _json(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) <= 4096:
        return encoded
    # Never cut JSON mid-document: keep the audit row valid and self-describing.
    return json.dumps(
        {
            "truncated": True,
            "sha256": token_digest(encoded),
            "original_length": len(encoded),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def audit_subject(normalized_or_raw_email: str) -> str:
    return token_digest("audit-subject-v1:" + normalized_or_raw_email)[:48]


def write_audit(
    session: AsyncSession,
    *,
    event_type: str,
    result: str,
    now: Any,
    actor_kind: str = "anonymous",
    actor_account_id: str | None = None,
    target_account_id: str | None = None,
    target_kind: str | None = None,
    target_id: str | None = None,
    subject_ref: str | None = None,
    reason_code: str | None = None,
    correlation_id: str | None = None,
    source_ref: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        id=str(uuid.uuid4()),
        occurred_at=format_timestamp(now),
        event_type=event_type,
        actor_kind=actor_kind,
        actor_account_id=actor_account_id,
        target_account_id=target_account_id,
        target_kind=target_kind,
        target_id=target_id,
        subject_ref=subject_ref,
        result=result,
        reason_code=reason_code,
        correlation_id=correlation_id or str(uuid.uuid4()),
        source_ref=source_ref,
        before_json=_json(before),
        after_json=_json(after),
        detail_json=_json(detail),
    )
    session.add(event)
    return event

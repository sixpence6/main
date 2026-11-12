from __future__ import annotations

import logging
from typing import Any

from app.auth.audit import write_audit
from app.auth.principal import Principal
from app.auth.tokens import source_ref
from app.db import write_transaction

logger = logging.getLogger(__name__)


async def record_operator_event(
    app: Any,
    *,
    event_type: str,
    principal: Principal,
    project_id: str,
    result: str = "success",
    reason_code: str | None = None,
    source: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Persist an immutable event identifying the account that acted.

    Business workflow tables keep their v2 role model.  This audit row is the
    bridge to the R1 account identity: it records the authenticated operator
    without changing any existing approval or workflow semantics.
    """

    now = app.state.clock.now()
    async with write_transaction(app.state.session_factory) as session:
        write_audit(
            session,
            event_type=event_type,
            result=result,
            now=now,
            actor_kind="account",
            actor_account_id=principal.account_id,
            target_kind="project",
            target_id=project_id,
            reason_code=reason_code,
            source_ref=source_ref(source or "unknown"),
            detail=detail or {},
        )


async def record_operator_event_safely(
    app: Any,
    *,
    event_type: str,
    principal: Principal,
    project_id: str,
    result: str = "success",
    reason_code: str | None = None,
    source: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Best-effort audit for non-account business mutations.

    Account security changes are audited atomically in their own transaction.
    Workflow/reference actions span several existing v2 transactions, so an
    audit failure must not claim that an already-committed user action failed.
    The durable audit row is still written on every normal request.
    """

    try:
        await record_operator_event(
            app,
            event_type=event_type,
            principal=principal,
            project_id=project_id,
            result=result,
            reason_code=reason_code,
            source=source,
            detail=detail,
        )
    except Exception:  # pragma: no cover - exercised only by infrastructure failure
        logger.exception("failed to record operator event %s", event_type)

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.timeutil import format_timestamp, utc_now

GATE_NAME = "restore-gate.json"
GATE_SCHEMA = "maintenance-restore-gate/v1"
GAP_SECONDS = 24 * 60 * 60
GATE_STATUS_OPEN = "open"
GATE_STATUS_CLEARED = "cleared"


def gate_path(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser().resolve() / GATE_NAME


def read_restore_gate(data_dir: str | Path) -> dict[str, Any] | None:
    path = gate_path(data_dir)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("schema") != GATE_SCHEMA:
        return None
    return value


def write_restore_gate(data_dir: str | Path, gate: dict[str, Any]) -> Path:
    path = gate_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{GATE_NAME}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(gate, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def _parse_ts(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now(now: datetime | None = None) -> datetime:
    return now or utc_now()


def gate_is_open(data_dir: str | Path, *, now: datetime | None = None) -> bool:
    gate = read_restore_gate(data_dir)
    if gate is None:
        return False
    if gate.get("status") != GATE_STATUS_OPEN:
        return False
    expires = _parse_ts(gate.get("gap_expires_at"))
    if expires is None:
        return True
    current = _now(now)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=current.tzinfo)
    return current < expires


def usage_floor_count(
    data_dir: str | Path, *, account_id: str, now_ms: int
) -> int:
    gate = read_restore_gate(data_dir)
    if gate is None:
        return 0
    total = 0
    for floor in gate.get("usage_floors") or ():
        if not isinstance(floor, dict) or floor.get("account_id") != account_id:
            continue
        try:
            start = int(floor.get("window_start_ms"))
            expires = int(floor.get("window_expires_ms"))
            count = int(floor.get("count"))
        except (TypeError, ValueError):
            continue
        if start <= now_ms < expires:
            total += max(count, 0)
    return total


def slot_floor_count(
    data_dir: str | Path, *, scope: str, scope_id: str, now: datetime | None = None
) -> int:
    gate = read_restore_gate(data_dir)
    if gate is None or not gate_is_open(data_dir, now=now):
        return 0
    total = 0
    for floor in gate.get("slot_floors") or ():
        if (
            isinstance(floor, dict)
            and floor.get("scope") == scope
            and floor.get("scope_id") == scope_id
        ):
            try:
                total += max(int(floor.get("count")), 0)
            except (TypeError, ValueError):
                continue
    return total


def build_gate(
    *,
    backup_id: str,
    restored_at: datetime,
    gap_seconds: int = GAP_SECONDS,
    usage_floors: list[dict[str, Any]] | None = None,
    slot_floors: list[dict[str, Any]] | None = None,
    sessions_revoked: int,
    links_revoked: int,
    approvals_revoked: int,
    deliveries_revoked: int,
    assets_guarded: int,
) -> dict[str, Any]:
    gap_expires = restored_at + timedelta(seconds=gap_seconds)
    return {
        "schema": GATE_SCHEMA,
        "status": GATE_STATUS_OPEN,
        "backup_id": backup_id,
        "restored_at": format_timestamp(restored_at),
        "gap_expires_at": format_timestamp(gap_expires),
        "cleared_at": None,
        "cleared_by": None,
        "clear_reason": None,
        "sessions_revoked": sessions_revoked,
        "links_revoked": links_revoked,
        "approvals_revoked": approvals_revoked,
        "deliveries_revoked": deliveries_revoked,
        "assets_guarded": assets_guarded,
        "usage_floors": usage_floors or [],
        "slot_floors": slot_floors or [],
    }


def clear_restore_gate(
    data_dir: str | Path,
    *,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    gate = read_restore_gate(data_dir)
    if gate is None:
        return None
    current = _now(now)
    gate["status"] = GATE_STATUS_CLEARED
    gate["cleared_at"] = format_timestamp(current)
    gate["cleared_by"] = "local_maintainer"
    gate["clear_reason"] = reason[:500]
    write_restore_gate(data_dir, gate)
    return gate


__all__ = [
    "GAP_SECONDS",
    "GATE_NAME",
    "GATE_SCHEMA",
    "build_gate",
    "clear_restore_gate",
    "gate_is_open",
    "gate_path",
    "read_restore_gate",
    "slot_floor_count",
    "usage_floor_count",
    "write_restore_gate",
]

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import DEFAULT_DATA_DIR, MAIN_DIR
from app.maintenance.backup import (
    _create_backup_sync,
    _validate_output,
    verify_backup,
)
from app.maintenance.errors import BackupError
from app.maintenance.manifest import DATA_DIR_NAME, load_manifest
from app.maintenance.restore_state import (
    GAP_SECONDS,
    GATE_STATUS_CLEARED,
    GATE_STATUS_OPEN,
    build_gate,
    clear_restore_gate,
    gate_is_open,
    read_restore_gate,
    write_restore_gate,
)
from app.maintenance.sources import DB_NAME, inspect_candidate
from app.startup import head_revision
from app.timeutil import format_timestamp, utc_now

RESTORE_CONFIRMATION = "RESTORE"
CLEAR_CONFIRMATION = "CLEAR_RESTORE_GAP"
RESTORE_REVOKED_REASON = "restore_revoked"


@dataclass(frozen=True)
class RestoreSwitchResult:
    backup_id: str
    restored_dir: Path
    safety_backup_id: str
    safety_backup_dir: Path
    manifest_sha256: str
    file_count: int
    total_bytes: int
    sessions_revoked: int
    links_revoked: int
    approvals_revoked: int
    deliveries_revoked: int
    assets_guarded: int
    usage_floor_accounts: int
    gap_expires_at: str
    verified: bool
    production_switched: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "backup_id": self.backup_id,
            "restored_dir": str(self.restored_dir),
            "safety_backup_id": self.safety_backup_id,
            "safety_backup_dir": str(self.safety_backup_dir),
            "manifest_sha256": self.manifest_sha256,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "sessions_revoked": self.sessions_revoked,
            "links_revoked": self.links_revoked,
            "approvals_revoked": self.approvals_revoked,
            "deliveries_revoked": self.deliveries_revoked,
            "assets_guarded": self.assets_guarded,
            "usage_floor_accounts": self.usage_floor_accounts,
            "gap_expires_at": self.gap_expires_at,
            "verified": self.verified,
            "production_switched": self.production_switched,
        }


@dataclass
class _CurrentMetrics:
    usage: dict[str, int]
    usage_expires: dict[str, int]
    slots: dict[tuple[str, str], int]
    revoked_approvals: set[str]
    revoked_deliveries: set[str]
    rejected_assets: set[str]
    accounts: dict[str, tuple[str | None, int]]


def _protected_sources() -> tuple[Path, ...]:
    return (
        Path(DEFAULT_DATA_DIR).expanduser().resolve(),
        (Path(MAIN_DIR) / "backend" / "data").expanduser().resolve(),
    )


def _assert_source_allowed(source: Path, *, allow_real_data_root: bool) -> None:
    for protected in _protected_sources():
        if source == protected and not allow_real_data_root:
            raise BackupError(
                "REAL_DATA_ROOT_REQUIRES_AUTHORIZATION",
                "selected source is a real data root; an explicit maintainer choice is required",
                details={"source": str(source)},
            )


def _open_readonly(path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        raise BackupError(
            "SOURCE_DATABASE_UNREADABLE",
            f"could not open source database: {type(exc).__name__}",
        ) from exc


def _read_current_metrics(source: Path, now: datetime) -> _CurrentMetrics:
    database = source / DB_NAME
    if not database.is_file():
        raise BackupError("SOURCE_NOT_DATA_ROOT", f"source has no {DB_NAME}: {source}")
    connection = _open_readonly(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        usage: dict[str, int] = {}
        usage_expires: dict[str, int] = {}
        for account_id, count, expires in connection.execute(
            "SELECT account_id, COUNT(*), MAX(window_expires_ms) FROM generation_usage "
            "WHERE window_start_ms > ? AND window_start_ms <= ? GROUP BY account_id",
            (_now_ms(now) - 24 * 60 * 60 * 1000, _now_ms(now)),
        ):
            usage[str(account_id)] = int(count)
            usage_expires[str(account_id)] = int(expires or 0)
        slots: dict[tuple[str, str], int] = {}
        for scope, scope_id, count in connection.execute(
            "SELECT scope, scope_id, COUNT(*) FROM generation_slots "
            "WHERE released_at IS NULL GROUP BY scope, scope_id"
        ):
            slots[(str(scope), str(scope_id))] = int(count)
        gate = read_restore_gate(source)
        if gate is not None:
            gate_active = gate_is_open(source, now=now)
            if gate_active:
                for floor in gate.get("slot_floors") or ():
                    if not isinstance(floor, dict):
                        continue
                    try:
                        key = (str(floor["scope"]), str(floor["scope_id"]))
                        slots[key] = slots.get(key, 0) + max(int(floor["count"]), 0)
                    except (KeyError, TypeError, ValueError):
                        continue
            now_ms = _now_ms(now)
            for floor in gate.get("usage_floors") or ():
                if not isinstance(floor, dict):
                    continue
                try:
                    if int(floor["window_start_ms"]) <= now_ms < int(floor["window_expires_ms"]):
                        account_id = str(floor["account_id"])
                        usage[account_id] = usage.get(account_id, 0) + max(
                            int(floor["count"]), 0
                        )
                        usage_expires[account_id] = max(
                            usage_expires.get(account_id, 0),
                            int(floor["window_expires_ms"]),
                        )
                except (KeyError, TypeError, ValueError):
                    continue
        revoked_approvals = {
            str(row[0])
            for row in connection.execute(
                "SELECT id FROM approvals WHERE revoked_at IS NOT NULL"
            )
        }
        revoked_deliveries = {
            str(row[0])
            for row in connection.execute(
                "SELECT id FROM deliveries WHERE status = 'revoked'"
            )
        }
        rejected_assets = {
            str(row[0])
            for row in connection.execute(
                "SELECT id FROM assets WHERE review_status = 'rejected'"
            )
        }
        accounts = {
            str(account_id): (password_hash, int(is_disabled))
            for account_id, password_hash, is_disabled in connection.execute(
                "SELECT id, password_hash, is_disabled FROM accounts"
            )
        }
    except sqlite3.Error as exc:
        raise BackupError(
            "SOURCE_DATABASE_UNREADABLE",
            f"could not read the current source: {type(exc).__name__}",
        ) from exc
    finally:
        connection.close()
    return _CurrentMetrics(
        usage=usage,
        usage_expires=usage_expires,
        slots=slots,
        revoked_approvals=revoked_approvals,
        revoked_deliveries=revoked_deliveries,
        rejected_assets=rejected_assets,
        accounts=accounts,
    )


def _now_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _staged_counts(database: Path) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    connection = sqlite3.connect(database)
    try:
        usage = {
            str(account_id): int(count)
            for account_id, count in connection.execute(
                "SELECT account_id, COUNT(*) FROM generation_usage GROUP BY account_id"
            )
        }
        slots = {
            (str(scope), str(scope_id)): int(count)
            for scope, scope_id, count in connection.execute(
                "SELECT scope, scope_id, COUNT(*) FROM generation_slots "
                "WHERE released_at IS NULL GROUP BY scope, scope_id"
            )
        }
    except sqlite3.Error as exc:
        raise BackupError(
            "RESTORE_DATABASE_UNREADABLE",
            f"could not read the restored database: {type(exc).__name__}",
        ) from exc
    finally:
        connection.close()
    return usage, slots


def _validate_staged_database(database: Path, expected_version: str) -> None:
    connection = sqlite3.connect(database)
    try:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        if not version or str(version[0]) != expected_version:
            raise BackupError(
                "RESTORE_SCHEMA_VERSION_MISMATCH",
                "restored database schema does not match the current application",
                details={"expected": expected_version, "actual": version[0] if version else None},
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        if [row[0] for row in integrity] != ["ok"]:
            raise BackupError(
                "RESTORE_DATABASE_INTEGRITY_FAILED",
                "restored database failed SQLite integrity_check",
                details={"result": [row[0] for row in integrity][:8]},
            )
        connection.execute("PRAGMA foreign_keys=ON")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise BackupError(
                "RESTORE_FOREIGN_KEY_VIOLATION",
                "restored database contains broken references",
                details={"count": len(foreign_keys), "first": str(foreign_keys[0])},
            )
        checks = (
            (
                "project_owner",
                "SELECT COUNT(*) FROM projects p LEFT JOIN accounts a "
                "ON p.owner_account_id = a.id WHERE p.owner_account_id IS NOT NULL "
                "AND a.id IS NULL",
            ),
            (
                "approval_artifact",
                "SELECT COUNT(*) FROM approvals ap LEFT JOIN artifacts ar "
                "ON ap.artifact_id = ar.id WHERE ap.artifact_id IS NOT NULL AND ar.id IS NULL",
            ),
            (
                "delivery_asset",
                "SELECT COUNT(*) FROM deliveries d LEFT JOIN assets a "
                "ON d.asset_id = a.id WHERE a.id IS NULL",
            ),
            (
                "delivery_job",
                "SELECT COUNT(*) FROM deliveries d LEFT JOIN generation_jobs j "
                "ON d.generation_job_id = j.id WHERE j.id IS NULL",
            ),
            (
                "job_owner",
                "SELECT COUNT(*) FROM generation_jobs j LEFT JOIN accounts a "
                "ON j.owner_account_id = a.id WHERE j.owner_account_id IS NOT NULL "
                "AND a.id IS NULL",
            ),
        )
        for name, query in checks:
            count = connection.execute(query).fetchone()
            if count and int(count[0]):
                raise BackupError(
                    "RESTORE_REFERENCE_INVALID",
                    "restored database failed reference validation",
                    details={"check": name, "count": int(count[0])},
                )
    except BackupError:
        raise
    except sqlite3.Error as exc:
        raise BackupError(
            "RESTORE_DATABASE_UNREADABLE",
            f"could not validate the restored database: {type(exc).__name__}",
        ) from exc
    finally:
        connection.close()


def _apply_restore_purge(
    database: Path, *, now: datetime, current: _CurrentMetrics
) -> dict[str, int]:
    now_text = format_timestamp(now)
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            sessions = connection.execute(
                "UPDATE sessions SET revoked_at = COALESCE(revoked_at, ?), "
                "revoked_reason = ?",
                (now_text, RESTORE_REVOKED_REASON),
            ).rowcount
            connection.execute("UPDATE accounts SET session_epoch = session_epoch + 1")
            links = connection.execute(
                "UPDATE account_links SET revoked_at = ?, revoked_reason = ? "
                "WHERE revoked_at IS NULL AND used_at IS NULL",
                (now_text, RESTORE_REVOKED_REASON),
            ).rowcount
            approvals = 0
            if current.revoked_approvals:
                placeholders = ",".join("?" for _ in current.revoked_approvals)
                approvals = connection.execute(
                    "UPDATE approvals SET revoked_at = ?, revocation_reason = ? "
                    f"WHERE id IN ({placeholders}) AND revoked_at IS NULL",
                    (now_text, "restore_revocation_floor", *sorted(current.revoked_approvals)),
                ).rowcount
            deliveries = 0
            if current.revoked_deliveries:
                placeholders = ",".join("?" for _ in current.revoked_deliveries)
                deliveries = connection.execute(
                    f"UPDATE deliveries SET status = 'revoked' "
                    f"WHERE id IN ({placeholders}) AND status != 'revoked'",
                    tuple(sorted(current.revoked_deliveries)),
                ).rowcount
            guarded = 0
            if current.rejected_assets:
                placeholders = ",".join("?" for _ in current.rejected_assets)
                guarded = connection.execute(
                    f"UPDATE assets SET review_status = 'rejected' "
                    f"WHERE id IN ({placeholders}) AND review_status != 'rejected'",
                    tuple(sorted(current.rejected_assets)),
                ).rowcount
            for account_id, (password_hash, is_disabled) in current.accounts.items():
                connection.execute(
                    "UPDATE accounts SET password_hash = ?, is_disabled = ? WHERE id = ?",
                    (password_hash, is_disabled, account_id),
                )
    except sqlite3.Error as exc:
        raise BackupError(
            "RESTORE_PURGE_FAILED",
            f"could not revoke restored credentials: {type(exc).__name__}",
        ) from exc
    finally:
        connection.close()
    return {
        "sessions_revoked": int(sessions),
        "links_revoked": int(links),
        "approvals_revoked": int(approvals),
        "deliveries_revoked": int(deliveries),
        "assets_guarded": int(guarded),
    }


def _insert_audit(
    database: Path,
    *,
    event_type: str,
    target_id: str,
    detail: dict[str, Any],
    now: datetime,
) -> None:
    connection = sqlite3.connect(database)
    try:
        with connection:
            connection.execute(
                "INSERT INTO audit_events "
                "(id, occurred_at, event_type, actor_kind, actor_account_id, "
                "target_account_id, target_kind, target_id, subject_ref, result, "
                "reason_code, correlation_id, source_ref, before_json, after_json, detail_json) "
                "VALUES (?, ?, ?, 'local_maintainer', NULL, NULL, 'maintenance', ?, NULL, "
                "'success', ?, ?, 'cli', NULL, NULL, ?)",
                (
                    str(uuid.uuid4()),
                    format_timestamp(now),
                    event_type,
                    target_id[:64],
                    "restore" if event_type.endswith("completed") else "restore_gap_cleared",
                    str(uuid.uuid4()),
                    json.dumps(detail, ensure_ascii=False, sort_keys=True)[:4096],
                ),
            )
    except sqlite3.Error as exc:
        raise BackupError(
            "RESTORE_AUDIT_FAILED",
            f"could not write restore audit: {type(exc).__name__}",
        ) from exc
    finally:
        connection.close()


def _build_usage_floors(
    current: _CurrentMetrics,
    staged: dict[str, int],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    now_ms = _now_ms(now)
    floors: list[dict[str, Any]] = []
    for account_id, current_count in current.usage.items():
        extra = max(current_count - int(staged.get(account_id, 0)), 0)
        if extra:
            expires = current.usage_expires.get(account_id, 0)
            if expires <= now_ms:
                expires = now_ms + 24 * 60 * 60 * 1000
            floors.append(
                {
                    "account_id": account_id,
                    "window_start_ms": now_ms - 24 * 60 * 60 * 1000,
                    "window_expires_ms": expires,
                    "count": extra,
                }
            )
    return floors


def _build_slot_floors(
    current: _CurrentMetrics, staged: dict[tuple[str, str], int]
) -> list[dict[str, Any]]:
    floors: list[dict[str, Any]] = []
    for (scope, scope_id), current_count in current.slots.items():
        extra = max(current_count - int(staged.get((scope, scope_id), 0)), 0)
        if extra:
            floors.append({"scope": scope, "scope_id": scope_id, "count": extra})
    return floors


def _switch_source(source: Path, staged_data: Path) -> None:
    quarantine = source.parent / f".restore-old-{uuid.uuid4().hex}"
    os.replace(source, quarantine)
    try:
        os.replace(staged_data, source)
    except BaseException:
        if source.exists():
            shutil.rmtree(source, ignore_errors=True)
        with contextlib.suppress(OSError):
            os.replace(quarantine, source)
        raise
    else:
        shutil.rmtree(quarantine, ignore_errors=True)


def restore_switch(
    *,
    source: str | Path,
    backup: str | Path,
    output: str | Path,
    confirmation: str,
    service_stopped: bool = False,
    allow_real_data_root: bool = False,
    gap_seconds: int = GAP_SECONDS,
    now: datetime | None = None,
) -> RestoreSwitchResult:
    """Restore one verified snapshot in place after an explicit offline confirmation."""
    if not service_stopped:
        raise BackupError(
            "SERVICE_MUST_BE_STOPPED",
            "restore is offline-only; stop the service and pass --service-stopped",
        )
    if confirmation != RESTORE_CONFIRMATION:
        raise BackupError(
            "RESTORE_CONFIRMATION_REQUIRED",
            f"restore requires explicit confirmation {RESTORE_CONFIRMATION}",
        )
    source_path = Path(source).expanduser().resolve()
    _assert_source_allowed(source_path, allow_real_data_root=allow_real_data_root)
    if not source_path.is_dir() or source_path.is_symlink():
        raise BackupError("SOURCE_NOT_DATA_ROOT", f"source is not a data directory: {source_path}")
    backup_path = Path(backup).expanduser().resolve()
    if not backup_path.is_dir():
        raise BackupError("BACKUP_NOT_FOUND", f"backup directory does not exist: {backup_path}")
    report = verify_backup(backup_path)
    manifest, manifest_digest = load_manifest(backup_path)
    expected_version = head_revision()
    backup_version = str(manifest.database.get("alembic_version") or "")
    if backup_version != expected_version:
        raise BackupError(
            "RESTORE_SCHEMA_VERSION_MISMATCH",
            "backup schema does not match the current application",
            details={"expected": expected_version, "actual": backup_version},
        )
    if report.ok is not True:
        raise BackupError("BACKUP_VERIFY_FAILED", "selected backup did not verify")
    inspection = inspect_candidate(source_path)
    if not inspection.app_db_present:
        raise BackupError("SOURCE_NOT_DATA_ROOT", f"source has no {DB_NAME}: {source_path}")
    output_path = _validate_output(source_path, Path(output), [inspection])
    current_time = now or utc_now()
    safety = _create_backup_sync(source_path, inspection, output_path)
    stage_root = Path(tempfile.mkdtemp(prefix=".restore-", dir=str(source_path.parent)))
    stage_backup = stage_root / "snapshot"
    try:
        shutil.copytree(backup_path, stage_backup, symlinks=True)
        verify_backup(stage_backup)
        stage_data = stage_backup / DATA_DIR_NAME
        stage_db = stage_data / DB_NAME
        _validate_staged_database(stage_db, backup_version)
        current = _read_current_metrics(source_path, current_time)
        staged_usage, staged_slots = _staged_counts(stage_db)
        usage_floors = _build_usage_floors(current, staged_usage, now=current_time)
        slot_floors = _build_slot_floors(current, staged_slots)
        counts = _apply_restore_purge(stage_db, now=current_time, current=current)
        _validate_staged_database(stage_db, backup_version)
        gate = build_gate(
            backup_id=manifest.backup_id,
            restored_at=current_time,
            gap_seconds=gap_seconds,
            usage_floors=usage_floors,
            slot_floors=slot_floors,
            **counts,
        )
        write_restore_gate(stage_data, gate)
        _insert_audit(
            stage_db,
            event_type="maintenance_restore_completed",
            target_id=manifest.backup_id,
            detail={
                "backup_id": manifest.backup_id,
                "safety_backup_id": safety.backup_id,
                "sessions_revoked": counts["sessions_revoked"],
                "links_revoked": counts["links_revoked"],
                "approvals_revoked": counts["approvals_revoked"],
                "deliveries_revoked": counts["deliveries_revoked"],
                "assets_guarded": counts["assets_guarded"],
                "gap_expires_at": gate["gap_expires_at"],
            },
            now=current_time,
        )
        _switch_source(source_path, stage_data)
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
    return RestoreSwitchResult(
        backup_id=manifest.backup_id,
        restored_dir=source_path,
        safety_backup_id=safety.backup_id,
        safety_backup_dir=safety.backup_dir,
        manifest_sha256=manifest_digest,
        file_count=report.file_count,
        total_bytes=report.total_bytes,
        sessions_revoked=counts["sessions_revoked"],
        links_revoked=counts["links_revoked"],
        approvals_revoked=counts["approvals_revoked"],
        deliveries_revoked=counts["deliveries_revoked"],
        assets_guarded=counts["assets_guarded"],
        usage_floor_accounts=len(usage_floors),
        gap_expires_at=gate["gap_expires_at"],
        verified=True,
    )


def clear_restore_gap(
    *,
    source: str | Path,
    confirmation: str,
    reason: str,
    service_stopped: bool = False,
    allow_real_data_root: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not service_stopped:
        raise BackupError(
            "SERVICE_MUST_BE_STOPPED",
            "gap clearing is offline-only; stop the service and pass --service-stopped",
        )
    if confirmation != CLEAR_CONFIRMATION:
        raise BackupError(
            "CLEAR_CONFIRMATION_REQUIRED",
            f"clearing the restore gap requires explicit confirmation {CLEAR_CONFIRMATION}",
        )
    source_path = Path(source).expanduser().resolve()
    _assert_source_allowed(source_path, allow_real_data_root=allow_real_data_root)
    gate = read_restore_gate(source_path)
    if gate is None:
        raise BackupError("RESTORE_GAP_NOT_FOUND", "no restore gate exists in the data root")
    database = source_path / DB_NAME
    if not database.is_file():
        raise BackupError("SOURCE_NOT_DATA_ROOT", f"source has no {DB_NAME}: {source_path}")
    current_time = now or utc_now()
    if gate.get("status") == GATE_STATUS_OPEN:
        updated = clear_restore_gate(source_path, reason=reason, now=current_time)
        if updated is None:
            raise BackupError("RESTORE_GAP_NOT_FOUND", "restore gate disappeared")
        _insert_audit(
            database,
            event_type="maintenance_restore_gap_cleared",
            target_id=str(updated.get("backup_id") or "restore"),
            detail={
                "backup_id": updated.get("backup_id"),
                "reason": reason,
                "cleared_at": updated.get("cleared_at"),
            },
            now=current_time,
        )
    else:
        updated = gate
    return {
        "ok": True,
        "status": updated.get("status"),
        "backup_id": updated.get("backup_id"),
        "cleared_at": updated.get("cleared_at"),
        "cleared_by": updated.get("cleared_by"),
        "already_cleared": gate.get("status") == GATE_STATUS_CLEARED,
    }


__all__ = [
    "CLEAR_CONFIRMATION",
    "RESTORE_CONFIRMATION",
    "RestoreSwitchResult",
    "clear_restore_gap",
    "restore_switch",
]

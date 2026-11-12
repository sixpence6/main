from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.maintenance.backup import (
    STATUS_NAME,
    BackupResult,
    _create_backup_sync,
    _validate_output,
    prune_verified_backups,
)
from app.maintenance.coordinator import MaintenanceCoordinator, MaintenanceTimeout
from app.maintenance.errors import BackupError
from app.maintenance.sources import inspect_candidate, select_source
from app.timeutil import format_timestamp, utc_now

logger = logging.getLogger("app.maintenance")

STATUS_SCHEMA = "maintenance-backup-status/v1"
RETRY_DELAY_SECONDS = 300


@dataclass(frozen=True)
class BackupCycleResult:
    backup: BackupResult
    pruned: tuple[str, ...]
    rotation_skipped: tuple[dict[str, str], ...]
    status: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "backup": self.backup.as_dict(),
            "pruned": list(self.pruned),
            "rotation_skipped": list(self.rotation_skipped),
            "status": self.status,
        }


def _status_path(output: str | Path) -> Path:
    return Path(output).expanduser().resolve() / STATUS_NAME


def read_status(output: str | Path) -> dict[str, Any] | None:
    path = _status_path(output)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_status(output: Path, payload: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    path = output / STATUS_NAME
    temporary = output / f".{STATUS_NAME}.{os.getpid()}.tmp"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def next_backup_due(
    status: dict[str, Any] | None,
    *,
    now: datetime,
    interval_seconds: int,
) -> datetime:
    """Return when the next scheduled attempt is due.

    A missing status means this is a fresh start and the first backup is due
    immediately. Older status files without next_due_at fall back to the last
    success plus the interval, so an upgrade still catches up.
    """
    if not status:
        return now
    last_success = parse_timestamp(status.get("last_success_at"))
    due = parse_timestamp(status.get("next_due_at"))
    if due is not None:
        if last_success is None:
            return due
        # A shortened configured interval takes effect immediately instead of
        # waiting for a next_due_at persisted under the previous interval.
        horizon = last_success + timedelta(seconds=max(interval_seconds, 1))
        return min(due, horizon)
    if last_success is not None:
        return last_success + timedelta(seconds=max(interval_seconds, 1))
    return now


def _failure_status(
    *,
    previous: dict[str, Any] | None,
    source: Path,
    output: Path,
    attempt_at: str,
    interval_seconds: int,
    keep: int,
    failure: BackupError,
) -> dict[str, Any]:
    now = parse_timestamp(attempt_at) or utc_now()
    retry_at = now + timedelta(seconds=min(max(interval_seconds, 1), RETRY_DELAY_SECONDS))
    return {
        "schema": STATUS_SCHEMA,
        "source": str(source),
        "output": str(output),
        "last_attempt_at": attempt_at,
        "last_success_at": (previous or {}).get("last_success_at"),
        "last_failure_at": attempt_at,
        "last_error": {"code": failure.code, "message": failure.message},
        "backup_id": (previous or {}).get("backup_id"),
        "backup_dir": (previous or {}).get("backup_dir"),
        "next_due_at": format_timestamp(retry_at),
        "keep": keep,
        "interval_seconds": interval_seconds,
        "pruned": [],
        "rotation_skipped": [],
    }


def _success_status(
    *,
    previous: dict[str, Any] | None,
    result: BackupResult,
    output: Path,
    attempt_at: str,
    interval_seconds: int,
    keep: int,
    pruned: tuple[str, ...],
    rotation_skipped: tuple[dict[str, str], ...],
) -> dict[str, Any]:
    completed = parse_timestamp(result.created_at) or utc_now()
    return {
        "schema": STATUS_SCHEMA,
        "source": str(result.source),
        "output": str(output),
        "last_attempt_at": attempt_at,
        "last_success_at": result.created_at,
        "last_failure_at": (previous or {}).get("last_failure_at"),
        "last_error": None,
        "backup_id": result.backup_id,
        "backup_dir": result.backup_dir.name,
        "next_due_at": format_timestamp(completed + timedelta(seconds=max(interval_seconds, 1))),
        "keep": keep,
        "interval_seconds": interval_seconds,
        "pruned": list(pruned),
        "rotation_skipped": list(rotation_skipped),
    }


async def run_backup_cycle(
    *,
    source: str | Path,
    output: str | Path,
    keep: int = 7,
    timeout: float = 60.0,  # noqa: ASYNC109
    interval_seconds: int = 86400,
    coordinator: MaintenanceCoordinator | None = None,
    now: datetime | None = None,
) -> BackupCycleResult:
    """Create, independently verify, publish and rotate one scheduled backup.

    The source is the explicitly configured APP_DATA_DIR, so the scheduler does
    not auto-discover sibling roots. It never exposes an HTTP export and only
    deletes old backup directories after each one still verifies.
    """
    source_path = Path(source).expanduser().resolve()  # noqa: ASYNC240
    output_path = Path(output).expanduser().resolve()  # noqa: ASYNC240
    attempt_at = format_timestamp(now or utc_now())
    previous = await asyncio.to_thread(read_status, output_path)
    output_ready = False
    try:
        decision = select_source(source_path, candidate_roots=(source_path,))
        output_path = _validate_output(source_path, output_path, decision.candidates)
        output_ready = True
        gate = coordinator or MaintenanceCoordinator()
        async with gate.maintenance(timeout=timeout):
            current = await asyncio.to_thread(inspect_candidate, source_path)
            if not current.app_db_present:
                raise BackupError(
                    "SOURCE_NOT_DATA_ROOT", "configured source disappeared before backup"
                )
            result = await asyncio.to_thread(
                _create_backup_sync,
                source_path,
                current,
                output_path,
                periodic_scheduler=True,
            )
            rotation = await asyncio.to_thread(
                prune_verified_backups,
                output_path,
                keep=keep,
                protect=(result.backup_dir.name,),
            )
    except MaintenanceTimeout as exc:
        failure = BackupError(
            "MAINTENANCE_TIMEOUT",
            "in-flight writes did not drain before the backup timeout",
            details={"timeout": exc.timeout, "in_flight": exc.in_flight},
        )
        if output_ready:
            try:
                _write_status(
                    output_path,
                    _failure_status(
                        previous=previous,
                        source=source_path,
                        output=output_path,
                        attempt_at=attempt_at,
                        interval_seconds=interval_seconds,
                        keep=keep,
                        failure=failure,
                    ),
                )
            except OSError:
                logger.exception("could not record backup timeout status")
        raise failure from exc
    except BackupError as exc:
        if output_ready:
            try:
                _write_status(
                    output_path,
                    _failure_status(
                        previous=previous,
                        source=source_path,
                        output=output_path,
                        attempt_at=attempt_at,
                        interval_seconds=interval_seconds,
                        keep=keep,
                        failure=exc,
                    ),
                )
            except OSError:
                logger.exception("could not record backup failure status")
        raise
    status = _success_status(
        previous=previous,
        result=result,
        output=output_path,
        attempt_at=attempt_at,
        interval_seconds=interval_seconds,
        keep=keep,
        pruned=rotation.pruned,
        rotation_skipped=rotation.skipped,
    )
    try:
        _write_status(output_path, status)
    except OSError as exc:
        raise BackupError(
            "STATUS_WRITE_FAILED", "verified backup exists but status could not be written"
        ) from exc
    logger.info("scheduled backup completed: %s", result.backup_id)
    return BackupCycleResult(
        backup=result,
        pruned=rotation.pruned,
        rotation_skipped=rotation.skipped,
        status=status,
    )


async def backup_loop(app: Any) -> None:
    """Lifespan task: catch up on startup, then keep the 24h cadence."""
    settings = app.state.settings
    output = settings.resolved_backup_dir()
    if output is None:
        return
    source = settings.resolved_data_dir()
    interval = settings.backup_interval_seconds
    while True:
        try:
            status = await asyncio.to_thread(read_status, output)
            now = utc_now()
            due = next_backup_due(status, now=now, interval_seconds=interval)
            delay = (due - now).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
                continue
            await run_backup_cycle(
                source=source,
                output=output,
                keep=settings.backup_keep,
                timeout=float(settings.backup_timeout_seconds),
                interval_seconds=interval,
                coordinator=app.state.maintenance,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduled backup cycle failed")
            await asyncio.sleep(RETRY_DELAY_SECONDS)


__all__ = [
    "BackupCycleResult",
    "RETRY_DELAY_SECONDS",
    "STATUS_SCHEMA",
    "backup_loop",
    "next_backup_due",
    "parse_timestamp",
    "read_status",
    "run_backup_cycle",
]

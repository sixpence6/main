from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import DEFAULT_DATA_DIR, MAIN_DIR
from app.maintenance.coordinator import MaintenanceCoordinator, MaintenanceTimeout
from app.maintenance.errors import BackupError
from app.maintenance.manifest import (
    DATA_DIR_NAME,
    BackupFile,
    Manifest,
    verify_backup,
    write_manifest,
)
from app.maintenance.sources import (
    DATA_SUBDIRS,
    DB_NAME,
    SHM_NAME,
    WAL_NAME,
    CandidateInspection,
    hash_file,
    inspect_candidate,
    is_excluded_name,
    is_safe_regular_file,
    select_source,
)
from app.timeutil import format_timestamp, utc_now

LATEST_NAME = "latest.json"
LATEST_SCHEMA = "maintenance-latest/v1"
BACKUP_PREFIX = "backup-"
STAGING_PREFIX = ".staging-"
VERIFY_PREFIX = ".verify-"
RESTORE_CONFIRMATION = "REHEARSE"

_KIND_BY_SUBDIR = {
    "references": "reference",
    "generated": "generated",
    "published": "published",
}


@dataclass(frozen=True)
class DatabaseSnapshot:
    byte_size: int
    sha256: str
    alembic_version: str
    asset_count: int
    rows: tuple[tuple[Any, ...], ...]


@dataclass(frozen=True)
class BackupResult:
    backup_id: str
    backup_dir: Path
    manifest_sha256: str
    source: Path
    source_digest: str
    created_at: str
    verified: bool
    file_count: int
    total_bytes: int
    missing_files: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "backup_id": self.backup_id,
            "backup_dir": str(self.backup_dir),
            "manifest_sha256": self.manifest_sha256,
            "source": str(self.source),
            "source_digest": self.source_digest,
            "created_at": self.created_at,
            "verified": self.verified,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "missing_files": self.missing_files,
        }


@dataclass(frozen=True)
class RestoreResult:
    backup_id: str
    restored_dir: Path
    manifest_sha256: str
    file_count: int
    total_bytes: int
    verified: bool
    production_switched: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "backup_id": self.backup_id,
            "restored_dir": str(self.restored_dir),
            "manifest_sha256": self.manifest_sha256,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "verified": self.verified,
            "production_switched": self.production_switched,
        }


def _utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _protected_roots() -> tuple[Path, ...]:
    return (
        Path(DEFAULT_DATA_DIR).expanduser().resolve(),
        (Path(MAIN_DIR) / "backend" / "data").expanduser().resolve(),
    )


def _assert_output_not_protected(path: Path) -> None:
    for protected in _protected_roots():
        if path == protected or path.is_relative_to(protected) or protected.is_relative_to(path):
            raise BackupError(
                "OUTPUT_FORBIDDEN",
                f"output directory overlaps a real data root: {protected}",
                details={"output": str(path)},
            )


def _validate_output(source: Path, output: Path, candidates: Sequence[CandidateInspection]) -> Path:
    resolved = output.expanduser().resolve()
    if resolved == source or resolved.is_relative_to(source) or source.is_relative_to(resolved):
        raise BackupError(
            "OUTPUT_OVERLAPS_SOURCE",
            "backup output must not overlap the selected source",
            details={"source": str(source), "output": str(resolved)},
        )
    for candidate in candidates:
        root = candidate.root
        if resolved == root or resolved.is_relative_to(root) or root.is_relative_to(resolved):
            raise BackupError(
                "OUTPUT_OVERLAPS_CANDIDATE",
                f"backup output overlaps a data source candidate: {root}",
            )
    _assert_output_not_protected(resolved)
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise BackupError("OUTPUT_NOT_DIRECTORY", "backup output is not a directory")
        for entry in resolved.iterdir():
            allowed = (
                entry.name == LATEST_NAME
                or entry.name.startswith(BACKUP_PREFIX)
                or entry.name.startswith(STAGING_PREFIX)
                or entry.name.startswith(VERIFY_PREFIX)
                or entry.name.startswith(".latest.json")
            )
            if not allowed:
                raise BackupError(
                    "OUTPUT_NOT_RESTRICTED",
                    "backup output contains files this tool does not own",
                    details={"unexpected_entry": entry.name},
                )
    else:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _source_db_uri(path: Path, *, immutable: bool) -> str:
    query = "mode=ro&immutable=1" if immutable else "mode=ro"
    return path.as_uri() + "?" + query


def _snapshot_database(source_db: Path, destination: Path) -> DatabaseSnapshot:
    source_root = source_db.parent
    has_wal = (source_root / WAL_NAME).exists()
    if has_wal and not (source_root / SHM_NAME).exists():
        raise BackupError(
            "SOURCE_WAL_UNREADABLE",
            "source has a WAL file without shared memory; refusing to touch it",
        )
    try:
        source_connection = sqlite3.connect(
            _source_db_uri(source_db, immutable=not has_wal), uri=True, timeout=5.0
        )
    except sqlite3.Error as exc:
        raise BackupError(
            "SOURCE_DATABASE_UNREADABLE", f"could not open source database: {type(exc).__name__}"
        ) from exc
    try:
        source_connection.execute("PRAGMA query_only=ON")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
            journal = destination_connection.execute("PRAGMA journal_mode=DELETE").fetchone()
            if not journal or str(journal[0]).lower() != "delete":
                raise BackupError(
                    "SOURCE_SNAPSHOT_JOURNAL_MODE",
                    "could not make the database snapshot self-contained",
                )
            integrity = destination_connection.execute("PRAGMA integrity_check").fetchall()
            if [row[0] for row in integrity] != ["ok"]:
                raise BackupError(
                    "SOURCE_DATABASE_INTEGRITY_FAILED",
                    "source database failed SQLite integrity_check",
                    details={"result": [row[0] for row in integrity][:8]},
                )
            try:
                version_row = destination_connection.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()
            except sqlite3.Error as exc:
                raise BackupError(
                    "SOURCE_SCHEMA_MISSING", "source database has no alembic_version table"
                ) from exc
            if not version_row:
                raise BackupError("SOURCE_SCHEMA_MISSING", "source database has no schema version")
            try:
                rows = tuple(
                    destination_connection.execute(
                        "SELECT id, kind, relative_path, sha256, byte_size "
                        "FROM assets ORDER BY relative_path, id"
                    ).fetchall()
                )
            except sqlite3.Error as exc:
                raise BackupError(
                    "SOURCE_SCHEMA_MISSING", "source database has no assets table"
                ) from exc
        finally:
            destination_connection.close()
    except BackupError:
        raise
    except sqlite3.Error as exc:
        raise BackupError(
            "SOURCE_DATABASE_UNREADABLE", f"SQLite backup failed: {type(exc).__name__}"
        ) from exc
    finally:
        source_connection.close()
    for suffix in (WAL_NAME, SHM_NAME):
        leftover = destination.parent / suffix
        if leftover.exists():
            leftover.unlink()
    byte_size, sha256 = hash_file(destination)
    return DatabaseSnapshot(
        byte_size=byte_size,
        sha256=sha256,
        alembic_version=str(version_row[0]),
        asset_count=len(rows),
        rows=rows,
    )


def _safe_asset_relative(raw: str) -> str:
    path = Path(str(raw))
    if path.is_absolute() or path.drive:
        raise BackupError("SOURCE_FILE_UNSAFE", "database references an absolute asset path")
    parts = path.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise BackupError("SOURCE_FILE_UNSAFE", "database references a non-canonical asset path")
    if parts[0] not in DATA_SUBDIRS:
        raise BackupError(
            "SOURCE_FILE_UNSAFE",
            "database references a file outside the allowlisted data directories",
        )
    if is_excluded_name(parts[-1]):
        raise BackupError("SOURCE_FILE_UNSAFE", "database references a secret-like file")
    return "/".join(parts)


def _copy_file_with_hash(source: Path, destination: Path) -> tuple[int, str]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as reader, destination.open("wb") as writer:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(chunk)
            digest.update(chunk)
            total += len(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return total, digest.hexdigest()


def _copy_asset_files(
    source_root: Path, rows: Sequence[tuple[Any, ...]], data_dir: Path
) -> tuple[tuple[BackupFile, ...], tuple[dict[str, Any], ...], int]:
    files: dict[str, BackupFile] = {}
    missing: list[dict[str, Any]] = []
    for row in rows:
        asset_id, kind, relative_path, sha256, byte_size = row
        relative = _safe_asset_relative(relative_path)
        if relative in files:
            continue
        source_file = source_root / relative
        if not source_file.exists():
            missing.append(
                {"asset_id": str(asset_id), "relative_path": relative, "kind": str(kind)}
            )
            continue
        if not is_safe_regular_file(source_file, source_root):
            raise BackupError(
                "SOURCE_FILE_UNSAFE", f"asset file is not a safe regular file: {relative}"
            )
        byte_count, digest = _copy_file_with_hash(source_file, data_dir / relative)
        expected = (str(sha256) if sha256 else "").strip().lower()
        if expected and expected != digest:
            raise BackupError(
                "SOURCE_FILE_DIGEST_MISMATCH",
                f"asset file digest does not match the database: {relative}",
                details={"expected": expected, "actual": digest},
            )
        if int(byte_size) != byte_count:
            raise BackupError(
                "SOURCE_FILE_SIZE_MISMATCH",
                f"asset file size does not match the database: {relative}",
            )
        files[relative] = BackupFile(
            relative_path=relative,
            kind=_KIND_BY_SUBDIR[relative.split("/", 1)[0]],
            byte_size=byte_count,
            sha256=digest,
        )
    if missing:
        raise BackupError(
            "SOURCE_FILE_MISSING",
            f"{len(missing)} database-referenced file(s) are missing from the source",
            details={"missing": missing},
        )
    ordered = tuple(files[key] for key in sorted(files))
    total = sum(item.byte_size for item in ordered)
    return ordered, tuple(missing), total


def _assert_no_secret_files(data_dir: Path) -> None:
    for _current, _dirnames, filenames in os.walk(data_dir, followlinks=False):
        for name in filenames:
            if is_excluded_name(name):
                raise BackupError(
                    "SECRET_FILE_PRESENT", f"refusing to publish secret-like file: {name}"
                )


def _unique_final_dir(output: Path, backup_id: str) -> Path:
    candidate = output / f"{BACKUP_PREFIX}{backup_id}"
    if not candidate.exists():
        return candidate
    for _ in range(10):
        candidate = output / f"{BACKUP_PREFIX}{backup_id}-{uuid.uuid4().hex[:6]}"
        if not candidate.exists():
            return candidate
    raise BackupError("OUTPUT_BACKUP_ID_COLLISION", "could not allocate a unique backup directory")


def _write_latest(output: Path, result: BackupResult) -> None:
    payload = {
        "schema": LATEST_SCHEMA,
        "backup_id": result.backup_id,
        "backup_dir": result.backup_dir.name,
        "manifest_sha256": result.manifest_sha256,
        "source": str(result.source),
        "created_at": result.created_at,
        "verified": True,
    }
    temporary = output / f".{LATEST_NAME}.{os.getpid()}.tmp"
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, output / LATEST_NAME)


def read_latest(output: str | Path) -> dict[str, Any] | None:
    path = Path(output).expanduser().resolve() / LATEST_NAME
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _create_backup_sync(
    source: Path, inspection: CandidateInspection, output: Path
) -> BackupResult:
    created_at = format_timestamp(utc_now())
    backup_id = f"{_utc_stamp()}-{uuid.uuid4().hex[:8]}"
    staging = output / f"{STAGING_PREFIX}{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        data_dir = staging / DATA_DIR_NAME
        data_dir.mkdir()
        snapshot = _snapshot_database(source / DB_NAME, data_dir / "app.db")
        files, missing, total = _copy_asset_files(source, snapshot.rows, data_dir)
        _assert_no_secret_files(data_dir)
        database = inspection.database
        manifest = Manifest(
            backup_id=backup_id,
            created_at=created_at,
            source={
                "root": str(source),
                "content_digest": inspection.content_digest,
                "database_sha256": database.sha256 if database else None,
                "wal_byte_size": database.wal_byte_size if database else 0,
            },
            database={
                "relative_path": "app.db",
                "byte_size": snapshot.byte_size,
                "snapshot_sha256": snapshot.sha256,
                "alembic_version": snapshot.alembic_version,
                "asset_count": snapshot.asset_count,
                "integrity_check": "ok",
                "journal_mode": "delete",
            },
            files=files,
            missing=missing,
            boundaries={
                "included_dirs": list(DATA_SUBDIRS),
                "excluded": [".env", "TLS private keys", "model key material"],
                "web_whole_db_export": False,
                "periodic_scheduler": False,
                "production_restore_switch": False,
            },
        )
        write_manifest(staging, manifest)
        isolated = Path(tempfile.mkdtemp(prefix=VERIFY_PREFIX, dir=output))
        try:
            report = verify_backup(staging, isolated_dir=isolated)
        finally:
            shutil.rmtree(isolated, ignore_errors=True)
        if not report.ok:
            raise BackupError("BACKUP_VERIFY_FAILED", "staged backup did not verify")
        final = _unique_final_dir(output, backup_id)
        os.replace(staging, final)
        result = BackupResult(
            backup_id=backup_id,
            backup_dir=final,
            manifest_sha256=report.manifest_sha256,
            source=source,
            source_digest=inspection.content_digest,
            created_at=created_at,
            verified=True,
            file_count=report.file_count,
            total_bytes=report.total_bytes,
            missing_files=report.missing_files,
        )
        _write_latest(output, result)
        return result
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


async def create_backup(  # noqa: ASYNC109
    *,
    source: str | Path,
    output: str | Path,
    timeout: float = 30.0,  # noqa: ASYNC109
    confirm_source_digest: str | None = None,
    candidate_roots: Sequence[str | Path] | None = None,
    coordinator: MaintenanceCoordinator | None = None,
) -> BackupResult:
    """Create one verified backup from exactly one explicitly selected source."""
    source_path = Path(source).expanduser().resolve()  # noqa: ASYNC240
    decision = select_source(
        source_path,
        candidate_roots=candidate_roots,
        confirm_source_digest=confirm_source_digest,
    )
    output_path = _validate_output(source_path, Path(output), decision.candidates)
    gate = coordinator or MaintenanceCoordinator()
    try:
        async with gate.maintenance(timeout=timeout):
            current = await asyncio.to_thread(inspect_candidate, source_path)
            if not current.app_db_present:
                raise BackupError(
                    "SOURCE_NOT_DATA_ROOT", "selected source disappeared before backup"
                )
            if (
                confirm_source_digest is not None
                and current.content_digest != confirm_source_digest
            ):
                raise BackupError(
                    "SOURCE_DIGEST_MISMATCH",
                    "selected source changed before the maintenance window opened; "
                    "inspect it again",
                    details={
                        "expected": confirm_source_digest,
                        "actual": current.content_digest,
                    },
                )
            return await asyncio.to_thread(_create_backup_sync, source_path, current, output_path)
    except MaintenanceTimeout as exc:
        raise BackupError(
            "MAINTENANCE_TIMEOUT",
            "in-flight writes did not drain before the backup timeout",
            details={"timeout": exc.timeout, "in_flight": exc.in_flight},
        ) from exc


def _validate_restore_output(source: Path, output: Path) -> Path:
    resolved = output.expanduser().resolve()
    if resolved == source or resolved.is_relative_to(source) or source.is_relative_to(resolved):
        raise BackupError(
            "RESTORE_OUTPUT_OVERLAPS_BACKUP",
            "restore rehearsal output must not overlap the backup",
        )
    _assert_output_not_protected(resolved)
    if resolved.exists():
        if resolved.is_symlink() or not resolved.is_dir():
            raise BackupError("RESTORE_OUTPUT_NOT_DIRECTORY", "restore output is not a directory")
        if any(resolved.iterdir()):
            raise BackupError(
                "RESTORE_OUTPUT_NOT_EMPTY",
                "restore rehearsal requires an empty output directory",
            )
    else:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def restore_verify(*, backup: str | Path, output: str | Path, confirmation: str) -> RestoreResult:
    """Copy a verified backup into an isolated empty directory; never switch production."""
    if confirmation != RESTORE_CONFIRMATION:
        raise BackupError(
            "RESTORE_CONFIRMATION_REQUIRED",
            f"restore rehearsal requires explicit confirmation {RESTORE_CONFIRMATION}",
        )
    source = Path(backup).expanduser().resolve()
    if not source.is_dir():
        raise BackupError("BACKUP_NOT_FOUND", f"backup directory does not exist: {source}")
    target = _validate_restore_output(source, Path(output))
    created = not any(target.iterdir())
    try:
        verify_backup(source)
        shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
        restored = verify_backup(target)
        return RestoreResult(
            backup_id=restored.backup_id,
            restored_dir=target,
            manifest_sha256=restored.manifest_sha256,
            file_count=restored.file_count,
            total_bytes=restored.total_bytes,
            verified=True,
        )
    except BaseException:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise


def inspect_sources_report(candidate_roots: Sequence[str | Path] | None = None) -> dict[str, Any]:
    from app.maintenance.sources import default_candidate_roots, discover_candidates

    roots = tuple(candidate_roots) if candidate_roots is not None else default_candidate_roots()
    candidates = discover_candidates(roots)
    non_empty = [item for item in candidates if item.non_empty]
    return {
        "schema": "maintenance-sources/v1",
        "generated_at": format_timestamp(utc_now()),
        "candidates": [item.as_dict() for item in candidates],
        "non_empty_count": len(non_empty),
        "requires_explicit_choice": len(non_empty) > 1,
        "note": "no data was modified; pass --source and, when required, --confirm-source-digest",
    }


__all__ = [
    "BACKUP_PREFIX",
    "BackupResult",
    "LATEST_NAME",
    "LATEST_SCHEMA",
    "RESTORE_CONFIRMATION",
    "RestoreResult",
    "create_backup",
    "inspect_sources_report",
    "read_latest",
    "restore_verify",
    "verify_backup",
]

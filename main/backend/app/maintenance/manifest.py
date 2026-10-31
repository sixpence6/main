from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.maintenance.errors import BackupIntegrityError
from app.maintenance.sources import DATA_SUBDIRS, is_excluded_name, is_safe_regular_file

MANIFEST_SCHEMA = "maintenance-backup/v1"
MANIFEST_NAME = "manifest.json"
MANIFEST_HASH_NAME = "manifest.sha256"
DATA_DIR_NAME = "data"


@dataclass(frozen=True)
class BackupFile:
    relative_path: str
    kind: str
    byte_size: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "kind": self.kind,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class Manifest:
    backup_id: str
    created_at: str
    source: dict[str, Any]
    database: dict[str, Any]
    files: tuple[BackupFile, ...]
    missing: tuple[dict[str, Any], ...]
    boundaries: dict[str, Any]
    schema: str = MANIFEST_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "source": self.source,
            "database": self.database,
            "files": [item.as_dict() for item in self.files],
            "missing": list(self.missing),
            "boundaries": self.boundaries,
        }


@dataclass(frozen=True)
class VerificationReport:
    backup_id: str
    manifest_sha256: str
    file_count: int
    total_bytes: int
    database_bytes: int
    database_sha256: str
    alembic_version: str
    asset_count: int
    missing_files: int
    checks: tuple[str, ...]
    isolated: bool = False

    @property
    def ok(self) -> bool:
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "manifest_sha256": self.manifest_sha256,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "database_bytes": self.database_bytes,
            "database_sha256": self.database_sha256,
            "alembic_version": self.alembic_version,
            "asset_count": self.asset_count,
            "missing_files": self.missing_files,
            "checks": list(self.checks),
            "isolated": self.isolated,
            "ok": True,
        }


def canonical_manifest_bytes(manifest: Manifest) -> bytes:
    payload = json.dumps(
        manifest.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return payload.encode("utf-8") + b"\n"


def manifest_digest(manifest: Manifest) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def write_manifest(backup_dir: Path, manifest: Manifest) -> str:
    backup_dir.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(manifest.as_dict(), ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        + b"\n"
    )
    digest = hashlib.sha256(payload).hexdigest()
    manifest_path = backup_dir / MANIFEST_NAME
    temporary = backup_dir / f".{MANIFEST_NAME}.{os.getpid()}.tmp"
    temporary.write_bytes(payload)
    os.replace(temporary, manifest_path)
    (backup_dir / MANIFEST_HASH_NAME).write_text(f"{digest}  {MANIFEST_NAME}\n", encoding="utf-8")
    return digest


def load_manifest(backup_dir: Path) -> tuple[Manifest, str]:
    manifest_path = backup_dir / MANIFEST_NAME
    hash_path = backup_dir / MANIFEST_HASH_NAME
    if not manifest_path.is_file() or not hash_path.is_file():
        raise BackupIntegrityError(
            "MANIFEST_MISSING", f"backup does not contain {MANIFEST_NAME} and its digest"
        )
    payload = manifest_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    try:
        recorded = hash_path.read_text(encoding="utf-8").split()[0]
    except (OSError, IndexError) as exc:
        raise BackupIntegrityError(
            "MANIFEST_HASH_UNREADABLE", "manifest digest is unreadable"
        ) from exc
    if recorded != digest:
        raise BackupIntegrityError(
            "MANIFEST_DIGEST_MISMATCH",
            "manifest.json does not match manifest.sha256",
            details={"expected": recorded, "actual": digest},
        )
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupIntegrityError("MANIFEST_INVALID", "manifest.json is not valid JSON") from exc
    if not isinstance(raw, dict) or raw.get("schema") != MANIFEST_SCHEMA:
        raise BackupIntegrityError(
            "MANIFEST_SCHEMA_UNSUPPORTED", "manifest schema is not supported"
        )
    try:
        files = tuple(
            BackupFile(
                relative_path=item["relative_path"],
                kind=item["kind"],
                byte_size=int(item["byte_size"]),
                sha256=item["sha256"],
            )
            for item in raw["files"]
        )
        manifest = Manifest(
            schema=raw["schema"],
            backup_id=raw["backup_id"],
            created_at=raw["created_at"],
            source=raw["source"],
            database=raw["database"],
            files=files,
            missing=tuple(raw.get("missing", [])),
            boundaries=raw.get("boundaries", {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupIntegrityError("MANIFEST_INVALID", "manifest fields are incomplete") from exc
    return manifest, digest


def _safe_backup_relative(relative_path: str) -> tuple[str, ...]:
    path = Path(relative_path)
    if path.is_absolute() or path.drive:
        raise BackupIntegrityError("BACKUP_PATH_UNSAFE", "backup file path is absolute")
    parts = path.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise BackupIntegrityError("BACKUP_PATH_UNSAFE", "backup file path is not canonical")
    if parts[0] not in DATA_SUBDIRS:
        raise BackupIntegrityError(
            "BACKUP_PATH_UNSAFE",
            f"backup file is outside the allowlisted data dirs: {relative_path}",
        )
    return parts


def _db_connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.as_uri() + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def _check_database(path: Path, manifest: Manifest) -> None:
    try:
        connection = _db_connect_readonly(path)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            if [row[0] for row in integrity] != ["ok"]:
                raise BackupIntegrityError(
                    "DATABASE_INTEGRITY_FAILED",
                    "restored database failed PRAGMA integrity_check",
                    details={"result": [row[0] for row in integrity][:8]},
                )
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                raise BackupIntegrityError(
                    "DATABASE_FOREIGN_KEY_FAILED",
                    "restored database has foreign key violations",
                    details={"violations": len(foreign_keys)},
                )
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            expected_version = str(manifest.database.get("alembic_version", ""))
            if not version or version[0] != expected_version:
                raise BackupIntegrityError(
                    "DATABASE_VERSION_MISMATCH",
                    "restored database schema version does not match the manifest",
                    details={
                        "expected": expected_version,
                        "actual": version[0] if version else None,
                    },
                )
            asset_count = connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            if int(asset_count) != int(manifest.database.get("asset_count", -1)):
                raise BackupIntegrityError(
                    "DATABASE_ASSET_COUNT_MISMATCH",
                    "restored database asset count does not match the manifest",
                )
            rows = connection.execute(
                "SELECT id, kind, relative_path, sha256 FROM assets ORDER BY id"
            ).fetchall()
        finally:
            connection.close()
    except BackupIntegrityError:
        raise
    except sqlite3.Error as exc:
        raise BackupIntegrityError(
            "DATABASE_UNREADABLE", f"restored database could not be read: {type(exc).__name__}"
        ) from exc
    file_map = {item.relative_path: item for item in manifest.files}
    missing_map = {str(item.get("relative_path")): item for item in manifest.missing}
    seen_db_paths: set[str] = set()
    for _asset_id, _kind, relative_path, sha256 in rows:
        relative = str(relative_path)
        _safe_backup_relative(relative)
        if relative in seen_db_paths:
            continue
        seen_db_paths.add(relative)
        if relative in missing_map:
            if relative in file_map:
                raise BackupIntegrityError(
                    "MANIFEST_MISSING_CONFLICT",
                    f"manifest lists {relative} as both present and missing",
                )
            continue
        item = file_map.get(relative)
        if item is None:
            raise BackupIntegrityError(
                "BACKUP_FILE_MISSING",
                f"database-referenced file is absent from the backup: {relative}",
            )
        expected = (sha256 or "").strip().lower()
        if expected and item.sha256.lower() != expected:
            raise BackupIntegrityError(
                "DATABASE_ASSET_DIGEST_MISMATCH",
                f"backup file digest does not match the database row: {relative}",
            )


def verify_backup(
    backup_dir: str | Path, *, isolated_dir: str | Path | None = None
) -> VerificationReport:
    """Verify a backup from its manifest, hashes and a read-only SQLite open.

    When *isolated_dir* is provided the backup is first copied there and all
    checks run against the copy, proving the result does not depend on the
    staging directory or the source data root.
    """
    source = Path(backup_dir).expanduser().resolve()
    if not source.is_dir():
        raise BackupIntegrityError("BACKUP_NOT_FOUND", f"backup directory does not exist: {source}")
    working = source
    if isolated_dir is not None:
        target = Path(isolated_dir).expanduser().resolve()
        if target == source or target.is_relative_to(source) or source.is_relative_to(target):
            raise BackupIntegrityError(
                "ISOLATION_PATH_UNSAFE", "isolated verification path overlaps the backup"
            )
        if target.exists() and any(target.iterdir()):
            raise BackupIntegrityError(
                "ISOLATION_PATH_NOT_EMPTY", "isolated verification path must be empty"
            )
        target.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)
        working = target
    manifest, digest = load_manifest(working)
    if manifest.missing:
        raise BackupIntegrityError(
            "BACKUP_INCOMPLETE",
            "backup manifest reports database-referenced files that are missing",
            details={"missing_files": len(manifest.missing)},
        )
    data_dir = working / DATA_DIR_NAME
    if not data_dir.is_dir():
        raise BackupIntegrityError("BACKUP_DATA_MISSING", "backup data directory is missing")
    allowed_top = {MANIFEST_NAME, MANIFEST_HASH_NAME, DATA_DIR_NAME}
    for entry in working.iterdir():
        if entry.name not in allowed_top:
            raise BackupIntegrityError(
                "UNEXPECTED_BACKUP_ENTRY", f"backup contains an unexpected entry: {entry.name}"
            )
    database_file = data_dir / "app.db"
    expected_db_sha = str(manifest.database.get("snapshot_sha256", ""))
    if not database_file.is_file() or not is_safe_regular_file(database_file, data_dir):
        raise BackupIntegrityError("BACKUP_DATABASE_MISSING", "backup database snapshot is missing")
    actual_db_sha = hashlib.sha256(database_file.read_bytes()).hexdigest()
    if actual_db_sha != expected_db_sha:
        raise BackupIntegrityError(
            "DATABASE_DIGEST_MISMATCH",
            "backup database digest does not match the manifest",
            details={"expected": expected_db_sha, "actual": actual_db_sha},
        )
    expected_files: set[str] = {"app.db"}
    total_bytes = 0
    for item in manifest.files:
        parts = _safe_backup_relative(item.relative_path)
        if is_excluded_name(parts[-1]):
            raise BackupIntegrityError(
                "SECRET_FILE_PRESENT",
                f"backup manifest contains a secret-like file: {item.relative_path}",
            )
        if item.relative_path in expected_files:
            raise BackupIntegrityError(
                "MANIFEST_DUPLICATE_FILE", "manifest contains a duplicate file entry"
            )
        expected_files.add(item.relative_path)
        path = data_dir / item.relative_path
        if not is_safe_regular_file(path, data_dir):
            raise BackupIntegrityError(
                "BACKUP_FILE_MISSING", f"backup file is missing or unsafe: {item.relative_path}"
            )
        byte_size = path.stat().st_size
        if byte_size != item.byte_size:
            raise BackupIntegrityError(
                "FILE_SIZE_MISMATCH",
                f"backup file size does not match the manifest: {item.relative_path}",
            )
        if hashlib.sha256(path.read_bytes()).hexdigest() != item.sha256:
            raise BackupIntegrityError(
                "FILE_DIGEST_MISMATCH",
                f"backup file digest does not match the manifest: {item.relative_path}",
            )
        total_bytes += byte_size
    for current, _dirnames, filenames in os.walk(data_dir, followlinks=False):
        current_path = Path(current)
        for name in filenames:
            path = current_path / name
            relative = path.relative_to(data_dir).as_posix()
            if relative not in expected_files:
                raise BackupIntegrityError(
                    "UNEXPECTED_BACKUP_FILE", f"backup contains an unexpected file: {relative}"
                )
    _check_database(database_file, manifest)
    return VerificationReport(
        backup_id=manifest.backup_id,
        manifest_sha256=digest,
        file_count=len(manifest.files),
        total_bytes=total_bytes,
        database_bytes=database_file.stat().st_size,
        database_sha256=actual_db_sha,
        alembic_version=str(manifest.database.get("alembic_version", "")),
        asset_count=int(manifest.database.get("asset_count", 0)),
        missing_files=len(manifest.missing),
        checks=(
            "manifest_digest",
            "file_sizes",
            "file_digests",
            "sqlite_integrity",
            "foreign_keys",
            "schema_version",
            "asset_cross_check",
        ),
        isolated=isolated_dir is not None,
    )


__all__ = [
    "BackupFile",
    "DATA_DIR_NAME",
    "MANIFEST_HASH_NAME",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA",
    "Manifest",
    "VerificationReport",
    "canonical_manifest_bytes",
    "load_manifest",
    "manifest_digest",
    "verify_backup",
    "write_manifest",
]

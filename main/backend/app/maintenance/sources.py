from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import DEFAULT_DATA_DIR, MAIN_DIR
from app.maintenance.errors import BackupError

DB_NAME = "app.db"
WAL_NAME = "app.db-wal"
SHM_NAME = "app.db-shm"
#: Only these data-root subdirectories may contribute immutable files.
DATA_SUBDIRS = ("references", "generated", "published")

_REPARSE_POINT_ATTRIBUTE = 0x400
_SECRET_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        "id_rsa",
        "id_ed25519",
        "model_keys.json",
        "api_keys.json",
        "secrets.json",
    }
)
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".kdbx")


def is_excluded_name(name: str) -> bool:
    """Return True for .env, TLS private keys and obvious key material."""
    lowered = name.lower()
    if lowered in _SECRET_NAMES or lowered.startswith(".env."):
        return True
    if lowered.startswith("id_rsa") or lowered.startswith("id_ed25519"):
        return True
    return lowered.endswith(_SECRET_SUFFIXES)


def has_reparse_component(path: Path, base: Path) -> bool:
    """Reject symlinks and Windows reparse points on the way to *path*."""
    current = path
    while current != base and current != current.parent:
        try:
            info = current.lstat()
        except OSError:
            return True
        if stat.S_ISLNK(info.st_mode):
            return True
        if getattr(info, "st_file_attributes", 0) & _REPARSE_POINT_ATTRIBUTE:
            return True
        current = current.parent
    return False


def is_safe_regular_file(path: Path, base: Path) -> bool:
    if has_reparse_component(path, base):
        return False
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode)


def hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            total += len(chunk)
    return total, digest.hexdigest()


@dataclass(frozen=True)
class SourceFile:
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
class DatabaseInfo:
    byte_size: int
    sha256: str
    wal_byte_size: int = 0
    wal_sha256: str | None = None
    shm_present: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "wal_byte_size": self.wal_byte_size,
            "wal_sha256": self.wal_sha256,
            "shm_present": self.shm_present,
        }


@dataclass(frozen=True)
class CandidateInspection:
    root: Path
    exists: bool
    is_data_root: bool
    app_db_present: bool
    database: DatabaseInfo | None
    files: tuple[SourceFile, ...] = ()
    excluded_files: int = 0
    unsafe_files: int = 0
    content_digest: str = ""
    error: str | None = None

    @property
    def non_empty(self) -> bool:
        return self.app_db_present or bool(self.files)

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "exists": self.exists,
            "is_data_root": self.is_data_root,
            "app_db_present": self.app_db_present,
            "non_empty": self.non_empty,
            "database": self.database.as_dict() if self.database else None,
            "file_count": len(self.files),
            "total_bytes": sum(item.byte_size for item in self.files),
            "files": [item.as_dict() for item in self.files],
            "excluded_files": self.excluded_files,
            "unsafe_files": self.unsafe_files,
            "content_digest": self.content_digest,
            "error": self.error,
        }


def _digest_from_entries(entries: Iterable[tuple[str, int, str]]) -> str:
    digest = hashlib.sha256()
    for relative_path, byte_size, sha256 in sorted(entries):
        digest.update(f"{relative_path}\t{byte_size}\t{sha256}\n".encode())
    return digest.hexdigest()


def _scan_allowlisted_files(root: Path) -> tuple[list[SourceFile], int, int]:
    files: list[SourceFile] = []
    excluded = 0
    unsafe = 0
    for subdir in DATA_SUBDIRS:
        base = root / subdir
        if not base.exists():
            continue
        if not base.is_dir() or base.is_symlink():
            unsafe += 1
            continue
        for current, dirnames, filenames in os.walk(base, followlinks=False):
            current_path = Path(current)
            dirnames[:] = [name for name in dirnames if not (current_path / name).is_symlink()]
            for name in filenames:
                path = current_path / name
                if path.is_symlink() or not is_safe_regular_file(path, root):
                    unsafe += 1
                    continue
                if is_excluded_name(name):
                    excluded += 1
                    continue
                relative = path.relative_to(root).as_posix()
                byte_size, sha256 = hash_file(path)
                files.append(
                    SourceFile(
                        relative_path=relative,
                        kind=subdir,
                        byte_size=byte_size,
                        sha256=sha256,
                    )
                )
    files.sort(key=lambda item: item.relative_path)
    return files, excluded, unsafe


def inspect_candidate(root: str | Path) -> CandidateInspection:
    """Read-only candidate report; never opens or writes the SQLite database."""
    path = Path(root).expanduser().resolve()
    if not path.exists():
        return CandidateInspection(
            root=path,
            exists=False,
            is_data_root=False,
            app_db_present=False,
            database=None,
            content_digest=_digest_from_entries([]),
        )
    if not path.is_dir():
        return CandidateInspection(
            root=path,
            exists=True,
            is_data_root=False,
            app_db_present=False,
            database=None,
            content_digest=_digest_from_entries([]),
            error="CANDIDATE_NOT_A_DIRECTORY",
        )

    entries: list[tuple[str, int, str]] = []
    database: DatabaseInfo | None = None
    app_db = path / DB_NAME
    app_db_present = app_db.is_file() and not app_db.is_symlink()
    error: str | None = None
    if app_db.exists() and not is_safe_regular_file(app_db, path):
        error = "SOURCE_DATABASE_UNSAFE"
        app_db_present = False
    elif app_db_present:
        size, digest = hash_file(app_db)
        wal = path / WAL_NAME
        wal_size = 0
        wal_sha: str | None = None
        if wal.is_file() and is_safe_regular_file(wal, path):
            wal_size, wal_sha = hash_file(wal)
        shm_present = (path / SHM_NAME).is_file()
        database = DatabaseInfo(
            byte_size=size,
            sha256=digest,
            wal_byte_size=wal_size,
            wal_sha256=wal_sha,
            shm_present=shm_present,
        )
        entries.append((DB_NAME, size, digest))
        if wal_sha is not None:
            entries.append((WAL_NAME, wal_size, wal_sha))

    files, excluded, unsafe = _scan_allowlisted_files(path)
    try:
        top_level = os.listdir(path)
    except OSError:
        top_level = []
    for name in top_level:
        if is_excluded_name(name):
            excluded += 1
    for item in files:
        entries.append((item.relative_path, item.byte_size, item.sha256))
    is_data_root = app_db_present or any((path / name).is_dir() for name in DATA_SUBDIRS)
    return CandidateInspection(
        root=path,
        exists=True,
        is_data_root=is_data_root,
        app_db_present=app_db_present,
        database=database,
        files=tuple(files),
        excluded_files=excluded,
        unsafe_files=unsafe,
        content_digest=_digest_from_entries(entries),
        error=error,
    )


def discover_candidates(roots: Sequence[str | Path]) -> tuple[CandidateInspection, ...]:
    seen: set[Path] = set()
    result: list[CandidateInspection] = []
    for root in roots:
        path = Path(root).expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        result.append(inspect_candidate(path))
    return tuple(result)


def default_candidate_roots(source: str | Path | None = None) -> tuple[Path, ...]:
    """Known layout candidates plus immediate sibling data roots of *source*."""
    roots: list[Path] = [Path(DEFAULT_DATA_DIR), Path(MAIN_DIR) / "backend" / "data"]
    if source is not None:
        parent = Path(source).expanduser().resolve().parent
        try:
            children = sorted(parent.iterdir())
        except OSError:
            children = []
        for child in children:
            if child.is_dir() and not child.is_symlink() and _looks_like_data_root(child):
                roots.append(child)
    deduped: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return tuple(deduped)


def _looks_like_data_root(path: Path) -> bool:
    return (path / DB_NAME).is_file() or any((path / name).is_dir() for name in DATA_SUBDIRS)


@dataclass(frozen=True)
class SourceDecision:
    selected: CandidateInspection
    candidates: tuple[CandidateInspection, ...]
    requires_confirmation: bool
    confirmed: bool


def select_source(
    source: str | Path,
    *,
    candidate_roots: Sequence[str | Path] | None = None,
    confirm_source_digest: str | None = None,
) -> SourceDecision:
    """Select exactly one source; refuse to merge or silently choose one."""
    source_path = Path(source).expanduser().resolve()
    roots = (
        tuple(candidate_roots)
        if candidate_roots is not None
        else default_candidate_roots(source_path)
    )
    candidates = list(discover_candidates(roots))
    selected = inspect_candidate(source_path)
    if not selected.exists or not selected.app_db_present:
        raise BackupError(
            "SOURCE_NOT_DATA_ROOT",
            f"selected source does not contain a readable {DB_NAME}",
            details={"source": str(source_path)},
        )
    if selected.error:
        raise BackupError(selected.error, "selected source database path is unsafe")
    if not any(item.root == selected.root for item in candidates):
        candidates.append(selected)
    non_empty = [item for item in candidates if item.non_empty]
    other_sources = [item for item in non_empty if item.root != selected.root]
    requires_confirmation = bool(other_sources)
    if requires_confirmation and not confirm_source_digest:
        raise BackupError(
            "SOURCE_CHOICE_REQUIRED",
            "multiple non-empty data sources were found; choose one explicitly and "
            "pass its content digest",
            details={
                "candidates": [item.as_dict() for item in non_empty],
                "selected": str(selected.root),
            },
        )
    if confirm_source_digest is not None and confirm_source_digest != selected.content_digest:
        raise BackupError(
            "SOURCE_DIGEST_MISMATCH",
            "confirmed source digest does not match the selected source",
            details={
                "expected": selected.content_digest,
                "received": confirm_source_digest,
            },
        )
    return SourceDecision(
        selected=selected,
        candidates=tuple(non_empty),
        requires_confirmation=requires_confirmation,
        confirmed=confirm_source_digest is not None,
    )


__all__ = [
    "DB_NAME",
    "DATA_SUBDIRS",
    "CandidateInspection",
    "DatabaseInfo",
    "SHM_NAME",
    "SourceDecision",
    "SourceFile",
    "WAL_NAME",
    "default_candidate_roots",
    "discover_candidates",
    "hash_file",
    "inspect_candidate",
    "is_excluded_name",
    "is_safe_regular_file",
    "select_source",
]

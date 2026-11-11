from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.audit import audit_subject, write_audit
from app.auth.clock import Clock
from app.auth.models import Account, InstanceBootstrap
from app.auth.service import InvalidEmail, normalize_email
from app.config import BACKEND_DIR
from app.db import build_session_factory, read_session, write_transaction
from app.maintenance.backup import verify_backup
from app.maintenance.errors import BackupError
from app.maintenance.manifest import load_manifest
from app.maintenance.sources import SourceDecision, select_source
from app.models import Base, Project
from app.timeutil import format_timestamp
from app.workflow.tables import LegacyItem

CLAIM_CONFIRMATION = "CLAIM_LEGACY"
CLAIM_EVENT_TYPE = "legacy_projects_claimed"

# Tables whose rows belong to a project and must not change while ownership is
# being established. The projects row itself is handled separately because its
# owner column is the one field the claim intentionally changes.
_SNAPSHOT_TABLES: tuple[str, ...] = (
    "messages",
    "assets",
    "generations",
    "workflow_runs",
    "role_calls",
    "artifacts",
    "artifact_reviews",
    "approvals",
    "generation_jobs",
    "deliveries",
    "legacy_items",
    "diagnostic_events",
)


class LegacyClaim(Base):
    __tablename__ = "legacy_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    admin_account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
    )
    source_root: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    backup_id: Mapped[str] = mapped_column(String(64), nullable=False)
    backup_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    project_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    project_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_before_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_after_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="applied")

    __table_args__ = (
        CheckConstraint("project_count > 0", name="ck_legacy_claims_project_count"),
        CheckConstraint("status IN ('applied')", name="ck_legacy_claims_status"),
        UniqueConstraint("idempotency_key", name="uq_legacy_claims_idempotency_key"),
        Index("ix_legacy_claims_admin_created", "admin_account_id", "created_at"),
    )


@dataclass(frozen=True)
class ClaimResult:
    claim_id: str
    idempotent: bool
    source: Path
    source_digest: str
    backup_id: str
    backup_manifest_sha256: str
    admin_account_id: str
    project_ids: tuple[str, ...]
    snapshot_digest: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "claim_id": self.claim_id,
            "idempotent": self.idempotent,
            "source": str(self.source),
            "source_digest": self.source_digest,
            "backup_id": self.backup_id,
            "backup_manifest_sha256": self.backup_manifest_sha256,
            "admin_account_id": self.admin_account_id,
            "project_ids": list(self.project_ids),
            "snapshot_digest": self.snapshot_digest,
            "created_at": self.created_at,
        }


def _error(code: str, message: str, **details: Any) -> BackupError:
    return BackupError(code, message, details=details or None)


def _expected_head() -> str | None:
    ini = BACKEND_DIR / "alembic.ini"
    scripts = BACKEND_DIR / "migrations"
    if not ini.is_file() or not scripts.is_dir():
        return None
    config = Config(str(ini))
    config.set_main_option("script_location", str(scripts))
    return ScriptDirectory.from_config(config).get_current_head()


def _engine_for(db_path: Path):
    engine = create_async_engine(
        "sqlite+aiosqlite:///" + db_path.as_posix(),
        future=True,
        connect_args={"timeout": 5.0},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()

    return engine


async def _schema_version(session: AsyncSession) -> str | None:
    result = await session.execute(text("SELECT version_num FROM alembic_version"))
    value = result.scalar_one_or_none()
    return str(value) if value is not None else None


async def _normalized_project_ids(project_ids: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in project_ids:
        value = str(raw).strip()
        if value:
            normalized.append(value)
    unique = tuple(dict.fromkeys(normalized))
    if not unique:
        raise _error("PROJECTS_REQUIRED", "select at least one legacy project")
    return unique


def _idempotency_key(
    *, backup_manifest_sha256: str, admin_account_id: str, project_ids: Sequence[str]
) -> str:
    payload = {
        "kind": "legacy-claim-v1",
        "backup_manifest_sha256": backup_manifest_sha256,
        "admin_account_id": admin_account_id,
        "project_ids": list(project_ids),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


async def _load_admin(session: AsyncSession, email: str) -> Account:
    try:
        normalized = normalize_email(email)
    except InvalidEmail as exc:
        raise _error("ADMIN_EMAIL_INVALID", "administrator email is not valid") from exc
    admin = await session.scalar(select(Account).where(Account.email_normalized == normalized))
    if admin is None:
        raise _error("ADMIN_NOT_FOUND", "the selected administrator does not exist")
    if not admin.is_admin:
        raise _error("ADMIN_NOT_ELIGIBLE", "the selected account is not an administrator")
    if not admin.can_login:
        raise _error(
            "ADMIN_NOT_ELIGIBLE",
            "the selected administrator must be active and able to log in",
        )
    bootstrap = await session.get(InstanceBootstrap, 1)
    if bootstrap is None or bootstrap.admin_account_id != admin.id:
        raise _error(
            "ADMIN_NOT_BOOTSTRAP",
            "only the initial administrator may claim legacy projects",
        )
    return admin


async def _load_projects(
    session: AsyncSession, project_ids: Sequence[str]
) -> dict[str, Project]:
    rows = list(
        await session.scalars(select(Project).where(Project.id.in_(tuple(project_ids))))
    )
    return {project.id: project for project in rows}


async def _require_claimable_projects(
    session: AsyncSession, project_ids: Sequence[str]
) -> dict[str, Project]:
    projects = await _load_projects(session, project_ids)
    if len(projects) != len(project_ids):
        missing = [project_id for project_id in project_ids if project_id not in projects]
        raise _error(
            "PROJECT_NOT_FOUND",
            "one or more selected projects do not exist",
            project_ids=missing,
        )
    legacy_counts = dict(
        (
            await session.execute(
                select(LegacyItem.project_id, func.count(LegacyItem.id))
                .where(LegacyItem.project_id.in_(tuple(project_ids)))
                .group_by(LegacyItem.project_id)
            )
        ).all()
    )
    for project_id in project_ids:
        project = projects[project_id]
        if project.owner_account_id is not None:
            raise _error(
                "PROJECT_ALREADY_CLAIMED",
                "one or more selected projects already have an owner",
                project_ids=[project_id],
            )
        if not legacy_counts.get(project_id):
            raise _error(
                "PROJECT_NOT_LEGACY",
                "one or more selected projects have no legacy evidence",
                project_ids=[project_id],
            )
    return projects


async def _snapshot_digest(session: AsyncSession, project_ids: Sequence[str]) -> str:
    digest = hashlib.sha256()
    ids = tuple(project_ids)
    for table in ("projects", *_SNAPSHOT_TABLES):
        exists = await session.scalar(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table},
        )
        if exists is None:
            continue
        info = (await session.execute(text(f'PRAGMA table_info("{table}")'))).fetchall()
        columns = [str(row[1]) for row in info]
        if table == "projects":
            columns = [column for column in columns if column != "owner_account_id"]
            where = "id IN ({})".format(", ".join(f":p{i}" for i in range(len(ids))))
        else:
            if "project_id" not in columns:
                continue
            where = "project_id IN ({})".format(", ".join(f":p{i}" for i in range(len(ids))))
        params = {f"p{i}": project_id for i, project_id in enumerate(ids)}
        quoted = ", ".join(f'"{column}"' for column in columns)
        statement = text(f'SELECT {quoted} FROM "{table}" WHERE {where} ORDER BY rowid')
        rows = (await session.execute(statement, params)).fetchall()
        payload = {
            "table": table,
            "columns": columns,
            "rows": [list(row) for row in rows],
        }
        digest.update(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _verify_backup_for_source(
    *, backup: Path, decision: SourceDecision
) -> tuple[Any, str, Any]:
    if not backup.is_dir():
        raise _error("BACKUP_NOT_FOUND", "backup directory does not exist")
    source_root = decision.selected.root.resolve()
    backup_root = backup.resolve()
    if backup_root == source_root or backup_root.is_relative_to(source_root):
        raise _error(
            "BACKUP_OVERLAPS_SOURCE",
            "backup must live outside the source data root",
        )
    try:
        manifest, manifest_digest = load_manifest(backup_root)
        report = verify_backup(backup_root)
    except BackupError:
        raise
    except Exception as exc:
        raise _error(
            "BACKUP_INVALID",
            "backup verification failed",
            error=type(exc).__name__,
        ) from exc
    if report.missing_files:
        raise _error(
            "BACKUP_INCOMPLETE",
            "backup is missing referenced files and cannot be used for a claim",
            missing_files=report.missing_files,
        )
    manifest_root_raw = manifest.source.get("root")
    manifest_digest_value = manifest.source.get("content_digest")
    if not isinstance(manifest_root_raw, str) or not isinstance(manifest_digest_value, str):
        raise _error("BACKUP_INVALID", "backup manifest does not describe its source")
    if Path(manifest_root_raw).resolve() != source_root:
        raise _error(
            "BACKUP_SOURCE_MISMATCH",
            "backup was created from a different source data root",
            backup_source=manifest_root_raw,
            selected_source=str(source_root),
        )
    return manifest, manifest_digest, report


async def inspect_legacy_projects(
    *, source: str | Path, candidate_roots: Sequence[str | Path] | None = None,
    confirm_source_digest: str | None = None,
) -> dict[str, Any]:
    decision = await asyncio.to_thread(
        select_source,
        source,
        candidate_roots=candidate_roots,
        confirm_source_digest=confirm_source_digest,
    )
    selected = decision.selected
    assert selected.database is not None
    engine = _engine_for(selected.root / "app.db")
    factory = build_session_factory(engine)
    try:
        async with read_session(factory) as session:
            rows = (
                await session.execute(
                    select(Project, func.count(LegacyItem.id))
                    .outerjoin(LegacyItem, LegacyItem.project_id == Project.id)
                    .where(Project.owner_account_id.is_(None))
                    .group_by(Project.id)
                    .having(func.count(LegacyItem.id) > 0)
                    .order_by(Project.created_at, Project.id)
                )
            ).all()
            projects = [
                {
                    "id": project.id,
                    "title": project.title,
                    "legacy_items": int(count),
                    "created_at": project.created_at,
                }
                for project, count in rows
            ]
    finally:
        await engine.dispose()
    return {
        "ok": True,
        "source": str(selected.root),
        "source_digest": selected.content_digest,
        "projects": projects,
    }


async def claim_legacy(
    *,
    source: str | Path,
    backup: str | Path,
    admin_email: str,
    project_ids: Sequence[str],
    confirmation: str,
    service_stopped: bool,
    candidate_roots: Sequence[str | Path] | None = None,
    confirm_source_digest: str | None = None,
) -> ClaimResult:
    if confirmation != CLAIM_CONFIRMATION:
        raise _error(
            "CLAIM_CONFIRMATION_REQUIRED",
            "claim requires explicit confirmation",
        )
    if not service_stopped:
        raise _error(
            "SERVICE_MUST_BE_STOPPED",
            "claim is offline-only; stop the service and acknowledge it",
        )
    ids = await _normalized_project_ids(project_ids)
    decision = await asyncio.to_thread(
        select_source,
        source,
        candidate_roots=candidate_roots,
        confirm_source_digest=confirm_source_digest,
    )
    selected = decision.selected
    if selected.content_digest == "":
        raise _error("SOURCE_DIGEST_MISSING", "selected source has no content digest")
    backup_path = await asyncio.to_thread(lambda: Path(backup).expanduser().resolve())
    manifest, manifest_digest, report = await asyncio.to_thread(
        _verify_backup_for_source, backup=backup_path, decision=decision
    )
    backup_version = str(manifest.database.get("alembic_version", ""))
    if backup_version != report.alembic_version:
        raise _error(
            "BACKUP_SCHEMA_MISMATCH",
            "backup manifest and verification disagree about the schema version",
        )

    db_path = selected.root / "app.db"
    engine = _engine_for(db_path)
    factory = build_session_factory(engine)
    clock = Clock()
    try:
        async with read_session(factory) as session:
            current_version = await _schema_version(session)
            expected = _expected_head()
            if expected is not None and current_version != expected:
                raise _error(
                    "SCHEMA_NOT_CURRENT",
                    "the selected source must be upgraded to the current schema first",
                    expected=expected,
                    actual=current_version,
                )
            if backup_version != current_version:
                raise _error(
                    "BACKUP_SCHEMA_MISMATCH",
                    "backup schema version does not match the selected source",
                    backup=backup_version,
                    source=current_version,
                )
            admin = await _load_admin(session, admin_email)
            key = _idempotency_key(
                backup_manifest_sha256=manifest_digest,
                admin_account_id=admin.id,
                project_ids=ids,
            )
            existing = await session.scalar(
                select(LegacyClaim).where(LegacyClaim.idempotency_key == key)
            )
            if existing is not None:
                if existing.source_root != str(selected.root):
                    raise _error(
                        "CLAIM_SOURCE_MISMATCH",
                        "the existing claim belongs to a different source root",
                    )
                if existing.backup_manifest_sha256 != manifest_digest:
                    raise _error(
                        "CLAIM_BACKUP_MISMATCH",
                        "the existing claim belongs to a different backup",
                    )
                projects = await _load_projects(session, ids)
                if len(projects) != len(ids) or any(
                    project.owner_account_id != admin.id for project in projects.values()
                ):
                    raise _error(
                        "CLAIM_STATE_CHANGED",
                        "projects no longer match the recorded claim",
                    )
                current_snapshot = await _snapshot_digest(session, ids)
                if current_snapshot != existing.snapshot_after_digest:
                    raise _error(
                        "CLAIM_STATE_CHANGED",
                        "project content changed after the original claim",
                    )
                return ClaimResult(
                    claim_id=existing.id,
                    idempotent=True,
                    source=selected.root,
                    source_digest=existing.source_digest,
                    backup_id=existing.backup_id,
                    backup_manifest_sha256=existing.backup_manifest_sha256,
                    admin_account_id=existing.admin_account_id,
                    project_ids=tuple(json.loads(existing.project_ids_json)),
                    snapshot_digest=existing.snapshot_after_digest,
                    created_at=existing.created_at,
                )
            if selected.content_digest != manifest.source.get("content_digest"):
                raise _error(
                    "SOURCE_CHANGED_SINCE_BACKUP",
                    "source content no longer matches the verified backup",
                    source_digest=selected.content_digest,
                    backup_source_digest=manifest.source.get("content_digest"),
                )
            projects = await _require_claimable_projects(session, ids)
            snapshot_before = await _snapshot_digest(session, ids)
            claim_id = str(uuid.uuid4())
            created_at = format_timestamp(clock.now())
            record = LegacyClaim(
                id=claim_id,
                idempotency_key=key,
                admin_account_id=admin.id,
                source_root=str(selected.root),
                source_digest=selected.content_digest,
                backup_id=str(manifest.backup_id),
                backup_manifest_sha256=manifest_digest,
                project_ids_json=json.dumps(list(ids), ensure_ascii=False, separators=(",", ":")),
                project_count=len(ids),
                snapshot_before_digest=snapshot_before,
                snapshot_after_digest=snapshot_before,
                created_at=created_at,
                status="applied",
            )
            before_owners = {project_id: None for project_id in ids}
            try:
                async with write_transaction(factory) as write_session:
                    admin_row = await _load_admin(write_session, admin_email)
                    current_projects = await _load_projects(write_session, ids)
                    if len(current_projects) != len(ids):
                        raise _error(
                            "PROJECT_NOT_FOUND",
                            "one or more selected projects disappeared during claim",
                        )
                    for project_id in ids:
                        project = current_projects[project_id]
                        if project.owner_account_id is not None:
                            raise _error(
                                "PROJECT_ALREADY_CLAIMED",
                                "one or more selected projects already have an owner",
                                project_ids=[project_id],
                            )
                    live_before = await _snapshot_digest(write_session, ids)
                    if live_before != snapshot_before:
                        raise _error(
                            "CLAIM_SNAPSHOT_CHANGED",
                            "project content changed before the claim transaction",
                        )
                    write_session.add(record)
                    for project in current_projects.values():
                        project.owner_account_id = admin_row.id
                    await write_session.flush()
                    live_after = await _snapshot_digest(write_session, ids)
                    if live_after != snapshot_before:
                        raise _error(
                            "CLAIM_SNAPSHOT_CHANGED",
                            "claim unexpectedly changed project content",
                        )
                    write_audit(
                        write_session,
                        event_type=CLAIM_EVENT_TYPE,
                        result="success",
                        now=clock.now(),
                        actor_kind="admin",
                        actor_account_id=admin_row.id,
                        target_kind="legacy_claim",
                        target_id=claim_id,
                        subject_ref=audit_subject(admin_row.email_normalized),
                        source_ref="local_cli",
                        before=before_owners,
                        after={project_id: admin_row.id for project_id in ids},
                        detail={
                            "source": str(selected.root),
                            "source_digest": selected.content_digest,
                            "backup_id": str(manifest.backup_id),
                            "backup_manifest_sha256": manifest_digest,
                            "project_count": len(ids),
                            "snapshot_digest": snapshot_before,
                        },
                    )
            except BackupError:
                raise
            except Exception as exc:
                raise _error(
                    "CLAIM_FAILED",
                    "legacy claim transaction failed and was rolled back",
                    error=type(exc).__name__,
                ) from exc
            return ClaimResult(
                claim_id=claim_id,
                idempotent=False,
                source=selected.root,
                source_digest=selected.content_digest,
                backup_id=str(manifest.backup_id),
                backup_manifest_sha256=manifest_digest,
                admin_account_id=admin_row.id,
                project_ids=ids,
                snapshot_digest=snapshot_before,
                created_at=created_at,
            )
    finally:
        await engine.dispose()


__all__ = [
    "CLAIM_CONFIRMATION",
    "CLAIM_EVENT_TYPE",
    "ClaimResult",
    "LegacyClaim",
    "claim_legacy",
    "inspect_legacy_projects",
]

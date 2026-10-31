from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.auth.clock import Clock
from app.auth.models import Account
from app.auth.passwords import WEAK_PASSWORD_LIST_VERSION, hash_password
from app.auth.service import normalize_email
from app.db import read_session, write_transaction
from app.maintenance.backup import create_backup
from app.maintenance.claim import (
    CLAIM_CONFIRMATION,
    claim_legacy,
    inspect_legacy_projects,
)
from app.maintenance.errors import BackupError
from app.maintenance.sources import inspect_candidate
from app.models import Project
from app.timeutil import format_timestamp
from app.workflow.tables import Approval, GenerationJob
from tests.support.adapters import build_test_registry
from tests.support.auth import (
    TEST_ADMIN_EMAIL,
    AuthClient,
    bootstrap_admin,
    ensure_test_admin,
    login,
)
from tests.test_v2_t01_legacy import _seed_v1, _upgrade

PASSWORD = "correct horse battery staple 63"


async def _seed_source(settings: Any) -> dict[str, Any]:
    _upgrade(settings, "0001")
    first = _seed_v1(settings)
    second = _seed_v1(settings)
    _upgrade(settings, "head")
    await bootstrap_admin(settings)
    return {"first": first, "second": second}


async def _backup(settings: Any, output: Path):
    return await create_backup(
        source=settings.data_dir,
        output=output,
        candidate_roots=[settings.data_dir],
    )


async def _add_account(app: Any, email: str) -> str:
    account_id = str(uuid.uuid4())
    now = format_timestamp(Clock().now())
    async with write_transaction(app.state.session_factory) as session:
        session.add(
            Account(
                id=account_id,
                email_normalized=normalize_email(email),
                password_hash=hash_password(PASSWORD),
                password_set_at=now,
                password_policy_version=WEAK_PASSWORD_LIST_VERSION,
                is_admin=0,
                is_disabled=0,
                session_epoch=0,
                created_at=now,
                updated_at=now,
            )
        )
    return account_id


def _row(settings: Any, query: str, *params: Any) -> Any:
    connection = sqlite3.connect(settings.database_path)
    try:
        return connection.execute(query, params).fetchone()
    finally:
        connection.close()


def _claim_events(settings: Any) -> int:
    row = _row(
        settings,
        "SELECT COUNT(*) FROM audit_events WHERE event_type = ?",
        "legacy_projects_claimed",
    )
    return int(row[0]) if row is not None else 0


def _count(settings: Any, table: str) -> int:
    row = _row(settings, f"SELECT COUNT(*) FROM {table}")
    return int(row[0]) if row is not None else 0


def _owner(settings: Any, project_id: str) -> Any:
    return _row(settings, "SELECT owner_account_id FROM projects WHERE id = ?", project_id)[0]


async def _claim(settings: Any, backup: Path, ids: list[str], **overrides: Any):
    values = {
        "source": settings.data_dir,
        "backup": backup,
        "admin_email": TEST_ADMIN_EMAIL,
        "project_ids": ids,
        "confirmation": CLAIM_CONFIRMATION,
        "service_stopped": True,
        "candidate_roots": [settings.data_dir],
    }
    values.update(overrides)
    return await claim_legacy(**values)


@pytest.mark.asyncio
async def test_claim_requires_verified_backup_and_explicit_confirmation(settings, tmp_path):
    seeded = await _seed_source(settings)
    backup = await _backup(settings, tmp_path / "backups")
    project_id = seeded["first"]["project_id"]

    with pytest.raises(BackupError) as wrong_confirmation:
        await _claim(
            settings,
            backup.backup_dir,
            [project_id],
            confirmation="WRONG",
        )
    assert wrong_confirmation.value.code == "CLAIM_CONFIRMATION_REQUIRED"

    with pytest.raises(BackupError) as not_stopped:
        await _claim(settings, backup.backup_dir, [project_id], service_stopped=False)
    assert not_stopped.value.code == "SERVICE_MUST_BE_STOPPED"

    with pytest.raises(BackupError) as missing_backup:
        await _claim(settings, tmp_path / "missing", [project_id])
    assert missing_backup.value.code == "BACKUP_NOT_FOUND"

    assert _owner(settings, project_id) is None
    assert _count(settings, "legacy_claims") == 0


@pytest.mark.asyncio
async def test_source_conflict_requires_digest_and_claim_is_idempotent(settings, tmp_path):
    seeded = await _seed_source(settings)
    backup = await _backup(settings, tmp_path / "backups")
    project_id = seeded["first"]["project_id"]
    other = tmp_path / "other-source"
    other.mkdir()
    shutil.copy2(settings.database_path, other / "app.db")

    with pytest.raises(BackupError) as conflict:
        await _claim(
            settings,
            backup.backup_dir,
            [project_id],
            candidate_roots=[settings.data_dir, other],
        )
    assert conflict.value.code == "SOURCE_CHOICE_REQUIRED"

    digest = inspect_candidate(settings.data_dir).content_digest
    first = await _claim(
        settings,
        backup.backup_dir,
        [project_id],
        candidate_roots=[settings.data_dir, other],
        confirm_source_digest=digest,
    )
    assert first.idempotent is False
    assert first.project_ids == (project_id,)
    assert _owner(settings, project_id) is not None
    second_digest = inspect_candidate(settings.data_dir).content_digest
    second = await _claim(
        settings,
        backup.backup_dir,
        [project_id],
        candidate_roots=[settings.data_dir, other],
        confirm_source_digest=second_digest,
    )
    assert second.idempotent is True
    assert second.claim_id == first.claim_id
    assert _count(settings, "legacy_claims") == 1
    assert _claim_events(settings) == 1
    assert _count(settings, "approvals") == 0
    assert _count(settings, "generation_jobs") == 0
    status = _row(
        settings, "SELECT status FROM legacy_items WHERE project_id = ? LIMIT 1", project_id
    )[0]
    assert status == "legacy_unreviewed"


@pytest.mark.asyncio
async def test_claimed_workspace_isolated_and_generated_bytes_stay_unapproved(settings, tmp_path):
    seeded = await _seed_source(settings)
    backup = await _backup(settings, tmp_path / "backups")
    claimed_id = seeded["first"]["project_id"]
    unclaimed_id = seeded["second"]["project_id"]
    await _claim(settings, backup.backup_dir, [claimed_id])

    registry = build_test_registry()
    application = __import__("app.main", fromlist=["create_app"]).create_app(
        settings=settings, registry=registry
    )
    async with application.router.lifespan_context(application):
        await ensure_test_admin(application)
        transport = ASGITransport(app=application)
        async with AuthClient(transport=transport, base_url="https://testserver") as admin_client:
            await login(admin_client)
            listed = await admin_client.get("/api/projects")
            assert listed.status_code == 200
            assert {item["id"] for item in listed.json()["items"]} == {claimed_id}
            assert (await admin_client.get(f"/api/projects/{claimed_id}")).status_code == 200
            assert (await admin_client.get(f"/api/projects/{unclaimed_id}")).status_code == 404
            reference = await admin_client.get(f"/api/assets/{seeded['first']['reference_id']}")
            assert reference.status_code == 200
            assert reference.content == seeded["first"]["reference_bytes"]
            generated = await admin_client.get(f"/api/assets/{seeded['first']['generated_id']}")
            assert generated.status_code == 409
            assert seeded["first"]["generated_bytes"] not in generated.content
            events = await admin_client.get("/api/admin/security-events")
            assert events.status_code == 200
            claim_events = [
                item
                for item in events.json()["items"]
                if item["event_type"] == "legacy_projects_claimed"
            ]
            assert len(claim_events) == 1
            assert claim_events[0]["detail"] == {"project_count": 1}

            new_account_id = await _add_account(application, "new63@example.com")
            assert new_account_id
            async with AuthClient(transport=transport, base_url="https://testserver") as new_client:
                await login(new_client, "new63@example.com", PASSWORD)
                empty = await new_client.get("/api/projects")
                assert empty.status_code == 200
                assert empty.json()["items"] == []

        async with read_session(application.state.session_factory) as session:
            projects = {
                project.id: project.owner_account_id
                for project in await session.scalars(select(Project))
            }
            assert projects[claimed_id] is not None
            assert projects[unclaimed_id] is None
            assert await session.scalar(select(Approval)) is None
            assert await session.scalar(select(GenerationJob)) is None


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_claim(settings, tmp_path, monkeypatch):
    seeded = await _seed_source(settings)
    backup = await _backup(settings, tmp_path / "backups")
    project_id = seeded["first"]["project_id"]

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("app.maintenance.claim.write_audit", _boom)
    with pytest.raises(BackupError) as failure:
        await _claim(settings, backup.backup_dir, [project_id])
    assert failure.value.code == "CLAIM_FAILED"
    assert _owner(settings, project_id) is None
    assert _count(settings, "legacy_claims") == 0
    assert _claim_events(settings) == 0


@pytest.mark.asyncio
async def test_source_change_after_backup_is_refused(settings, tmp_path):
    seeded = await _seed_source(settings)
    backup = await _backup(settings, tmp_path / "backups")
    project_id = seeded["first"]["project_id"]
    reference_path = (
        settings.resolved_data_dir() / "references" / f"{seeded['first']['reference_id']}.png"
    )
    reference_path.write_bytes(b"changed after backup")

    with pytest.raises(BackupError) as changed:
        await _claim(settings, backup.backup_dir, [project_id])
    assert changed.value.code == "SOURCE_CHANGED_SINCE_BACKUP"
    assert _owner(settings, project_id) is None
    assert _count(settings, "legacy_claims") == 0


@pytest.mark.asyncio
async def test_inspect_lists_only_unclaimed_legacy_projects(settings, tmp_path):
    seeded = await _seed_source(settings)
    report = await inspect_legacy_projects(
        source=settings.data_dir, candidate_roots=[settings.data_dir]
    )
    ids = {item["id"] for item in report["projects"]}
    assert seeded["first"]["project_id"] in ids
    assert seeded["second"]["project_id"] in ids
    assert report["source_digest"] == inspect_candidate(settings.data_dir).content_digest

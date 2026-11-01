from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from app.maintenance.automation import (
    STATUS_SCHEMA,
    next_backup_due,
    parse_timestamp,
    read_status,
    run_backup_cycle,
)
from app.maintenance.backup import (
    load_manifest,
    read_latest,
    verify_backup,
    write_manifest,
)
from app.maintenance.coordinator import EXPIRY_DELETION, MaintenanceCoordinator
from app.maintenance.errors import BackupError, BackupIntegrityError
from tests.conftest import _test_settings, migrate
from tests.test_t54_maintenance_backup import _asset_row, _backup_names, _create_source


def _source_with_one_file(root: Path) -> None:
    data = b"reference-payload"
    _create_source(
        root,
        rows=[_asset_row("asset-1", "references/ref.bin", data)],
        files={"references/ref.bin": data},
    )
    (root / ".env").write_text("LLM_API_KEY=must-not-be-copied", encoding="utf-8")
    (root / "certs").mkdir()
    (root / "certs" / "server.key").write_text("private-key", encoding="utf-8")


def test_backup_dir_must_be_outside_data_and_web_dist(tmp_path):
    with pytest.raises(ValidationError, match="APP_BACKUP_DIR"):
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            web_dist_dir=tmp_path / "web",
            backup_dir=tmp_path / "data" / "backups",
            app_public_url="https://testserver",
            allowed_hosts=["testserver"],
        )


def test_backup_interval_cannot_exceed_twenty_four_hours(tmp_path):
    with pytest.raises(ValidationError, match="backup_interval_seconds"):
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            web_dist_dir=tmp_path / "web",
            backup_dir=tmp_path / "backups",
            backup_interval_seconds=86401,
            app_public_url="https://testserver",
            allowed_hosts=["testserver"],
        )


def test_next_due_uses_status_then_last_success_then_now(tmp_path):
    now = datetime(2026, 1, 8, 12, 0, tzinfo=UTC)
    assert next_backup_due(None, now=now, interval_seconds=86400) == now
    assert next_backup_due(
        {"next_due_at": "2026-01-08T13:00:00.000Z"}, now=now, interval_seconds=86400
    ) == datetime(2026, 1, 8, 13, 0, tzinfo=UTC)
    assert next_backup_due(
        {"last_success_at": "2026-01-01T00:00:00.000Z"}, now=now, interval_seconds=86400
    ) == datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
    assert next_backup_due(
        {
            "last_success_at": "2026-01-08T00:00:00.000Z",
            "next_due_at": "2026-01-08T13:00:00.000Z",
        },
        now=now,
        interval_seconds=3600,
    ) == datetime(2026, 1, 8, 1, 0, tzinfo=UTC)
    assert parse_timestamp("not-a-date") is None


@pytest.mark.asyncio
async def test_cycle_writes_status_latest_and_table_counts_without_secrets(tmp_path, capsys):
    source = tmp_path / "data"
    output = tmp_path / "backups"
    _source_with_one_file(source)
    output.mkdir()
    (output / ".status.json.999.tmp").write_text("leftover", encoding="utf-8")
    result = await run_backup_cycle(
        source=source,
        output=output,
        keep=7,
        timeout=5,
        interval_seconds=3600,
        now=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )
    status = read_status(output)
    assert status is not None
    assert status["schema"] == STATUS_SCHEMA
    assert status["backup_id"] == result.backup.backup_id
    assert status["last_error"] is None
    assert status["keep"] == 7
    assert parse_timestamp(status["next_due_at"]) - parse_timestamp(
        status["last_success_at"]
    ) == timedelta(seconds=3600)
    assert read_latest(output)["backup_id"] == result.backup.backup_id

    manifest, _digest = load_manifest(result.backup.backup_dir)
    assert manifest.boundaries["periodic_scheduler"] is True
    assert manifest.boundaries["excluded"] == [".env", "TLS private keys", "model key material"]
    assert manifest.database["table_counts"] == {
        "alembic_version": 1,
        "assets": 1,
        "payload": 0,
    }
    assert not (result.backup.backup_dir / "data" / ".env").exists()
    assert not (result.backup.backup_dir / "data" / "certs" / "server.key").exists()

    from app.maintenance.cli import main as cli_main

    assert cli_main(["backup-status", "--output", str(output), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"]["backup_id"] == result.backup.backup_id


@pytest.mark.asyncio
async def test_rotation_keeps_seven_verified_and_leaves_invalid_candidate(tmp_path, monkeypatch):
    source = tmp_path / "data"
    output = tmp_path / "backups"
    _source_with_one_file(source)
    start = datetime(2026, 2, 1, 0, 0, tzinfo=UTC)
    counter = 0

    import app.maintenance.backup as backup_module

    def fake_now():
        nonlocal counter
        value = start + timedelta(seconds=counter)
        counter += 1
        return value

    monkeypatch.setattr(backup_module, "utc_now", fake_now)
    for _ in range(9):
        await run_backup_cycle(source=source, output=output, keep=100, interval_seconds=86400)
    invalid = output / "backup-invalid"
    invalid.mkdir()
    (invalid / "junk.txt").write_text("not a backup", encoding="utf-8")
    result = await run_backup_cycle(source=source, output=output, keep=7, interval_seconds=86400)
    verified = [name for name in _backup_names(output) if name != "backup-invalid"]
    assert len(verified) == 7
    assert len(result.pruned) == 3
    assert result.backup.backup_dir.name in verified
    assert invalid.exists()
    assert any(item["backup"] == "backup-invalid" for item in result.rotation_skipped)
    status = read_status(output)
    assert status["backup_id"] == result.backup.backup_id
    assert status["pruned"] == list(result.pruned)


@pytest.mark.asyncio
async def test_failed_cycle_preserves_last_usable_backup_and_records_failure(tmp_path):
    source = tmp_path / "data"
    output = tmp_path / "backups"
    _source_with_one_file(source)
    first = await run_backup_cycle(source=source, output=output, keep=7, interval_seconds=3600)
    latest_before = read_latest(output)
    names_before = _backup_names(output)
    (source / "app.db").write_bytes(b"not a sqlite database")
    with pytest.raises(BackupError) as excinfo:
        await run_backup_cycle(source=source, output=output, keep=7, interval_seconds=3600)
    assert excinfo.value.code in {"SOURCE_DATABASE_UNREADABLE", "SOURCE_DATABASE_INTEGRITY_FAILED"}
    assert read_latest(output) == latest_before
    assert _backup_names(output) == names_before
    assert not [path for path in output.iterdir() if path.name.startswith(".staging-")]
    status = read_status(output)
    assert status["backup_id"] == first.backup.backup_id
    assert status["last_error"]["code"] == excinfo.value.code
    assert status["last_failure_at"] is not None
    assert status["last_success_at"] == first.status["last_success_at"]


@pytest.mark.asyncio
async def test_cycle_waits_for_file_publication_lease(tmp_path):
    source = tmp_path / "data"
    output = tmp_path / "backups"
    _source_with_one_file(source)
    coordinator = MaintenanceCoordinator()
    started = threading.Event()
    release = threading.Event()

    def publisher():
        with coordinator.file_publication():
            started.set()
            release.wait(5)

    publisher_task = asyncio.create_task(asyncio.to_thread(publisher))
    assert await asyncio.to_thread(started.wait, 2) is True
    cycle_task = asyncio.create_task(
        run_backup_cycle(
            source=source,
            output=output,
            timeout=2,
            interval_seconds=3600,
            coordinator=coordinator,
        )
    )
    await asyncio.sleep(0.1)
    assert cycle_task.done() is False
    release.set()
    await publisher_task
    result = await cycle_task
    assert result.backup.verified is True
    assert coordinator.active is False


@pytest.mark.asyncio
async def test_cycle_waits_for_expiry_deletion_lease(tmp_path):
    source = tmp_path / "data"
    output = tmp_path / "backups"
    _source_with_one_file(source)
    coordinator = MaintenanceCoordinator()
    started = threading.Event()
    release = threading.Event()

    async def expirer():
        async with coordinator.guard(EXPIRY_DELETION):
            started.set()
            await asyncio.to_thread(release.wait, 5)

    expirer_task = asyncio.create_task(expirer())
    assert await asyncio.to_thread(started.wait, 2) is True
    cycle_task = asyncio.create_task(
        run_backup_cycle(
            source=source,
            output=output,
            timeout=2,
            interval_seconds=3600,
            coordinator=coordinator,
        )
    )
    await asyncio.sleep(0.1)
    assert cycle_task.done() is False
    release.set()
    await expirer_task
    result = await cycle_task
    assert result.backup.verified is True
    assert coordinator.active is False


@pytest.mark.asyncio
async def test_manifest_table_counts_detect_changed_expected_counts(tmp_path):
    source = tmp_path / "data"
    output = tmp_path / "backups"
    _source_with_one_file(source)
    result = await run_backup_cycle(source=source, output=output, keep=7, interval_seconds=3600)
    manifest, _digest = load_manifest(result.backup.backup_dir)
    database = dict(manifest.database)
    database["table_counts"] = {"alembic_version": 0, "assets": 0, "payload": 0}
    write_manifest(result.backup.backup_dir, replace(manifest, database=database))
    with pytest.raises(BackupIntegrityError) as excinfo:
        verify_backup(result.backup.backup_dir)
    assert excinfo.value.code == "DATABASE_TABLE_COUNTS_MISMATCH"


@pytest.mark.asyncio
async def test_lifespan_starts_catch_up_scheduler_and_has_no_export_route(tmp_path, registry):
    base = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        web_dist_dir=tmp_path / "web",
        app_public_url="https://testserver",
        allowed_hosts=["testserver"],
    )
    settings = _test_settings(
        base,
        backup_dir=tmp_path / "backups",
        backup_interval_seconds=3600,
        backup_timeout_seconds=5,
        backup_keep=7,
    )
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        status = None
        for _ in range(100):
            status = read_status(settings.resolved_backup_dir())
            if status and status.get("backup_id"):
                break
            await asyncio.sleep(0.1)
        assert status is not None
        assert status.get("backup_id")
        assert application.state.backup_task is not None
        paths = {getattr(route, "path", "") for route in application.routes}
        assert not any("backup" in path or "maintenance" in path for path in paths)

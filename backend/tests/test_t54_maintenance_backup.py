from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading

import pytest

from app.db import write_transaction
from app.maintenance.backup import (
    create_backup,
    inspect_sources_report,
    read_latest,
    restore_verify,
    verify_backup,
)
from app.maintenance.cli import main as cli_main
from app.maintenance.coordinator import (
    BUSINESS_WRITE,
    EXPIRY_DELETION,
    FILE_PUBLICATION,
    MaintenanceBlocked,
    MaintenanceCoordinator,
    MaintenanceTimeout,
)
from app.maintenance.errors import BackupError, BackupIntegrityError
from app.maintenance.sources import discover_candidates, inspect_candidate


@pytest.mark.asyncio
async def test_inflight_writer_drains_and_new_writers_are_rejected():
    coordinator = MaintenanceCoordinator()
    writer_started = asyncio.Event()
    release_writer = asyncio.Event()
    maintenance_entered = asyncio.Event()

    async def writer():
        async with coordinator.guard(BUSINESS_WRITE):
            writer_started.set()
            await release_writer.wait()

    writer_task = asyncio.create_task(writer())
    await writer_started.wait()

    async def maintenance():
        async with coordinator.maintenance(timeout=2):
            maintenance_entered.set()
            assert coordinator.in_flight == {}
            with pytest.raises(MaintenanceBlocked):
                async with coordinator.guard(FILE_PUBLICATION):
                    pass

    maintenance_task = asyncio.create_task(maintenance())
    await asyncio.sleep(0.05)
    assert coordinator.active is True
    assert not maintenance_entered.is_set()

    with pytest.raises(MaintenanceBlocked):
        async with coordinator.guard(BUSINESS_WRITE):
            pass

    release_writer.set()
    await writer_task
    await asyncio.wait_for(maintenance_entered.wait(), timeout=1)
    await maintenance_task
    assert coordinator.active is False


@pytest.mark.asyncio
async def test_file_publication_lease_drains_and_rejects_new_publishers():
    coordinator = MaintenanceCoordinator()
    started = threading.Event()
    release = threading.Event()

    def publisher():
        with coordinator.file_publication():
            started.set()
            release.wait(2)

    task = asyncio.create_task(asyncio.to_thread(publisher))
    assert await asyncio.to_thread(started.wait, 1) is True

    entered = asyncio.Event()

    async def maintenance():
        async with coordinator.maintenance(timeout=2):
            entered.set()

    maintenance_task = asyncio.create_task(maintenance())
    await asyncio.sleep(0.05)
    assert coordinator.active is True
    assert not entered.is_set()
    with pytest.raises(MaintenanceBlocked):
        with coordinator.file_publication():
            pass

    release.set()
    await task
    await asyncio.wait_for(entered.wait(), timeout=1)
    await maintenance_task
    assert coordinator.active is False


@pytest.mark.asyncio
async def test_file_publication_timeout_releases_gate():
    coordinator = MaintenanceCoordinator()
    started = threading.Event()
    release = threading.Event()

    def publisher():
        with coordinator.file_publication():
            started.set()
            release.wait(2)

    task = asyncio.create_task(asyncio.to_thread(publisher))
    assert await asyncio.to_thread(started.wait, 1) is True

    with pytest.raises(MaintenanceTimeout) as excinfo:
        async with coordinator.maintenance(timeout=0.05):
            pass
    assert excinfo.value.in_flight == {FILE_PUBLICATION: 1}
    assert coordinator.active is False

    release.set()
    await task


@pytest.mark.asyncio
async def test_timeout_exits_with_failure_and_releases_gate():
    coordinator = MaintenanceCoordinator()
    release = asyncio.Event()

    started = asyncio.Event()

    async def writer():
        async with coordinator.guard(EXPIRY_DELETION):
            started.set()
            await release.wait()

    task = asyncio.create_task(writer())
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(MaintenanceTimeout) as excinfo:
        async with coordinator.maintenance(timeout=0.05):
            pass
    assert excinfo.value.in_flight == {EXPIRY_DELETION: 1}
    assert coordinator.active is False

    release.set()
    await task


def _make_db(root, rows=("committed",), *, keep_open=False):
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "app.db")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS t(x TEXT)")
    connection.executemany("INSERT INTO t VALUES (?)", [(row,) for row in rows])
    connection.commit()
    if keep_open:
        return connection
    connection.close()
    return None


def _tree_digest(root):
    entries = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            entries.append(
                (
                    path.relative_to(root).as_posix(),
                    path.stat().st_size,
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            )
    return entries


def test_inspect_sources_reports_candidates_and_digests_without_modifying(tmp_path):
    first = tmp_path / "data-a"
    second = tmp_path / "data-b"
    _make_db(first, rows=("a",))
    _make_db(second, rows=("b", "b2"))
    (first / "references").mkdir()
    (first / "references" / "ref.bin").write_bytes(b"reference")
    (first / "references" / ".env").write_text("SECRET=1", encoding="utf-8")
    (first / "references" / "private.key").write_bytes(b"key")
    (first / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")
    (second / "generated").mkdir()
    (second / "generated" / "gen.bin").write_bytes(b"generated")
    before = _tree_digest(tmp_path)

    candidates = discover_candidates([first, second])

    assert [item.root for item in candidates] == [first.resolve(), second.resolve()]
    assert all(item.app_db_present for item in candidates)
    assert candidates[0].content_digest != candidates[1].content_digest
    assert len(candidates[0].content_digest) == 64
    assert [item.relative_path for item in candidates[0].files] == ["references/ref.bin"]
    assert candidates[0].excluded_files >= 2
    assert candidates[0].database is not None
    assert (
        candidates[0].database.sha256 == hashlib.sha256((first / "app.db").read_bytes()).hexdigest()
    )

    assert _tree_digest(tmp_path) == before
    assert not (first / "app.db-wal").exists()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _asset_row(asset_id: str, relative_path: str, data: bytes, kind: str = "reference"):
    return {
        "id": asset_id,
        "kind": kind,
        "relative_path": relative_path,
        "byte_size": len(data),
        "sha256": _sha(data),
    }


def _create_source(
    root,
    *,
    rows=(),
    files=None,
    payload_rows=(),
    keep_open=False,
):
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "app.db")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO alembic_version VALUES ('0002')")
    connection.execute(
        "CREATE TABLE assets ("
        "id TEXT PRIMARY KEY, kind TEXT NOT NULL, relative_path TEXT NOT NULL, "
        "mime_type TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL, "
        "byte_size INTEGER NOT NULL, sha256 TEXT, review_status TEXT NOT NULL, "
        "created_at TEXT NOT NULL)"
    )
    connection.execute("CREATE TABLE payload (id TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for asset in rows:
        connection.execute(
            "INSERT INTO assets VALUES "
            "(?, ?, ?, 'application/octet-stream', 1, 1, ?, ?, 'approved', 'now')",
            (
                asset["id"],
                asset["kind"],
                asset["relative_path"],
                asset["byte_size"],
                asset["sha256"],
            ),
        )
    for payload in payload_rows:
        connection.execute("INSERT INTO payload VALUES (?, ?)", payload)
    connection.commit()
    for relative_path, data in (files or {}).items():
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if keep_open:
        return connection
    connection.close()
    return None


def _backup_names(output):
    if not output.exists():
        return []
    return sorted(path.name for path in output.iterdir() if path.name.startswith("backup-"))


def _open_immutable(path):
    return sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)


@pytest.mark.asyncio
async def test_backup_leaves_clean_source_content_digest_unchanged(tmp_path):
    source = tmp_path / "data"
    data = b"reference-payload"
    _create_source(
        source,
        rows=[_asset_row("asset-1", "references/ref.bin", data)],
        files={"references/ref.bin": data},
    )
    before = inspect_candidate(source).content_digest
    await create_backup(source=source, output=tmp_path / "backups", timeout=2)
    after = inspect_candidate(source)
    assert after.content_digest == before
    assert not (source / "app.db-wal").exists()
    assert not (source / "app.db-shm").exists()


@pytest.mark.asyncio
async def test_single_source_backup_includes_wal_commit_and_verified_manifest(tmp_path):
    source = tmp_path / "data"
    reference = b"reference-payload"
    generated = b"generated-payload"
    writer = _create_source(
        source,
        rows=[
            _asset_row("asset-ref", "references/ref.bin", reference),
            _asset_row("asset-gen", "generated/gen.bin", generated, kind="generated"),
        ],
        files={
            "references/ref.bin": reference,
            "generated/gen.bin": generated,
        },
        payload_rows=[("wal-commit", "committed-in-wal")],
        keep_open=True,
    )
    try:
        result = await create_backup(
            source=source,
            output=tmp_path / "backups",
            candidate_roots=[source],
            timeout=2.0,
        )
    finally:
        writer.close()

    assert result.verified is True
    assert result.file_count == 2
    latest = read_latest(tmp_path / "backups")
    assert latest is not None
    assert latest["backup_id"] == result.backup_id
    backup_dir = tmp_path / "backups" / latest["backup_dir"]
    assert backup_dir == result.backup_dir
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["database"]["asset_count"] == 2
    assert [item["relative_path"] for item in manifest["files"]] == [
        "generated/gen.bin",
        "references/ref.bin",
    ]

    restored = restore_verify(
        backup=backup_dir,
        output=tmp_path / "rehearsal",
        confirmation="REHEARSE",
    )
    assert restored.verified is True
    connection = _open_immutable(restored.restored_dir / "data" / "app.db")
    try:
        assert connection.execute(
            "SELECT value FROM payload WHERE id = 'wal-commit'"
        ).fetchone() == ("committed-in-wal",)
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone() == (2,)
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_multiple_non_empty_sources_require_explicit_authorization(tmp_path):
    first = tmp_path / "data-a"
    second = tmp_path / "data-b"
    _create_source(
        first, rows=[_asset_row("a1", "references/a.bin", b"a")], files={"references/a.bin": b"a"}
    )
    _create_source(
        second, rows=[_asset_row("b1", "references/b.bin", b"b")], files={"references/b.bin": b"b"}
    )
    report = inspect_sources_report([first, second])
    assert report["non_empty_count"] == 2
    assert report["requires_explicit_choice"] is True
    output = tmp_path / "backups"
    with pytest.raises(BackupError) as excinfo:
        await create_backup(source=first, output=output, candidate_roots=[first, second])
    assert excinfo.value.code == "SOURCE_CHOICE_REQUIRED"
    assert read_latest(output) is None
    assert _backup_names(output) == []

    digest = inspect_candidate(first).content_digest
    with pytest.raises(BackupError) as excinfo:
        await create_backup(
            source=first,
            output=output,
            candidate_roots=[first, second],
            confirm_source_digest="0" * 64,
        )
    assert excinfo.value.code == "SOURCE_DIGEST_MISMATCH"

    result = await create_backup(
        source=first,
        output=output,
        candidate_roots=[first, second],
        confirm_source_digest=digest,
    )
    assert result.verified is True
    assert read_latest(output)["backup_id"] == result.backup_id


@pytest.mark.asyncio
async def test_corrupted_snapshot_and_manifest_are_rejected(tmp_path):
    source = tmp_path / "data"
    data = b"reference"
    _create_source(
        source,
        rows=[_asset_row("a1", "references/a.bin", data)],
        files={"references/a.bin": data},
    )
    result = await create_backup(
        source=source, output=tmp_path / "backups", candidate_roots=[source]
    )
    backup_dir = result.backup_dir
    assert verify_backup(backup_dir).ok is True

    (backup_dir / "data" / "references" / "a.bin").write_bytes(b"corrupted")
    with pytest.raises(BackupIntegrityError) as excinfo:
        verify_backup(backup_dir)
    assert excinfo.value.code == "FILE_DIGEST_MISMATCH"

    # Restore the file and corrupt the manifest instead.
    (backup_dir / "data" / "references" / "a.bin").write_bytes(data)
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(manifest_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(BackupIntegrityError) as excinfo:
        verify_backup(backup_dir)
    assert excinfo.value.code == "MANIFEST_DIGEST_MISMATCH"


@pytest.mark.asyncio
async def test_failed_staging_verification_keeps_previous_latest_usable(tmp_path, monkeypatch):
    source = tmp_path / "data"
    data = b"reference"
    _create_source(
        source,
        rows=[_asset_row("a1", "references/a.bin", data)],
        files={"references/a.bin": data},
    )
    output = tmp_path / "backups"
    first = await create_backup(source=source, output=output, candidate_roots=[source])
    first_latest = read_latest(output)

    def fail_verify(*args, **kwargs):
        raise BackupIntegrityError("STAGED_VERIFY_FAILED", "injected verification failure")

    monkeypatch.setattr("app.maintenance.backup.verify_backup", fail_verify)
    with pytest.raises(BackupIntegrityError) as excinfo:
        await create_backup(source=source, output=output, candidate_roots=[source])
    assert excinfo.value.code == "STAGED_VERIFY_FAILED"
    assert read_latest(output) == first_latest
    assert _backup_names(output) == [first.backup_dir.name]
    assert verify_backup(first.backup_dir).ok is True


@pytest.mark.asyncio
async def test_secret_env_and_key_material_are_excluded(tmp_path):
    source = tmp_path / "data"
    data = b"reference"
    _create_source(
        source,
        rows=[_asset_row("a1", "references/a.bin", data)],
        files={"references/a.bin": data},
    )
    (source / ".env").write_text("LLM_API_KEY=secret", encoding="utf-8")
    (source / "references" / "private.key").write_bytes(b"key")
    (source / "references" / "tls.pem").write_bytes(b"pem")
    result = await create_backup(
        source=source, output=tmp_path / "backups", candidate_roots=[source]
    )
    names = [item.name for item in result.backup_dir.rglob("*")]
    assert ".env" not in names
    assert "private.key" not in names
    assert "tls.pem" not in names
    assert [
        item["relative_path"]
        for item in json.loads((result.backup_dir / "manifest.json").read_text(encoding="utf-8"))[
            "files"
        ]
    ] == [
        "references/a.bin",
    ]
    assert inspect_candidate(source).excluded_files >= 3


@pytest.mark.asyncio
async def test_database_path_outside_allowlist_is_refused(tmp_path):
    source = tmp_path / "data"
    _create_source(
        source,
        rows=[_asset_row("bad", ".env", b"secret")],
        files={".env": b"secret"},
    )
    with pytest.raises(BackupError) as excinfo:
        await create_backup(source=source, output=tmp_path / "backups", candidate_roots=[source])
    assert excinfo.value.code == "SOURCE_FILE_UNSAFE"


@pytest.mark.asyncio
async def test_missing_database_referenced_file_is_refused(tmp_path):
    source = tmp_path / "data"
    _create_source(
        source,
        rows=[_asset_row("a1", "references/missing.bin", b"missing")],
        files={},
    )
    output = tmp_path / "backups"
    with pytest.raises(BackupError) as excinfo:
        await create_backup(source=source, output=output, candidate_roots=[source])
    assert excinfo.value.code == "SOURCE_FILE_MISSING"
    assert excinfo.value.details["missing"][0]["relative_path"] == "references/missing.bin"
    assert read_latest(output) is None
    assert _backup_names(output) == []


@pytest.mark.asyncio
async def test_backup_timeout_leaves_no_half_published_backup(tmp_path):
    source = tmp_path / "data"
    data = b"reference"
    _create_source(
        source,
        rows=[_asset_row("a1", "references/a.bin", data)],
        files={"references/a.bin": data},
    )
    coordinator = MaintenanceCoordinator()
    release = asyncio.Event()
    started = asyncio.Event()

    async def writer():
        async with coordinator.guard(BUSINESS_WRITE):
            started.set()
            await release.wait()

    task = asyncio.create_task(writer())
    await started.wait()
    output = tmp_path / "backups"
    with pytest.raises(BackupError) as excinfo:
        await create_backup(
            source=source,
            output=output,
            candidate_roots=[source],
            coordinator=coordinator,
            timeout=0.05,
        )
    assert excinfo.value.code == "MAINTENANCE_TIMEOUT"
    assert read_latest(output) is None
    assert _backup_names(output) == []
    release.set()
    await task
    assert coordinator.active is False


@pytest.mark.asyncio
async def test_backup_waits_for_inflight_writer_and_includes_its_commit(tmp_path):
    source = tmp_path / "data"
    data = b"reference"
    _create_source(
        source,
        rows=[_asset_row("a1", "references/a.bin", data)],
        files={"references/a.bin": data},
        payload_rows=[("before", "before-maintenance")],
    )
    coordinator = MaintenanceCoordinator()
    writer_entered = asyncio.Event()
    release_writer = asyncio.Event()

    async def writer():
        async with coordinator.guard(BUSINESS_WRITE):
            connection = sqlite3.connect(source / "app.db")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO payload VALUES ('writer', 'committed-while-draining')")
            connection.commit()
            connection.close()
            writer_entered.set()
            await release_writer.wait()

    writer_task = asyncio.create_task(writer())
    await writer_entered.wait()
    backup_task = asyncio.create_task(
        create_backup(
            source=source,
            output=tmp_path / "backups",
            candidate_roots=[source],
            coordinator=coordinator,
            timeout=2.0,
        )
    )
    await asyncio.sleep(0.05)
    assert coordinator.active is True
    assert backup_task.done() is False
    release_writer.set()
    await writer_task
    result = await backup_task
    connection = _open_immutable(result.backup_dir / "data" / "app.db")
    try:
        assert connection.execute("SELECT value FROM payload WHERE id = 'writer'").fetchone() == (
            "committed-while-draining",
        )
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_restore_verify_requires_confirmation_and_stays_isolated(tmp_path):
    source = tmp_path / "data"
    data = b"reference"
    _create_source(
        source,
        rows=[_asset_row("a1", "references/a.bin", data)],
        files={"references/a.bin": data},
    )
    result = await create_backup(
        source=source, output=tmp_path / "backups", candidate_roots=[source]
    )
    before = _tree_digest(result.backup_dir)
    with pytest.raises(BackupError) as excinfo:
        restore_verify(
            backup=result.backup_dir,
            output=tmp_path / "rehearsal-denied",
            confirmation="NOT-AUTHORIZED",
        )
    assert excinfo.value.code == "RESTORE_CONFIRMATION_REQUIRED"
    assert not (tmp_path / "rehearsal-denied").exists()

    restored = restore_verify(
        backup=result.backup_dir,
        output=tmp_path / "rehearsal",
        confirmation="REHEARSE",
    )
    assert restored.verified is True
    assert restored.production_switched is False
    assert _tree_digest(result.backup_dir) == before


@pytest.mark.asyncio
async def test_fastapi_maintenance_gate_rejects_mutations_but_allows_reads(app, client):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with app.state.maintenance.maintenance(timeout=2):
            entered.set()
            await release.wait()

    task = asyncio.create_task(hold())
    await entered.wait()
    try:
        blocked = await client.post("/api/projects", json={})
        assert blocked.status_code == 503
        assert blocked.json()["error"]["code"] == "MAINTENANCE_ACTIVE"
        health = await client.get("/api/health")
        assert health.status_code == 200
        with pytest.raises(MaintenanceBlocked):
            async with write_transaction(app.state.session_factory) as _session:
                pass
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_backup_of_running_app_data_root_contains_committed_project(app, client, tmp_path):
    created = await client.post("/api/projects", json={})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    data_root = app.state.settings.resolved_data_dir()
    result = await create_backup(
        source=data_root,
        output=tmp_path / "backups",
        candidate_roots=[data_root],
        coordinator=app.state.maintenance,
        timeout=2.0,
    )
    connection = _open_immutable(result.backup_dir / "data" / "app.db")
    try:
        assert connection.execute(
            "SELECT id FROM projects WHERE id = ?", (project_id,)
        ).fetchone() == (project_id,)
    finally:
        connection.close()


def test_cli_inspect_backup_verify_and_restore_confirmation(tmp_path):
    source = tmp_path / "data"
    data = b"reference"
    _create_source(
        source,
        rows=[_asset_row("a1", "references/a.bin", data)],
        files={"references/a.bin": data},
    )
    output = tmp_path / "backups"
    assert cli_main(["inspect-sources", "--candidate-root", str(source), "--json"]) == 0
    assert (
        cli_main(
            [
                "backup",
                "--source",
                str(source),
                "--output",
                str(output),
                "--service-stopped",
                "--candidate-root",
                str(source),
                "--json",
            ]
        )
        == 0
    )
    latest = read_latest(output)
    assert latest is not None
    backup_dir = output / latest["backup_dir"]
    assert cli_main(["verify", "--backup", str(backup_dir), "--json"]) == 0
    assert (
        cli_main(
            [
                "restore-verify",
                "--backup",
                str(backup_dir),
                "--output",
                str(tmp_path / "denied"),
                "--confirm-restore",
                "NOPE",
                "--json",
            ]
        )
        == 1
    )
    assert (
        cli_main(
            [
                "restore-verify",
                "--backup",
                str(backup_dir),
                "--output",
                str(tmp_path / "rehearsal"),
                "--confirm-restore",
                "REHEARSE",
                "--json",
            ]
        )
        == 0
    )

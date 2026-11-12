from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import threading
import uuid
import zipfile
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.auth.models import AuditEvent
from app.db import write_transaction
from app.main import _run_retention_cleanup
from app.maintenance.backup import BackupError, create_backup
from app.maintenance.coordinator import EXPIRY_DELETION
from app.maintenance.manifest import load_manifest, verify_backup
from app.models import Asset, Project
from app.services import retention
from app.timeutil import format_timestamp, utc_now
from app.workflow.tables import DiagnosticEvent
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.test_v2_t20_retention import _live_app, _png, _seed_candidate, _seed_diagnostic

REPO_ROOT = Path(__file__).resolve().parents[2]
_SECRET_SCAN_PATH = REPO_ROOT / "tools" / "secret_scan.py"
_spec = importlib.util.spec_from_file_location("t83_secret_scan", _SECRET_SCAN_PATH)
assert _spec is not None and _spec.loader is not None
secret_scan = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = secret_scan
_spec.loader.exec_module(secret_scan)


def _ts(moment) -> str:
    return format_timestamp(moment)


async def _seed_audit(app, *, age: timedelta, event_type: str = "auth_login") -> str:
    event_id = f"audit-{uuid.uuid4()}"
    async with write_transaction(app.state.session_factory) as session:
        session.add(
            AuditEvent(
                id=event_id,
                occurred_at=_ts(utc_now() - age),
                event_type=event_type,
                actor_kind="system",
                result="success",
                correlation_id=str(uuid.uuid4()),
            )
        )
        await session.flush()
    return event_id


async def _seed_plain_project(app) -> Project:
    async with write_transaction(app.state.session_factory) as session:
        project = Project(
            id=f"project-{uuid.uuid4()}",
            title="T83 保密项目",
            revision=0,
            brief_status="draft",
            product_description="",
            purpose="",
            style="",
            composition="",
            text_content="",
            image_size=None,
            prompt="",
            created_at=_ts(utc_now()),
            updated_at=_ts(utc_now()),
        )
        session.add(project)
        await session.flush()
        return project


@pytest.mark.asyncio
async def test_security_audit_expires_at_ninety_days_but_diagnostics_at_thirty(full_settings):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=5)
    async with _live_app(full_settings, registry) as (app, _client):
        fresh = await _seed_audit(app, age=timedelta(days=89, hours=23))
        boundary = await _seed_audit(app, age=timedelta(days=90, seconds=1))
        old = await _seed_audit(app, age=timedelta(days=120))
        old_retention_diag = await _seed_diagnostic(
            app, event="retention_cleanup_failed", age=timedelta(days=31)
        )
        fresh_diag = await _seed_diagnostic(
            app, event="generation_progress", age=timedelta(days=29)
        )

        report = await retention.run_retention_cleanup(app, now=fixed_now)

        assert report.scanned_audits >= 3
        assert report.deleted_audit_ids == {boundary, old}
        assert report.deleted_diagnostic_ids == {old_retention_diag}
        assert report.failures == []
        async with app.state.session_factory() as session:
            remaining_audits = set((await session.scalars(select(AuditEvent.id))).all())
            remaining_diags = set((await session.scalars(select(DiagnosticEvent.id))).all())
        assert fresh in remaining_audits
        assert boundary not in remaining_audits
        assert old not in remaining_audits
        assert fresh_diag in remaining_diags
        assert old_retention_diag not in remaining_diags


@pytest.mark.asyncio
async def test_retention_skips_during_backup_maintenance_and_backup_waits_for_retention_lease(
    full_settings,
):
    registry = build_test_registry(LLMStub(), VisualStub())
    async with _live_app(full_settings, registry) as (app, _client):
        data_dir = Path(app.state.settings.resolved_data_dir())
        candidate = await _seed_candidate(app, completed_delta=timedelta(hours=25))
        started = threading.Event()
        release = threading.Event()

        async def expirer():
            async with app.state.maintenance.guard(EXPIRY_DELETION):
                started.set()
                await asyncio.to_thread(release.wait, 5)

        expirer_task = asyncio.create_task(expirer())
        assert await asyncio.to_thread(started.wait, 2) is True
        backup_dir = Path(full_settings.data_dir).parent / "t83-backup-coord"
        backup_task = asyncio.create_task(
            create_backup(
                source=data_dir,
                output=backup_dir,
                candidate_roots=[data_dir],
                timeout=10,
                coordinator=app.state.maintenance,
            )
        )
        await asyncio.sleep(0.1)
        assert backup_task.done() is False
        release.set()
        await expirer_task
        backup = await backup_task
        assert backup.verified is True

        async with app.state.maintenance.maintenance(timeout=5):
            await _run_retention_cleanup(app)
            assert candidate.path.is_file()
        await _run_retention_cleanup(app)
        assert not candidate.path.is_file()


def test_secret_scan_flags_markers_and_zip_without_printing_values(tmp_path):
    marker = "T83-SYNTHETIC-SECRET-4f6c1e7a"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "app.js").write_text(f"const leaked = '{marker}';", encoding="utf-8")
    (bundle / "sha256.txt").write_text("a" * 64, encoding="utf-8")
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("logs/app.log", f"secret={marker}")

    directory_hits = secret_scan.scan_directory(bundle, (marker,))
    zip_hits = secret_scan.scan_zip(archive, (marker,))
    assert any(hit.path == "app.js" and hit.kind == "marker" for hit in directory_hits)
    assert any(hit.path == "logs/app.log" and hit.kind == "marker" for hit in zip_hits)
    all_hits = [*directory_hits, *zip_hits]
    assert all(
        marker not in json.dumps(hit.__dict__, ensure_ascii=False) for hit in all_hits
    )
    assert not [hit for hit in directory_hits if hit.path == "sha256.txt"]


@pytest.mark.asyncio
async def test_secret_markers_stay_out_of_api_database_filenames_and_backup(full_settings):
    marker = "T83-CONFIG-SECRET-8f2c9d1a"
    visual_marker = "T83-VISUAL-SECRET-1a8e4b2c"
    settings = full_settings.model_copy(
        update={
            "llm_api_key": SecretStr(marker),
            "visual_api_key": SecretStr(visual_marker),
        }
    )
    registry = build_test_registry(LLMStub(), VisualStub())
    data_dir = Path(settings.resolved_data_dir())
    backup_dir = Path(full_settings.data_dir).parent / "t83-backups"
    registry.stub_llm.responder = None
    async with _live_app(settings, registry) as (app, client):
        config_response = await client.get("/api/config/status")
        session_response = await client.get("/api/auth/session")
        project_response = await client.post("/api/projects", json={})
        assert config_response.status_code == 200
        assert session_response.status_code == 200
        assert project_response.status_code == 201
        project_id = project_response.json()["id"]

        message_response = await client.post(
            f"/api/projects/{project_id}/messages",
            json={"content": "请描述这个商品的卖点", "expected_revision": 0},
        )
        assert message_response.status_code == 200, message_response.text
        detail_before_upload = await client.get(f"/api/projects/{project_id}")
        assert detail_before_upload.status_code == 200
        upload_response = await client.post(
            f"/api/projects/{project_id}/reference",
            files={"file": (f"{marker}.png", _png((30, 60, 90)), "image/png")},
            data={"expected_revision": str(detail_before_upload.json()["project"]["revision"])},
        )
        assert upload_response.status_code == 200, upload_response.text
        assert marker not in upload_response.text

        (data_dir / ".env").write_text(f"LLM_API_KEY={marker}\n", encoding="utf-8")
        result = await create_backup(
            source=data_dir,
            output=backup_dir,
            candidate_roots=[data_dir],
            timeout=10,
            coordinator=app.state.maintenance,
        )
        assert result.verified is True

        response_text = "\n".join(
            [
                config_response.text,
                session_response.text,
                project_response.text,
                message_response.text,
            ]
        )
        assert marker not in response_text
        assert visual_marker not in response_text
        assert marker not in repr(settings)
        assert visual_marker not in repr(settings)

        db_bytes = (data_dir / "app.db").read_bytes()
        assert marker.encode() not in db_bytes
        assert visual_marker.encode() not in db_bytes

        model_request = asdict(registry.stub_llm.calls[0])
        assert marker not in json.dumps(model_request, ensure_ascii=False, default=str)
        assert visual_marker not in json.dumps(model_request, ensure_ascii=False, default=str)

        paths = await asyncio.to_thread(lambda: list(data_dir.rglob("*")))
        for path in paths:
            if path.name == ".env":
                continue
            assert marker not in path.name and visual_marker not in path.name

    backup_hits = secret_scan.scan_directory(backup_dir, (marker, visual_marker))
    assert backup_hits == []


@pytest.mark.asyncio
async def test_audit_delete_failure_keeps_row_and_reports_redacted_failure(
    full_settings, monkeypatch
):
    registry = build_test_registry(LLMStub(), VisualStub())
    fixed_now = utc_now() + timedelta(seconds=5)
    async with _live_app(full_settings, registry) as (app, _client):
        old = await _seed_audit(app, age=timedelta(days=120))

        class FailingTransaction:
            async def __aenter__(self):
                raise RuntimeError("injected audit delete failure")

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        def failing_transaction(*_args, **_kwargs):
            return FailingTransaction()

        monkeypatch.setattr(retention, "write_transaction", failing_transaction)
        report = await retention.run_retention_cleanup(app, now=fixed_now)

        failure = next(
            item for item in report.failures if item.object_id == old
        )
        assert failure.object_kind == "audit"
        assert failure.stage == "audit"
        assert failure.error_code == "AUDIT_DELETE_FAILED"
        assert old not in report.deleted_audit_ids
        async with app.state.session_factory() as session:
            assert await session.get(AuditEvent, old) is not None


def test_secret_scan_cli_reports_hits_without_echoing_marker(tmp_path):
    marker = "T83-CLI-SECRET-92d4c7b1"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "index.js").write_text(f"const leaked = '{marker}';", encoding="utf-8")
    command = [
        sys.executable,
        str(_SECRET_SCAN_PATH),
        "--root",
        str(bundle),
        "--marker",
        marker,
        "--json",
    ]
    failed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert failed.returncode == 1
    assert marker not in failed.stdout
    assert marker not in failed.stderr
    payload = json.loads(failed.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["hits"]

    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "index.js").write_text("const value = 1;", encoding="utf-8")
    passed = subprocess.run(
        [
            sys.executable,
            str(_SECRET_SCAN_PATH),
            "--root",
            str(clean),
            "--marker",
            marker,
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert passed.returncode == 0
    assert json.loads(passed.stdout.strip().splitlines()[-1])["ok"] is True

@pytest.mark.asyncio
async def test_backup_accepts_retention_expired_candidate_but_not_unknown_missing(full_settings):
    registry = build_test_registry(LLMStub(), VisualStub())
    async with _live_app(full_settings, registry) as (app, _client):
        data_dir = Path(app.state.settings.resolved_data_dir())
        candidate = await _seed_candidate(app, completed_delta=timedelta(hours=25))
        await _run_retention_cleanup(app)
        assert not candidate.path.is_file()
        async with app.state.session_factory() as session:
            asset = await session.get(Asset, candidate.asset_id)
            assert asset is not None
            assert asset.review_note == retention.RETENTION_EXPIRED_NOTE

        backup_dir = Path(full_settings.data_dir).parent / "t83-retention-backup"
        backup = await create_backup(
            source=data_dir,
            output=backup_dir,
            candidate_roots=[data_dir],
            timeout=10,
            coordinator=app.state.maintenance,
        )
        assert backup.verified is True
        assert backup.missing_files == 1
        manifest, _digest = load_manifest(backup.backup_dir)
        assert manifest.missing[0]["reason"] == retention.RETENTION_EXPIRED_NOTE
        assert verify_backup(backup.backup_dir).ok is True

        unknown = await _seed_candidate(app, completed_delta=timedelta(hours=25))
        unknown.path.unlink()
        with pytest.raises(BackupError) as raised:
            await create_backup(
                source=data_dir,
                output=Path(full_settings.data_dir).parent / "t83-unknown-backup",
                candidate_roots=[data_dir],
                timeout=10,
                coordinator=app.state.maintenance,
            )
        assert raised.value.code == "SOURCE_FILE_MISSING"

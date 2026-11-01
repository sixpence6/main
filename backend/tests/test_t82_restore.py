from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.auth.security_events import SAFE_DETAIL_KEYS, SECURITY_EVENT_TYPES
from app.config import Settings
from app.maintenance.backup import _create_backup_sync, verify_backup
from app.maintenance.errors import BackupError
from app.maintenance.restore import clear_restore_gap, restore_switch
from app.maintenance.restore_state import (
    GATE_NAME,
    GATE_SCHEMA,
    gate_is_open,
    read_restore_gate,
    slot_floor_count,
    usage_floor_count,
    write_restore_gate,
)
from app.maintenance.sources import inspect_candidate
from app.timeutil import format_timestamp
from tests.conftest import migrate

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
IMAGE_BYTES = b"PNG1"
IMAGE_SHA = hashlib.sha256(IMAGE_BYTES).hexdigest()


def _settings(root: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=root / "data",
        web_dist_dir=root / "web",
        allowed_hosts=["testserver"],
        allowed_origins=["https://testserver"],
        allow_loopback_http=True,
        auth_cookie_secure=True,
        app_public_url="https://testserver",
    )


def _seed_source(root: Path) -> Path:
    settings = _settings(root)
    migrate(settings)
    source = settings.resolved_data_dir()
    (source / "published").mkdir(parents=True)
    (source / "published" / "asset-1.png").write_bytes(IMAGE_BYTES)
    stamp = format_timestamp(NOW)
    future = format_timestamp(NOW + timedelta(hours=1))
    absolute = format_timestamp(NOW + timedelta(hours=12))
    digest64 = "a" * 64
    connection = sqlite3.connect(source / "app.db")
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            connection.execute(
                "INSERT INTO accounts (id, email_normalized, password_hash, password_set_at, "
                "password_policy_version, is_admin, is_disabled, session_epoch, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 1, 0, 0, ?, ?)",
                ("account-1", "owner@example.com", "o" * 64, stamp, "r1-2026-10", stamp, stamp),
            )
            connection.execute(
                "INSERT INTO projects (id, title, revision, prompt_revision, confirmed_revision, "
                "brief_status, product_description, purpose, style, composition, text_content, "
                "image_size, prompt, created_at, updated_at, owner_account_id) "
                "VALUES (?, ?, 1, 1, 1, 'confirmed', '商品', '展示', '简洁', '居中', '品牌', "
                "'3840x2160', 'prompt', ?, ?, ?)",
                ("project-1", "恢复项目", stamp, stamp, "account-1"),
            )
            connection.execute(
                "INSERT INTO workflow_runs (id, project_id, sequence_no, stage, status, "
                "requirement_revision, created_at, updated_at) VALUES (?, ?, 1, 'S10', "
                "'completed', 1, ?, ?)",
                ("run-1", "project-1", stamp, stamp),
            )
            connection.execute(
                "INSERT INTO assets (id, project_id, kind, relative_path, mime_type, width, "
                "height, byte_size, created_at, sha256, review_status) VALUES (?, ?, 'generated', "
                "'published/asset-1.png', 'image/png', 3840, 2160, ?, ?, ?, 'approved')",
                ("asset-1", "project-1", len(IMAGE_BYTES), stamp, IMAGE_SHA),
            )
            connection.execute(
                "INSERT INTO approvals (id, project_id, artifact_id, approval_type, artifact_digest, "
                "approved_by, requirement_snapshot_digest, referenced_material_digest, prompt_version, "
                "rules_version, model_fingerprint, checks_json, created_at) VALUES (?, ?, NULL, "
                "'image', ?, 'user', ?, ?, 'v1', 'v1', 'fake', '{}', ?)",
                ("approval-1", "project-1", digest64, digest64, digest64, stamp),
            )
            connection.execute(
                "INSERT INTO generation_intents (id, project_id, run_id, owner_account_id, "
                "operator_account_id, approval_id, request_id, request_digest, requirement_revision, "
                "artifact_id, artifact_digest, reference_usage, image_size, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, ?, 'none', '3840x2160', 'delivered', ?, ?)",
                (
                    "intent-1",
                    "project-1",
                    "run-1",
                    "account-1",
                    "account-1",
                    "approval-1",
                    "request-1",
                    "b" * 64,
                    "c" * 64,
                    stamp,
                    stamp,
                ),
            )
            connection.execute(
                "INSERT INTO generation_usage (id, intent_id, account_id, project_id, "
                "window_start_ms, window_expires_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "usage-1",
                    "intent-1",
                    "account-1",
                    "project-1",
                    int(NOW.timestamp() * 1000) - 1000,
                    int(NOW.timestamp() * 1000) + 86400000,
                    stamp,
                ),
            )
            for scope, scope_id in (
                ("personal", "account-1"),
                ("instance", "instance"),
                ("project", "project-1"),
            ):
                connection.execute(
                    "INSERT INTO generation_slots (id, intent_id, scope, scope_id, created_at) "
                    "VALUES (?, 'intent-1', ?, ?, ?)",
                    (f"slot-{scope}", scope, scope_id, stamp),
                )
            connection.execute(
                "INSERT INTO generation_jobs (id, project_id, run_id, generation_id, request_id, "
                "request_digest, prompt_artifact_id, confirmed_snapshot_digest, status, "
                "submit_attempted, created_at, updated_at, owner_account_id, operator_account_id, "
                "intent_id) VALUES (?, ?, ?, NULL, ?, ?, NULL, ?, 'published', 0, ?, ?, ?, ?, ?)",
                (
                    "job-1",
                    "project-1",
                    "run-1",
                    "request-1",
                    "b" * 64,
                    "c" * 64,
                    stamp,
                    stamp,
                    "account-1",
                    "account-1",
                    "intent-1",
                ),
            )
            connection.execute(
                "INSERT INTO deliveries (id, project_id, generation_job_id, asset_id, "
                "manifest_artifact_id, status, filename, caption, asset_digest, approved_at, "
                "published_at, created_at) VALUES (?, ?, ?, ?, NULL, 'published', ?, '交付', ?, ?, ?, ?)",
                (
                    "delivery-1",
                    "project-1",
                    "job-1",
                    "asset-1",
                    "asset-1.png",
                    IMAGE_SHA,
                    stamp,
                    stamp,
                    stamp,
                ),
            )
            connection.execute(
                "INSERT INTO sessions (id, account_id, token_digest, csrf_digest, account_epoch, "
                "created_at, last_activity_at, idle_expires_at, absolute_expires_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)",
                ("session-1", "account-1", "d" * 64, "e" * 64, stamp, stamp, future, absolute),
            )
            connection.execute(
                "INSERT INTO account_links (id, account_id, purpose, token_digest, created_at, "
                "expires_at, created_by) VALUES (?, ?, 'setup', ?, ?, ?, ?)",
                ("link-1", "account-1", "f" * 64, stamp, future, "account-1"),
            )
    finally:
        connection.close()
    return source


def _mutate_after_snapshot(source: Path) -> None:
    stamp = format_timestamp(NOW + timedelta(minutes=1))
    connection = sqlite3.connect(source / "app.db")
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            connection.execute(
                "UPDATE approvals SET revoked_at = ?, revocation_reason = 'user_revoked' "
                "WHERE id = 'approval-1'",
                (stamp,),
            )
            connection.execute(
                "UPDATE deliveries SET status = 'revoked' WHERE id = 'delivery-1'"
            )
            connection.execute(
                "UPDATE assets SET review_status = 'rejected' WHERE id = 'asset-1'"
            )
            connection.execute(
                "UPDATE accounts SET password_hash = ? WHERE id = 'account-1'",
                ("n" * 64,),
            )
            connection.execute(
                "INSERT INTO generation_intents (id, project_id, run_id, owner_account_id, "
                "operator_account_id, approval_id, request_id, request_digest, requirement_revision, "
                "artifact_id, artifact_digest, reference_usage, image_size, status, created_at, updated_at) "
                "VALUES ('intent-2', 'project-1', 'run-1', 'account-1', 'account-1', NULL, "
                "'request-2', ?, 1, NULL, ?, 'none', '3840x2160', 'accepted', ?, ?)",
                ("b" * 64, "c" * 64, stamp, stamp),
            )
            connection.execute(
                "INSERT INTO generation_usage (id, intent_id, account_id, project_id, "
                "window_start_ms, window_expires_ms, created_at) VALUES ('usage-2', 'intent-2', "
                "'account-1', 'project-1', ?, ?, ?)",
                (
                    int(NOW.timestamp() * 1000) - 500,
                    int(NOW.timestamp() * 1000) + 86400000,
                    stamp,
                ),
            )
            connection.execute(
                "INSERT INTO generation_slots (id, intent_id, scope, scope_id, created_at) "
                "VALUES ('slot-extra', 'intent-2', 'personal', 'account-1', ?)",
                (stamp,),
            )
    finally:
        connection.close()


def _backup(source: Path, output: Path):
    output.mkdir()
    inspection = inspect_candidate(source)
    return _create_backup_sync(source, inspection, output)


def _row(source: Path, query: str):
    connection = sqlite3.connect(source / "app.db")
    try:
        return connection.execute(query).fetchone()
    finally:
        connection.close()


def test_restore_switch_revokes_credentials_and_preserves_ledger_floors(tmp_path):
    source = _seed_source(tmp_path)
    output = tmp_path / "backups"
    backup = _backup(source, output)
    assert verify_backup(backup.backup_dir).ok
    _mutate_after_snapshot(source)
    result = restore_switch(
        source=source,
        backup=backup.backup_dir,
        output=output,
        confirmation="RESTORE",
        service_stopped=True,
        now=NOW + timedelta(minutes=2),
    )
    assert result.production_switched is True
    assert result.backup_id == backup.backup_id
    assert result.safety_backup_id != backup.backup_id
    assert (output / result.safety_backup_dir.name).is_dir()
    assert result.sessions_revoked == 1
    assert result.links_revoked == 1
    assert result.approvals_revoked == 1
    assert result.deliveries_revoked == 1
    assert result.assets_guarded == 1
    assert result.usage_floor_accounts == 1
    assert read_restore_gate(source)["schema"] == GATE_SCHEMA
    assert gate_is_open(source, now=NOW + timedelta(minutes=2)) is True
    assert usage_floor_count(source, account_id="account-1", now_ms=int((NOW + timedelta(minutes=2)).timestamp() * 1000)) == 1
    assert slot_floor_count(source, scope="personal", scope_id="account-1", now=NOW + timedelta(minutes=2)) == 1
    assert _row(source, "SELECT revoked_reason FROM sessions WHERE id = 'session-1'") == (
        "restore_revoked",
    )
    assert _row(source, "SELECT session_epoch FROM accounts WHERE id = 'account-1'") == (1,)
    assert _row(source, "SELECT revoked_reason FROM account_links WHERE id = 'link-1'") == (
        "restore_revoked",
    )
    assert _row(source, "SELECT revoked_at IS NOT NULL FROM approvals WHERE id = 'approval-1'") == (1,)
    assert _row(source, "SELECT status FROM deliveries WHERE id = 'delivery-1'") == ("revoked",)
    assert _row(source, "SELECT review_status FROM assets WHERE id = 'asset-1'") == ("rejected",)
    assert _row(source, "SELECT password_hash FROM accounts WHERE id = 'account-1'") == ("n" * 64,)
    assert _row(source, "SELECT COUNT(*) FROM generation_usage") == (1,)
    assert (source / "published" / "asset-1.png").read_bytes() == IMAGE_BYTES
    gate = read_restore_gate(source)
    assert gate["status"] == "open"
    connection = sqlite3.connect(source / "app.db")
    try:
        audit = connection.execute(
            "SELECT event_type FROM audit_events WHERE event_type = 'maintenance_restore_completed'"
        ).fetchall()
    finally:
        connection.close()
    assert audit == [("maintenance_restore_completed",)]


def test_restore_requires_confirmation_and_does_not_touch_source_on_bad_backup(tmp_path):
    source = _seed_source(tmp_path)
    output = tmp_path / "backups"
    backup = _backup(source, output)
    original = (source / "app.db").read_bytes()
    with pytest.raises(BackupError) as excinfo:
        restore_switch(
            source=source,
            backup=backup.backup_dir,
            output=output,
            confirmation="NOT_RESTORE",
            service_stopped=True,
        )
    assert excinfo.value.code == "RESTORE_CONFIRMATION_REQUIRED"
    assert (source / "app.db").read_bytes() == original
    manifest = backup.backup_dir / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(BackupError):
        restore_switch(
            source=source,
            backup=backup.backup_dir,
            output=output,
            confirmation="RESTORE",
            service_stopped=True,
        )
    assert (source / "app.db").read_bytes() == original


def test_clear_restore_gap_requires_confirmation_and_is_idempotent(tmp_path):
    source = _seed_source(tmp_path)
    gate = {
        "schema": GATE_SCHEMA,
        "status": "open",
        "backup_id": "backup-test",
        "restored_at": format_timestamp(NOW),
        "gap_expires_at": format_timestamp(NOW + timedelta(hours=1)),
        "cleared_at": None,
        "cleared_by": None,
        "clear_reason": None,
        "sessions_revoked": 1,
        "links_revoked": 1,
        "approvals_revoked": 0,
        "deliveries_revoked": 0,
        "assets_guarded": 0,
        "usage_floors": [],
        "slot_floors": [],
    }
    write_restore_gate(source, gate)
    with pytest.raises(BackupError) as excinfo:
        clear_restore_gap(
            source=source, confirmation="NOPE", reason="checked", service_stopped=True
        )
    assert excinfo.value.code == "CLEAR_CONFIRMATION_REQUIRED"
    cleared = clear_restore_gap(
        source=source,
        confirmation="CLEAR_RESTORE_GAP",
        reason="ledger checked",
        service_stopped=True,
        now=NOW,
    )
    assert cleared["ok"] is True
    assert cleared["status"] == "cleared"
    assert gate_is_open(source, now=NOW) is False
    again = clear_restore_gap(
        source=source,
        confirmation="CLEAR_RESTORE_GAP",
        reason="ledger checked",
        service_stopped=True,
        now=NOW,
    )
    assert again["already_cleared"] is True
    assert (source / GATE_NAME).is_file()


def test_restore_events_are_present_in_security_event_allowlist():
    assert "maintenance_restore_completed" in SECURITY_EVENT_TYPES
    assert "maintenance_restore_gap_cleared" in SECURITY_EVENT_TYPES
    assert {
        "safety_backup_id",
        "gap_expires_at",
        "cleared_at",
        "approvals_revoked",
        "deliveries_revoked",
        "assets_guarded",
    } <= SAFE_DETAIL_KEYS


def test_gate_file_is_not_created_by_backup_or_gate_helpers(tmp_path):
    source = _seed_source(tmp_path)
    assert not (source / GATE_NAME).exists()
    assert gate_is_open(source) is False
    assert usage_floor_count(source, account_id="account-1", now_ms=0) == 0
    assert slot_floor_count(source, scope="personal", scope_id="account-1") == 0
    output = tmp_path / "backups"
    backup = _backup(source, output)
    assert not (backup.backup_dir / "data" / GATE_NAME).exists()
    assert json.loads((backup.backup_dir / "manifest.json").read_text(encoding="utf-8"))["backup_id"]

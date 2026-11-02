from __future__ import annotations

# ruff: noqa: E501
import hashlib
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from app.maintenance.errors import BackupError
from app.maintenance.restore import restore_switch
from app.maintenance.restore_state import (
    GATE_NAME,
    gate_is_open,
    read_restore_gate,
    usage_floor_count,
)
from app.workflow.snapshot import build_execution_snapshot
from tests.test_t82_restore import NOW, _backup, _seed_source
from tests.test_v3_p1_t10_execution_snapshot import _execution_brief

PROJECT = "project-1"
ACCOUNT = "account-1"


def _connect(source: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(source / "app.db")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _insert_snapshot_approval(source: Path, *, schema_version: str = "1.1.0") -> str:
    snapshot = build_execution_snapshot(
        artifact_id="artifact-snapshot",
        artifact_digest="a" * 64,
        artifact_revision_no=1,
        project_revision=1,
        requirement_revision=1,
        reference_asset_id=None,
        reference_usage="none",
        referenced_material_digest="b" * 64,
        brief=_execution_brief(),
    )
    snapshot["snapshot_schema_version"] = schema_version
    checks = json.dumps({"confirmation": snapshot}, ensure_ascii=False)
    connection = _connect(source)
    try:
        with connection:
            connection.execute(
                "INSERT INTO approvals (id, project_id, artifact_id, approval_type, artifact_digest, "
                "approved_by, requirement_snapshot_digest, referenced_material_digest, prompt_version, "
                "rules_version, model_fingerprint, checks_json, created_at) VALUES (?, ?, NULL, "
                "'requirements_card', ?, 'user', ?, ?, 'v1', 'v3.rules.2', 'fake', ?, "
                "format_timestamp(NOW))".replace("format_timestamp(NOW)", "'2026-01-01T12:00:00.000Z'"),
                (
                    "approval-snapshot",
                    PROJECT,
                    snapshot["artifact_digest"],
                    snapshot["snapshot_digest"],
                    "b" * 64,
                    checks,
                ),
            )
    finally:
        connection.close()
    return checks


def _insert_running_facts(source: Path) -> None:
    stamp = "2026-01-01T12:01:00.000Z"
    connection = _connect(source)
    try:
        with connection:
            connection.execute(
                "INSERT INTO workflow_runs (id, project_id, sequence_no, stage, status, "
                "requirement_revision, created_at, updated_at, recovery_claim_id) VALUES "
                "('run-2', ?, 2, 'S07', 'active', 2, ?, ?, 'claim-2')",
                (PROJECT, stamp, stamp),
            )
            connection.execute(
                "INSERT INTO revision_budgets (run_id, artifact_kind, used, max_allowed) "
                "VALUES ('run-2', 'prompt_candidate', 2, 3)"
            )
            connection.execute(
                "INSERT INTO generation_jobs (id, project_id, run_id, generation_id, request_id, "
                "request_digest, prompt_artifact_id, confirmed_snapshot_digest, status, "
                "submit_attempted, created_at, updated_at, owner_account_id, operator_account_id) "
                "VALUES ('job-2', ?, 'run-2', NULL, 'request-2', ?, NULL, ?, 'unknown', 1, ?, ?, ?, ?)",
                (PROJECT, "d" * 64, "e" * 64, stamp, stamp, ACCOUNT, ACCOUNT),
            )
    finally:
        connection.close()


def _insert_snapshot_job_round_trip(source: Path) -> None:
    stamp = "2026-01-01T12:01:00.000Z"
    connection = _connect(source)
    try:
        with connection:
            connection.execute(
                "INSERT INTO revision_budgets (run_id, artifact_kind, used, max_allowed) "
                "VALUES ('run-1', 'prompt_candidate', 2, 3)"
            )
            connection.execute(
                "INSERT INTO generation_jobs (id, project_id, run_id, generation_id, request_id, "
                "request_digest, prompt_artifact_id, confirmed_snapshot_digest, status, "
                "submit_attempted, created_at, updated_at, owner_account_id, operator_account_id) "
                "VALUES ('job-unknown', ?, 'run-1', NULL, 'request-unknown', ?, NULL, ?, 'unknown', 1, ?, ?, ?, ?)",
                (PROJECT, "f" * 64, "a" * 64, stamp, stamp, ACCOUNT, ACCOUNT),
            )
    finally:
        connection.close()


def _source_digest(source: Path) -> str:
    return hashlib.sha256((source / "app.db").read_bytes()).hexdigest()


def test_restore_round_trip_preserves_snapshot_job_budget_and_usage_floor(tmp_path):
    source = _seed_source(tmp_path)
    stored_checks = _insert_snapshot_approval(source)
    _insert_snapshot_job_round_trip(source)
    output = tmp_path / "backups"
    backup = _backup(source, output)
    from tests.test_t82_restore import _mutate_after_snapshot

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
    assert result.usage_floor_accounts == 1
    assert gate_is_open(source, now=NOW + timedelta(minutes=2)) is True
    assert usage_floor_count(
        source,
        account_id=ACCOUNT,
        now_ms=int((NOW + timedelta(minutes=2)).timestamp() * 1000),
    ) == 1
    connection = _connect(source)
    try:
        job = connection.execute(
            "SELECT status FROM generation_jobs WHERE id = 'job-unknown'"
        ).fetchone()
        budget = connection.execute(
            "SELECT used FROM revision_budgets WHERE run_id = 'run-1' "
            "AND artifact_kind = 'prompt_candidate'"
        ).fetchone()
        approval = connection.execute(
            "SELECT checks_json FROM approvals WHERE id = 'approval-snapshot'"
        ).fetchone()
    finally:
        connection.close()
    assert job == ("unknown",)
    assert budget == (2,)
    assert approval == (stored_checks,)


def test_restore_refuses_to_erase_unresolved_job_claim_and_budget(tmp_path):
    source = _seed_source(tmp_path)
    output = tmp_path / "backups"
    backup = _backup(source, output)
    _insert_running_facts(source)
    before = _source_digest(source)
    with pytest.raises(BackupError) as excinfo:
        restore_switch(
            source=source,
            backup=backup.backup_dir,
            output=output,
            confirmation="RESTORE",
            service_stopped=True,
            now=NOW + timedelta(minutes=2),
        )
    assert excinfo.value.code == "RESTORE_LEDGER_GAP"
    assert excinfo.value.details["count"] >= 3
    kinds = {gap["kind"] for gap in excinfo.value.details["gaps"]}
    assert {"active_run", "recovery_claim", "unresolved_job", "revision_budget"} <= kinds
    assert _source_digest(source) == before
    assert not (source / GATE_NAME).exists()


def test_restore_refuses_missing_new_approval(tmp_path):
    source = _seed_source(tmp_path)
    output = tmp_path / "backups"
    backup = _backup(source, output)
    _insert_snapshot_approval(source)
    before = _source_digest(source)
    with pytest.raises(BackupError) as excinfo:
        restore_switch(
            source=source,
            backup=backup.backup_dir,
            output=output,
            confirmation="RESTORE",
            service_stopped=True,
            now=NOW + timedelta(minutes=2),
        )
    assert excinfo.value.code == "RESTORE_LEDGER_GAP"
    assert {gap["kind"] for gap in excinfo.value.details["gaps"]} == {"approval_missing"}
    assert _source_digest(source) == before


def test_restore_refuses_incompatible_snapshot_version(tmp_path):
    source = _seed_source(tmp_path)
    _insert_snapshot_approval(source, schema_version="9.9.9")
    output = tmp_path / "backups"
    backup = _backup(source, output)
    before = _source_digest(source)
    with pytest.raises(BackupError) as excinfo:
        restore_switch(
            source=source,
            backup=backup.backup_dir,
            output=output,
            confirmation="RESTORE",
            service_stopped=True,
            now=NOW + timedelta(minutes=2),
        )
    assert excinfo.value.code == "RESTORE_SNAPSHOT_INCOMPATIBLE"
    assert excinfo.value.details["reason"] == "SNAPSHOT_VERSION_UNSUPPORTED"
    assert _source_digest(source) == before
    assert not (source / GATE_NAME).exists()


def test_restore_gate_keeps_new_acceptance_blocked_until_explicit_clear(tmp_path):
    from app.maintenance.restore import clear_restore_gap

    source = _seed_source(tmp_path)
    output = tmp_path / "backups"
    backup = _backup(source, output)
    result = restore_switch(
        source=source,
        backup=backup.backup_dir,
        output=output,
        confirmation="RESTORE",
        service_stopped=True,
        now=NOW + timedelta(minutes=2),
    )
    gate = read_restore_gate(source)
    assert gate is not None and gate["status"] == "open"
    cleared = clear_restore_gap(
        source=source,
        confirmation="CLEAR_RESTORE_GAP",
        reason="ledger checked",
        service_stopped=True,
        now=NOW + timedelta(minutes=3),
    )
    assert cleared["status"] == "cleared"
    assert result.verified is True

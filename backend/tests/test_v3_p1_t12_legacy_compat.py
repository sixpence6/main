from __future__ import annotations

import copy
import json
import sqlite3
import types
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import OperationalError

from app.workflow.snapshot import (
    SnapshotCompatibilityError,
    build_execution_snapshot,
    execution_snapshot_from_checks,
    requirements_from_snapshot,
    snapshot_digest,
    validate_execution_snapshot,
)
from app.workflow.stages import s09, s12
from tests.test_v3_p1_t10_execution_snapshot import _execution_brief

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _snapshot(*, user_locks: dict[str, str] | None = None) -> dict[str, Any]:
    brief = _execution_brief()
    return build_execution_snapshot(
        artifact_id="artifact-1",
        artifact_digest="a" * 64,
        artifact_revision_no=1,
        project_revision=2,
        requirement_revision=2,
        reference_asset_id=None,
        reference_usage="none",
        referenced_material_digest="b" * 64,
        brief=brief,
        user_locks=user_locks,
    )


def test_legacy_approval_without_snapshot_keeps_old_semantics() -> None:
    assert execution_snapshot_from_checks({}) is None
    assert execution_snapshot_from_checks({"confirmation": {"confirmed": True}}) is None
    assert execution_snapshot_from_checks({"confirmation": []}) is None


def test_old_snapshot_version_is_read_only_and_not_rewritten() -> None:
    old = copy.deepcopy(_snapshot())
    old["snapshot_schema_version"] = "1.0.0"
    old.pop("user_locks", None)
    for constraint in old["constraints"]:
        constraint["strength"] = "hard"
    old["hard_requirements"] = [
        {
            "rule_id": item["rule_id"],
            "requirement": item["normalized_value"],
            "allowed_bindings": [item["binding"]],
        }
        for item in old["constraints"]
        if item.get("rule_id")
    ]
    old["snapshot_digest"] = snapshot_digest(old)
    digest_before = old["snapshot_digest"]
    checks = {"confirmation": old}

    loaded = execution_snapshot_from_checks(checks)

    assert loaded is not None
    assert loaded["snapshot_schema_version"] == "1.0.0"
    assert loaded["snapshot_digest"] == digest_before
    assert checks["confirmation"]["snapshot_digest"] == digest_before
    requirement_ids = {item["rule_id"] for item in requirements_from_snapshot(loaded)}
    assert "hard:style" in requirement_ids


def test_unknown_or_incomplete_snapshot_is_rejected_not_guessed() -> None:
    unsupported = copy.deepcopy(_snapshot())
    unsupported["snapshot_schema_version"] = "9.9.9"
    unsupported["snapshot_digest"] = snapshot_digest(unsupported)

    with pytest.raises(SnapshotCompatibilityError) as unsupported_error:
        validate_execution_snapshot(unsupported)
    assert unsupported_error.value.code == "SNAPSHOT_VERSION_UNSUPPORTED"

    incomplete = copy.deepcopy(_snapshot())
    incomplete.pop("user_locks", None)
    incomplete["snapshot_digest"] = snapshot_digest(incomplete)

    with pytest.raises(SnapshotCompatibilityError) as incomplete_error:
        validate_execution_snapshot(incomplete)
    assert incomplete_error.value.code == "SNAPSHOT_LOCKS_MISSING"


def test_normal_and_recovery_use_the_same_frozen_snapshot() -> None:
    brief = _execution_brief()
    locked = _snapshot(user_locks={"style_keywords": "用户明确要求极简风格"})

    normal_rules = s09._rule_specs(False, locked)
    normal_payload = s09._rule_payload(
        normal_rules, brief, "主体居中", "无", "1024x1024", locked
    )
    normal_composition = next(
        item for item in normal_payload if item["rule_id"] == "hard:composition"
    )
    assert normal_composition["applicability"] == "applicable"
    assert "极简" in normal_composition["requirement"]

    class _Asset:
        id = "asset-1"

    class _Candidate:
        asset = _Asset()
        data = b"image"
        mime_type = "image/png"

    async def fake_candidate(_ctx: Any) -> _Candidate:
        return _Candidate()

    async def fake_snapshot(_ctx: Any) -> tuple[dict[str, Any], str, dict[str, Any]]:
        return brief, locked["artifact_digest"], locked

    async def fake_reference(_ctx: Any, _snapshot: dict[str, Any]) -> tuple[None, str, None]:
        return None, "none", None

    ctx = types.SimpleNamespace(
        project=types.SimpleNamespace(image_size="1024x1024"),
        settings=types.SimpleNamespace(
            visual=lambda: types.SimpleNamespace(image_size="1024x1024")
        ),
    )
    original_candidate = s09._load_candidate
    original_snapshot = s09._confirmed_snapshot
    original_reference = s09._reference_context
    s09._load_candidate = fake_candidate
    s09._confirmed_snapshot = fake_snapshot
    s09._reference_context = fake_reference
    try:
        recovered = s12._s09_recovery_inputs(ctx)
        assert recovered is not None
        inputs = __import__("asyncio").run(recovered)
    finally:
        s09._load_candidate = original_candidate
        s09._confirmed_snapshot = original_snapshot
        s09._reference_context = original_reference

    assert inputs["execution_snapshot"] == locked
    recovered_composition = next(
        item for item in inputs["rule_payload"] if item["rule_id"] == "hard:composition"
    )
    assert recovered_composition == normal_composition


def _alembic_config(settings: Any) -> Config:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_dsn.replace("+aiosqlite", ""))
    return config


def _insert_legacy_approval(settings: Any) -> str:
    checks = {
        "confirmation": {
            "confirmed": True,
            "artifact_id": "legacy-artifact",
            "artifact_digest": "a" * 64,
            "requirement_revision": 1,
        }
    }
    now = "2026-01-01T00:00:00.000Z"
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "INSERT INTO projects ("
            "id,title,revision,brief_status,product_description,purpose,style,composition,"
            "text_content,prompt,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("project-1", "旧项目", 1, "confirmed", "旧商品", "", "", "", "", "", now, now),
        )
        connection.execute(
            "INSERT INTO approvals ("
            "id,project_id,artifact_id,approval_type,artifact_digest,approved_by,"
            "requirement_snapshot_digest,referenced_material_digest,prompt_version,"
            "rules_version,model_fingerprint,checks_json,created_at"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "approval-1",
                "project-1",
                None,
                "requirements_card",
                "a" * 64,
                "user",
                "c" * 64,
                "d" * 64,
                "v2",
                "v2.rules.1",
                "old-model",
                json.dumps(checks, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
    return json.dumps(checks, ensure_ascii=False, sort_keys=True)


def test_migration_from_supported_0010_preserves_legacy_approval(settings: Any) -> None:
    config = _alembic_config(settings)
    command.upgrade(config, "0010")
    checks_json = _insert_legacy_approval(settings)

    command.upgrade(config, "head")

    with sqlite3.connect(settings.database_path) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_runs)").fetchall()
        }
        stored = connection.execute(
            "SELECT checks_json FROM approvals WHERE id='approval-1'"
        ).fetchone()[0]
    assert version == "0013"
    assert {"recovery_claim_id", "recovery_claimed_at"} <= columns
    assert stored == checks_json
    assert execution_snapshot_from_checks(json.loads(stored)) is None


def test_failed_migration_leaves_0010_and_no_partial_column(settings: Any) -> None:
    config = _alembic_config(settings)
    command.upgrade(config, "0010")
    with sqlite3.connect(settings.database_path) as connection:
        # Force the batch rebuild to fail after it starts: SQLite must keep
        # the old table and the old alembic revision, never a half-added column.
        connection.execute(
            "CREATE TABLE _alembic_tmp_workflow_runs (id VARCHAR(32) PRIMARY KEY)"
        )

    with pytest.raises(OperationalError):
        command.upgrade(config, "head")

    with sqlite3.connect(settings.database_path) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workflow_runs)").fetchall()
        }
    assert version == "0010"
    assert "recovery_claim_id" not in columns

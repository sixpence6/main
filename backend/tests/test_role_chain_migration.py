"""Role policy migrations only touch temporary SQLite databases."""

from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.test_v3_p1_t12_legacy_compat import _alembic_config, _insert_legacy_approval


def _old_run(settings):
    _insert_legacy_approval(settings)
    with sqlite3.connect(settings.database_path) as db:
        db.execute(
            "INSERT INTO workflow_runs (id,project_id,sequence_no,stage,status,requirement_revision,created_at,updated_at) VALUES ('old-run','project-1',1,'S03','needs_input',1,'2026-11-01','2026-11-01')"
        )


def test_old_runs_remain_legacy_and_new_state_blocks_lossy_downgrade(settings):
    config = _alembic_config(settings)
    command.upgrade(config, "0016")
    _old_run(settings)
    command.upgrade(config, "head")
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute(
            "SELECT role_policy,requirements_state_json FROM workflow_runs"
        ).fetchall() == [("legacy", None)]
        db.execute(
            "UPDATE workflow_runs SET role_policy='consolidated', requirements_state_json=?",
            ('{"version":1,"data":{"purpose":"preserved"}}',),
        )
    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(config, "0016")
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0017",)
        assert db.execute(
            "SELECT role_policy,requirements_state_json FROM workflow_runs"
        ).fetchone() == ("consolidated", '{"version":1,"data":{"purpose":"preserved"}}')


def test_0017_version_write_failure_rolls_back_both_new_columns(settings):
    config = _alembic_config(settings)
    command.upgrade(config, "0016")
    _insert_legacy_approval(settings)

    def fail(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "UPDATE alembic_version SET version_num='0017'" in statement:
            raise RuntimeError("controlled role migration fault")

    event.listen(Engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="controlled role migration fault"):
            command.upgrade(config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", fail)
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0016",)
        columns = {row[1] for row in db.execute("PRAGMA table_info(workflow_runs)")}
        assert "role_policy" not in columns and "requirements_state_json" not in columns
    command.upgrade(config, "head")

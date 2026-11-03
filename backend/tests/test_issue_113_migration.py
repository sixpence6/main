"""Migration seam: temporary SQLite only, never a user database."""

from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.test_v3_p1_t12_legacy_compat import _alembic_config, _insert_legacy_approval


def test_0011_upgrade_preserves_history_without_inventing_execution_authorization(settings):
    config = _alembic_config(settings)
    command.upgrade(config, "0011")
    checks = _insert_legacy_approval(settings)
    with sqlite3.connect(settings.database_path) as db:
        before = db.execute("SELECT * FROM projects").fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0012",)
        assert db.execute("SELECT * FROM projects").fetchall() == before
        assert db.execute("SELECT checks_json FROM approvals").fetchall() == [(checks,)]
        assert db.execute("SELECT count(*) FROM prepared_prompts").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM generation_jobs").fetchone() == (0,)
    command.downgrade(config, "0011")
    command.upgrade(config, "head")


def test_failed_0012_version_write_rolls_back_the_new_structure(settings):
    config = _alembic_config(settings)
    command.upgrade(config, "0011")
    checks = _insert_legacy_approval(settings)

    def fail_version_write(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "UPDATE alembic_version SET version_num='0012'" in statement:
            raise RuntimeError("controlled version-write fault")

    event.listen(Engine, "before_cursor_execute", fail_version_write)
    try:
        with pytest.raises(RuntimeError, match="controlled version-write fault"):
            command.upgrade(config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", fail_version_write)
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0011",)
        assert db.execute("SELECT checks_json FROM approvals").fetchall() == [(checks,)]
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='prepared_prompts'").fetchall()
            == []
        )
    command.upgrade(config, "head")


@pytest.mark.asyncio
async def test_nonempty_prompt_facts_block_downgrade_and_remain_readable(full_settings):
    from tests.support.adapters import LLMStub, VisualStub
    from tests.support.t01_prompt import prepared, responder
    from tests.test_t65_generation_cycle import _drain, _environment

    async with _environment(full_settings, LLMStub(), VisualStub(), responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        path = f"/api/projects/{project_id}/workflow"
        before = (await client.get(path)).json()["prompt_preview"]
    config = _alembic_config(full_settings)
    with pytest.raises(RuntimeError, match="downgrade refused"):
        command.downgrade(config, "0011")
    async with _environment(full_settings, LLMStub(), VisualStub(), responder) as (app, client):
        after = (await client.get(path)).json()
        assert after["prompt_preview"] == before
        assert after["generation_job"] is None and after["usage"]["used"] == 1

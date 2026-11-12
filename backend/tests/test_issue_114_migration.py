"""Final-confirmation migration: exclusively temporary databases."""

from __future__ import annotations

import sqlite3

import pytest
from alembic import command
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tests.test_v3_p1_t12_legacy_compat import _alembic_config, _insert_legacy_approval


def test_0012_upgrade_preserves_history_and_creates_no_final_authorization(settings):
    config = _alembic_config(settings)
    command.upgrade(config, "0012")
    original = _insert_legacy_approval(settings)
    command.upgrade(config, "head")
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0017",)
        assert db.execute("SELECT checks_json FROM approvals").fetchall() == [(original,)]
        assert db.execute("SELECT count(*) FROM final_authorizations").fetchone() == (0,)
        assert db.execute("SELECT count(*) FROM generation_jobs").fetchone() == (0,)
    command.downgrade(config, "0012")
    command.upgrade(config, "head")


def test_final_migration_version_write_failure_rolls_back_new_schema(settings):
    config = _alembic_config(settings)
    command.upgrade(config, "0012")
    original = _insert_legacy_approval(settings)

    def fail(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "UPDATE alembic_version SET version_num='0013'" in statement:
            raise RuntimeError("controlled final migration fault")

    event.listen(Engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="controlled final migration fault"):
            command.upgrade(config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", fail)
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("0012",)
        assert (
            db.execute(
                "SELECT name FROM sqlite_master WHERE name='final_authorizations'"
            ).fetchall()
            == []
        )
        assert db.execute("SELECT checks_json FROM approvals").fetchall() == [(original,)]
    command.upgrade(config, "head")


@pytest.mark.asyncio
async def test_database_uniqueness_and_downgrade_preserve_real_final_authorization(full_settings):
    from app.adapters.base import ProviderOutcome
    from tests.support.adapters import LLMStub, VisualStub
    from tests.support.t01_prompt import prepared
    from tests.support.t02_prompt import final_click, responder
    from tests.test_t65_generation_cycle import _drain, _environment
    from tests.test_v2_t40_prompt_review import png_bytes

    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, LLMStub(), visual, responder) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        result = await final_click(client, {"project_id": project_id, "workflow": view})
        assert result.status_code == 200, result.text
        await _drain(app)
        with sqlite3.connect(full_settings.database_path) as db:
            columns = [x[1] for x in db.execute("PRAGMA table_info(final_authorizations)")]
            row = list(db.execute("SELECT * FROM final_authorizations").fetchone())
            row[columns.index("id")] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
            with pytest.raises(sqlite3.IntegrityError):
                db.execute(
                    "INSERT INTO final_authorizations VALUES(" + ",".join("?" for _ in row) + ")",
                    row,
                )
        with pytest.raises(RuntimeError, match="downgrade refused"):
            command.downgrade(_alembic_config(full_settings), "0012")
        assert (await client.get(f"/api/projects/{project_id}/workflow")).json()["generation_job"][
            "id"
        ] == result.json()["generation_job"]["id"]
        assert len(visual.submit_calls) == 1

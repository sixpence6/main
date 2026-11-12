from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.adapters.base import ProviderError, ProviderOutcome
from app.auth.models import AuditEvent
from app.db import read_session, write_transaction
from app.timeutil import format_timestamp, utc_now
from app.workflow.tables import Approval, Artifact, Delivery
from tests.support.adapters import LLMStub, VisualStub
from tests.support.t01_prompt import action, prepared
from tests.support.t01_prompt import responder as preparation_responder
from tests.support.t02_prompt import final_click, ready_project
from tests.support.t02_prompt import responder as prompt_responder
from tests.test_t65_generation_cycle import _drain, _environment
from tests.test_v2_t40_prompt_review import png_bytes


def _rows(path: str, sql: str, params: tuple = ()) -> list[dict]:
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql, params).fetchall()]


async def _view(client, project_id: str) -> dict:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def _post_action(client, project_id: str, name: str, payload: dict | None = None):
    view = await _view(client, project_id)
    body = {"expected_revision": view["project_revision"]}
    if payload is not None:
        body["payload"] = payload
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/{name}", json=body
    )


async def _audit_count(app) -> int:
    async with read_session(app.state.session_factory) as session:
        return int(
            await session.scalar(select(func.count()).select_from(AuditEvent)) or 0
        )


async def _review_candidate_if_needed(client, project_id: str) -> dict:
    view = await _view(client, project_id)
    if view["stage"] == "S09":
        response = await _post_action(client, project_id, "review_candidate")
        assert response.status_code == 200, response.text
        view = response.json()
    return view


async def _delivery_setup(app, client) -> dict:
    setup = await ready_project(app, client)
    clicked = await final_click(client, setup)
    assert clicked.status_code == 200, clicked.text
    await _drain(app)
    view = await _review_candidate_if_needed(client, setup["project_id"])
    assert view["stage"] == "S10", view
    await action(client, setup["project_id"], "prepare_delivery")
    await action(client, setup["project_id"], "approve_delivery")
    view = await _view(client, setup["project_id"])
    assert view["stage"] == "S10" and view["status"] == "needs_input", view
    assert view["issue"]["code"] == "DELIVERY_APPROVED", view
    deliveries = _rows(
        app.state.settings.database_path,
        "select id, asset_id, manifest_artifact_id, status, asset_digest from deliveries",
    )
    assert len(deliveries) == 1
    return {**setup, "delivery": deliveries[0], "workflow": view}


def _published_path(app, delivery_id: str) -> Path:
    rows = _rows(
        app.state.settings.database_path,
        "select a.relative_path from assets a join deliveries d on d.asset_id = a.id "
        "where d.id = ?",
        (delivery_id,),
    )
    assert len(rows) == 1
    path = app.state.settings.resolved_data_dir() / rows[0]["relative_path"]
    assert path.parent == app.state.settings.resolved_data_dir() / "published"
    return path


def _published_files(app, delivery_id: str) -> list[Path]:
    return list((app.state.settings.resolved_data_dir() / "published").glob(
        f"{delivery_id}-*.png"
    ))


def _asset_source(app, asset_id: str) -> Path:
    rows = _rows(
        app.state.settings.database_path,
        "select relative_path from assets where id = ?",
        (asset_id,),
    )
    assert rows
    return app.state.settings.resolved_data_dir() / rows[0]["relative_path"]


async def _revoke_approval(app, approval_type: str) -> None:
    async with write_transaction(app.state.session_factory) as session:
        row = await session.scalar(
            select(Approval)
            .where(
                Approval.approval_type == approval_type,
                Approval.revoked_at.is_(None),
            )
            .order_by(Approval.created_at.desc(), Approval.id.desc())
            .limit(1)
        )
        assert row is not None
        row.revoked_at = format_timestamp(utc_now())
        await session.flush()


async def _restore_approval(app, approval_type: str) -> None:
    async with write_transaction(app.state.session_factory) as session:
        row = await session.scalar(
            select(Approval)
            .where(Approval.approval_type == approval_type)
            .order_by(Approval.created_at.desc(), Approval.id.desc())
            .limit(1)
        )
        assert row is not None
        row.revoked_at = None
        await session.flush()


async def _revoke_delivery(app, delivery_id: str) -> None:
    async with write_transaction(app.state.session_factory) as session:
        row = await session.get(Delivery, delivery_id)
        assert row is not None
        row.status = "revoked"
        await session.flush()


async def _restore_delivery(app, delivery_id: str) -> None:
    async with write_transaction(app.state.session_factory) as session:
        row = await session.get(Delivery, delivery_id)
        assert row is not None
        row.status = "approved"
        await session.flush()


def _mutating_writer(app, monkeypatch, callback):
    import app.workflow.stages.s10 as s10

    original = s10.write_asset_file
    loop = asyncio.get_running_loop()

    def writer(settings, relative_path, data):
        original(settings, relative_path, data)
        future = asyncio.run_coroutine_threadsafe(callback(), loop)
        future.result(timeout=10)

    monkeypatch.setattr(s10, "write_asset_file", writer)
    return original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("image_revoked", "IMAGE_NOT_APPROVED"),
        ("manifest_revoked", "ASSET_UNAVAILABLE"),
        ("delivery_revoked", "ASSET_UNAVAILABLE"),
        ("asset_replaced", "DIGEST_MISMATCH"),
        ("manifest_tampered", "DIGEST_MISMATCH"),
    ],
)
async def test_publish_revalidates_every_fact_in_the_final_write_transaction(
    full_settings, monkeypatch, case, expected_code
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual, prompt_responder) as (app, client):
        setup = await _delivery_setup(app, client)
        delivery = setup["delivery"]
        asset_id = delivery["asset_id"]
        source = _asset_source(app, asset_id)
        original_bytes = source.read_bytes()
        assert not _published_files(app, delivery["id"])
        manifest_original = _rows(
            app.state.settings.database_path,
            "select payload_json from artifacts where id = ?",
            (delivery["manifest_artifact_id"],),
        )[0]["payload_json"]

        async def mutate():
            if case == "image_revoked":
                await _revoke_approval(app, "image")
            elif case == "manifest_revoked":
                await _revoke_approval(app, "delivery_manifest")
            elif case == "delivery_revoked":
                await _revoke_delivery(app, delivery["id"])
            elif case == "asset_replaced":
                source.write_bytes(png_bytes(1024, 1024, color=(200, 10, 10)))
            elif case == "manifest_tampered":
                async with write_transaction(app.state.session_factory) as session:
                    artifact = await session.get(
                        Artifact, delivery["manifest_artifact_id"]
                    )
                    assert artifact is not None
                    artifact.payload_json = json.dumps(
                        {
                            "asset_id": asset_id,
                            "requirement_revision": 1,
                            "filename_hint": "tampered.png",
                            "caption": "伪造的交付说明",
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    await session.flush()

        original_write = _mutating_writer(app, monkeypatch, mutate)
        blocked = await _post_action(client, setup["project_id"], "publish_delivery")
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == expected_code, blocked.text
        assert not _published_files(app, delivery["id"])
        delivery_status = _rows(
            app.state.settings.database_path,
            "select status from deliveries where id = ?",
            (delivery["id"],),
        )[0]["status"]
        assert delivery_status == (
            "revoked" if case == "delivery_revoked" else "approved"
        )
        assert _rows(
            app.state.settings.database_path,
            "select status from generation_jobs limit 1",
        )[0]["status"] == "approved"
        assert _rows(
            app.state.settings.database_path,
            "select count(*) as n from generation_slots where released_at is null",
        )[0]["n"] == 3

        if case == "image_revoked":
            await _restore_approval(app, "image")
        elif case == "manifest_revoked":
            await _restore_approval(app, "delivery_manifest")
        elif case == "delivery_revoked":
            await _restore_delivery(app, delivery["id"])
        elif case == "asset_replaced":
            source.write_bytes(original_bytes)
        elif case == "manifest_tampered":
            async with write_transaction(app.state.session_factory) as session:
                artifact = await session.get(Artifact, delivery["manifest_artifact_id"])
                assert artifact is not None
                artifact.payload_json = manifest_original
                await session.flush()
        import app.workflow.stages.s10 as s10

        monkeypatch.setattr(s10, "write_asset_file", original_write)
        retried = await _post_action(client, setup["project_id"], "publish_delivery")
        assert retried.status_code == 200, retried.text
        assert retried.json()["stage"] == "S10"
        assert retried.json()["status"] == "completed", retried.json()
        destination = _published_path(app, delivery["id"])
        assert destination.exists()
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == delivery["asset_digest"]
        assert _rows(
            app.state.settings.database_path,
            "select count(*) as n from deliveries where status = 'published'",
        )[0]["n"] == 1


@pytest.mark.asyncio
async def test_publish_filesystem_and_database_failures_leave_retryable_state(
    full_settings, monkeypatch
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual, prompt_responder) as (app, client):
        setup = await _delivery_setup(app, client)
        delivery = setup["delivery"]
        assert not _published_files(app, delivery["id"])
        import app.workflow.stages.s10 as s10

        original_write = s10.write_asset_file
        original_audit = s10.write_audit

        def fail_write(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(s10, "write_asset_file", fail_write)
        failed = await _post_action(client, setup["project_id"], "publish_delivery")
        assert failed.status_code == 409, failed.text
        assert failed.json()["error"]["code"] == "ASSET_UNAVAILABLE"
        assert not _published_files(app, delivery["id"])
        assert _rows(
            app.state.settings.database_path,
            "select status from deliveries where id = ?",
            (delivery["id"],),
        )[0]["status"] == "approved"

        monkeypatch.setattr(s10, "write_asset_file", original_write)

        def fail_audit(*args, **kwargs):
            raise RuntimeError("audit transaction failed")

        monkeypatch.setattr(s10, "write_audit", fail_audit)
        failed = await _post_action(client, setup["project_id"], "publish_delivery")
        assert failed.status_code == 409, failed.text
        assert failed.json()["error"]["code"] == "ASSET_UNAVAILABLE"
        assert not _published_files(app, delivery["id"])
        assert _rows(
            app.state.settings.database_path,
            "select status from deliveries where id = ?",
            (delivery["id"],),
        )[0]["status"] == "approved"
        assert _rows(
            app.state.settings.database_path,
            "select count(*) as n from generation_slots where released_at is null",
        )[0]["n"] == 3

        monkeypatch.setattr(s10, "write_audit", original_audit)
        retried = await _post_action(client, setup["project_id"], "publish_delivery")
        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "completed"
        destination = _published_path(app, delivery["id"])
        assert destination.exists()
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == delivery["asset_digest"]
        assert _rows(
            app.state.settings.database_path,
            "select count(*) as n from deliveries where status = 'published'",
        )[0]["n"] == 1


@pytest.mark.asyncio
async def test_reads_do_not_write_business_audit_and_publish_audits_once(
    full_settings, monkeypatch
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual, prompt_responder) as (app, client):
        setup = await ready_project(app, client)
        project_id = setup["project_id"]
        before = await _audit_count(app)
        for _ in range(3):
            assert (await client.get(f"/api/projects/{project_id}/workflow")).status_code == 200
            assert (await client.get(f"/api/projects/{project_id}")).status_code == 200
            assert (await client.get("/api/projects")).status_code == 200
        assert await _audit_count(app) == before

        clicked = await final_click(client, setup)
        assert clicked.status_code == 200, clicked.text
        await _drain(app)
        view = await _review_candidate_if_needed(client, project_id)
        assert view["stage"] == "S10", view
        await action(client, project_id, "prepare_delivery")
        await action(client, project_id, "approve_delivery")
        published = await _post_action(client, project_id, "publish_delivery")
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "completed"
        async with read_session(app.state.session_factory) as session:
            count = await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "delivery.published")
            )
        assert count == 1


@pytest.mark.asyncio
async def test_model_failure_logs_do_not_contain_the_private_prompt(
    full_settings, caplog
):
    secret = "PRIVATE-PROMPT-SENTINEL-9f1c"
    base = preparation_responder

    def failing(request):
        if request.payload["task"]["role"] == "generation_engineer":
            raise ProviderError(
                f"provider rejected private prompt: {secret}",
                evidence="uncertain",
                error_code="LLM_REJECTED",
            )
        return base(request)

    caplog.set_level(logging.INFO)
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(full_settings, llm, visual, failing) as (app, client):
        project_id = await prepared(client)
        await _drain(app)
        view = await _view(client, project_id)
        assert view["stage"] == "S07"
        assert secret not in caplog.text

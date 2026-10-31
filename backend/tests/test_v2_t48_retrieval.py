from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters.base import ProviderOutcome
from app.main import create_app
from app.services.generations import service_fingerprint
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import migrate
from tests.test_v2_t01_legacy import (
    OLD_BRIEF,
    OLD_PROMPT,
    _connect,
    _insert_project,
    _png_bytes,
    _upgrade,
)
from tests.test_v2_t49_legacy_review import Script
from tests.support.auth import authenticated_client


def _seed_failed_generation(
    settings,
    *,
    status="failed",
    locator='{"locator":"internal-locator-1","expires_at":"2099-01-01T00:00:00.000000Z"}',
    adapter_id="stub-visual",
):
    project_id = str(uuid.uuid4())
    generation_id = str(uuid.uuid4())
    _upgrade(settings, "0001")
    connection = _connect(settings)
    try:
        _insert_project(connection, project_id)
        connection.execute(
            """INSERT INTO generations (
                id, project_id, version_no, request_id, request_canonical, request_digest,
                confirmed_revision, status, stage, brief_snapshot_json, prompt, image_size,
                reference_asset_id, adapter_id, model, service_fingerprint, remote_task_id,
                result_locator_json, result_asset_id, error_code, error_message,
                submit_attempted, created_at, started_at, completed_at
            ) VALUES (?, ?, 1, ?, 'canonical', 'digest', 1, ?, 'done', ?, ?, '8x8',
                      NULL, ?, 'stub-model', ?, 'remote-1', ?, NULL, ?, '原结果获取失败', 1, ?, ?, ?)""",  # noqa: E501
            (
                generation_id,
                project_id,
                str(uuid.uuid4()),
                status,
                json.dumps(OLD_BRIEF, ensure_ascii=False),
                OLD_PROMPT,
                adapter_id,
                "fp",
                locator,
                "RESULT_FETCH_FAILED" if status == "failed" else None,
                "2026-01-01T00:00:00.000000Z",
                "2026-01-01T00:00:00.000000Z",
                "2026-01-01T00:00:00.000000Z",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    _upgrade(settings, "head")
    return project_id, generation_id


def _registry(settings, script=None):
    png = _png_bytes((10, 20, 30))
    visual = VisualStub(fetch_outcome=ProviderOutcome(state="succeeded", image_bytes=png))
    llm = LLMStub(role_responder=script or Script())
    registry = build_test_registry(llm=llm, visual=visual)
    return registry, visual, png


@asynccontextmanager
async def _client(settings, registry):
    migrate(settings)
    app = create_app(settings=settings, registry=registry)
    async with app.router.lifespan_context(app):
        async with authenticated_client(app) as client:
            yield client, app


async def _post(client, project_id, body):
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/retrieve_original_result", json=body
    )


@pytest.mark.asyncio
async def test_retrieve_failed_result_updates_same_generation_after_gates(settings, full_settings):
    project_id, generation_id = _seed_failed_generation(settings)
    # Match the seeded fingerprint to the configured test visual role.
    connection = _connect(settings)
    try:
        connection.execute(
            "UPDATE generations SET service_fingerprint = ? WHERE id = ?",
            (service_fingerprint(full_settings.visual()), generation_id),
        )
        connection.commit()
    finally:
        connection.close()
    registry, visual, png = _registry(full_settings)
    async with _client(full_settings, registry) as (client, app):
        response = await _post(
            client,
            project_id,
            {"expected_revision": 1, "payload": {"generation_id": generation_id}},
        )
        assert response.status_code == 200, response.text
        async with app.state.session_factory() as session:
            from sqlalchemy import select

            from app.models import Asset, Generation
            from app.workflow.tables import Delivery, GenerationJob

            generation = await session.get(Generation, generation_id)
            asset = await session.get(Asset, generation.result_asset_id)
            delivery = await session.scalar(select(Delivery).where(Delivery.asset_id == asset.id))
            jobs = (await session.scalars(select(GenerationJob))).all()
        assert generation.status == "succeeded"
        assert generation.result_asset_id == asset.id
        assert generation.version_no == 1
        assert asset.review_status == "approved"
        assert asset.sha256
        assert delivery is not None and delivery.status == "published"
        assert len(jobs) == 1 and jobs[0].submit_attempted == 0
        assert visual.fetch_calls == ["internal-locator-1"]
        assert visual.submit_calls == []
        assert "internal-locator-1" not in response.text
        assert "http" not in response.text.lower()
        preview = await client.get(f"/api/assets/{asset.id}")
        assert preview.status_code == 200 and preview.content == png
        download = await client.get(f"/api/assets/{asset.id}?download=true")
        assert download.status_code == 200 and download.content == png
        # A repeated click is idempotent: no new fetch, no new generation job.
        again = await _post(
            client,
            project_id,
            {"expected_revision": 1, "payload": {"generation_id": generation_id}},
        )
        assert again.status_code == 200
        assert visual.fetch_calls == ["internal-locator-1"]


@pytest.mark.asyncio
async def test_unknown_task_is_not_polled_or_fetched(settings, full_settings):
    project_id, generation_id = _seed_failed_generation(settings, status="unknown", locator=None)
    registry, visual, _ = _registry(full_settings)
    async with _client(full_settings, registry) as (client, app):
        response = await _post(
            client,
            project_id,
            {"expected_revision": 1, "payload": {"generation_id": generation_id}},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "RESULT_NOT_RETRIEVABLE"
        assert visual.fetch_calls == []


@pytest.mark.asyncio
async def test_g06_unknown_keeps_retrieved_asset_quarantined(settings, full_settings):
    project_id, generation_id = _seed_failed_generation(settings)
    connection = _connect(settings)
    try:
        connection.execute(
            "UPDATE generations SET service_fingerprint = ? WHERE id = ?",
            (service_fingerprint(full_settings.visual()), generation_id),
        )
        connection.commit()
    finally:
        connection.close()
    registry, visual, png = _registry(full_settings, Script(decisions={"image": "unknown"}))
    async with _client(full_settings, registry) as (client, app):
        response = await _post(
            client,
            project_id,
            {"expected_revision": 1, "payload": {"generation_id": generation_id}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "blocked"
        async with app.state.session_factory() as session:
            from sqlalchemy import select

            from app.models import Asset, Generation
            from app.workflow.tables import Delivery

            generation = await session.get(Generation, generation_id)
            assets = (await session.scalars(select(Asset))).all()
            delivery = await session.scalar(select(Delivery))
        assert generation.status == "failed"
        assert len(assets) == 1 and assets[0].review_status == "quarantined"
        assert delivery is None
        assert (await client.get(f"/api/assets/{assets[0].id}")).status_code == 409
        assert visual.fetch_calls == ["internal-locator-1"]
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_unsupported_adapter_and_fetch_failure_do_not_retry(settings, full_settings):
    project_id, generation_id = _seed_failed_generation(settings, adapter_id="missing-adapter")
    connection = _connect(settings)
    try:
        connection.execute(
            "UPDATE generations SET service_fingerprint = ? WHERE id = ?",
            (service_fingerprint(full_settings.visual()), generation_id),
        )
        connection.commit()
    finally:
        connection.close()
    registry, visual, _ = _registry(full_settings)
    async with _client(full_settings, registry) as (client, app):
        response = await _post(
            client,
            project_id,
            {"expected_revision": 1, "payload": {"generation_id": generation_id}},
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "ADAPTER_UNAVAILABLE"
        assert visual.fetch_calls == []


@pytest.mark.asyncio
async def test_fetch_failure_keeps_original_task_without_automatic_retry(settings, full_settings):
    project_id, generation_id = _seed_failed_generation(settings)
    connection = _connect(settings)
    try:
        connection.execute(
            "UPDATE generations SET service_fingerprint = ? WHERE id = ?",
            (service_fingerprint(full_settings.visual()), generation_id),
        )
        connection.commit()
    finally:
        connection.close()
    registry, visual, _ = _registry(full_settings)
    from app.adapters.base import ProviderError

    visual.fetch_error = ProviderError("获取失败", evidence="not_submitted")
    async with _client(full_settings, registry) as (client, app):
        response = await _post(
            client,
            project_id,
            {"expected_revision": 1, "payload": {"generation_id": generation_id}},
        )
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "RESULT_FETCH_FAILED"
        assert visual.fetch_calls == ["internal-locator-1"]
        assert visual.submit_calls == []
        async with app.state.session_factory() as session:
            from app.models import Generation

            generation = await session.get(Generation, generation_id)
        assert generation.status == "failed"
        assert generation.error_code == "RESULT_FETCH_FAILED"

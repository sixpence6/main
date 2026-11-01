from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.db import write_transaction
from app.models import Asset
from app.workflow.tables import Delivery, WorkflowRun
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import _drain, _environment, _setup_s06
from tests.test_v2_t40_prompt_review import png_bytes


@pytest.mark.asyncio
async def test_current_delivery_content_gate_and_feedback_preserve_history(full_settings):
    llm = LLMStub()
    gate = asyncio.Event()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )

    async with _environment(full_settings, llm, visual) as (app, client):
        setup = await _setup_s06(app, client)
        confirmed = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": setup["revision"],
                "payload": {
                    "artifact_id": setup["final_artifact_id"],
                    "artifact_digest": setup["final_digest"],
                    "requirement_revision": setup["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        llm.gate = None
        gate.set()
        await _drain(app)

        preparing = await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )
        assert preparing.status_code == 200, preparing.text
        assert preparing.json()["stage"] == "S10"
        assert preparing.json()["status"] == "needs_input"
        approved = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/approve_delivery",
            json={"expected_revision": preparing.json()["project_revision"]},
        )
        assert approved.status_code == 200, approved.text
        published = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/publish_delivery",
            json={"expected_revision": approved.json()["project_revision"]},
        )
        assert published.status_code == 200, published.text
        view = published.json()
        assert view["stage"] == "S10"
        assert view["status"] == "completed"
        assert view["delivery"]["status"] == "published"
        versions = view["versions"]
        assert len(versions) == 1
        version = versions[0]
        assert version["can_download"] is True
        assert version["asset_id"] == view["delivery"]["asset_id"]
        assert version["filename"] == view["delivery"]["filename"]
        assert version["brief"]["product_description"]
        asset_id = version["asset_id"]
        old_snapshot = json.dumps(version, ensure_ascii=False, sort_keys=True)

        preview = await client.get(f"/api/assets/{asset_id}")
        download = await client.get(f"/api/assets/{asset_id}?download=true")
        assert preview.status_code == 200, preview.text
        assert download.status_code == 200, download.text
        assert preview.content == download.content
        old_bytes = download.content
        assert old_bytes

        calls_after_publish = len(llm.role_calls_received)
        submits_after_publish = len(visual.submit_calls)
        history_refresh = await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )
        assert history_refresh.status_code == 200, history_refresh.text
        assert json.dumps(
            history_refresh.json()["versions"][0], ensure_ascii=False, sort_keys=True
        ) == old_snapshot
        assert len(llm.role_calls_received) == calls_after_publish
        assert len(visual.submit_calls) == submits_after_publish

        async with write_transaction(app.state.session_factory) as session:
            asset = await session.get(Asset, asset_id)
            assert asset is not None
            asset.review_status = "rejected"
            asset.review_note = "T75 content gate rehearsal"
            await session.flush()

        blocked = await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )
        assert blocked.status_code == 200, blocked.text
        blocked_view = blocked.json()
        assert blocked_view["status"] == "completed"
        assert blocked_view["delivery"]["status"] == "published"
        blocked_version = blocked_view["versions"][0]
        assert blocked_version["run_id"] == version["run_id"]
        assert blocked_version["can_download"] is False
        assert blocked_version["asset_id"] is None
        assert blocked_version["filename"] is None
        denied = await client.get(f"/api/assets/{asset_id}")
        assert denied.status_code == 409, denied.text
        assert denied.json()["error"]["code"] == "ASSET_REJECTED"

        async with write_transaction(app.state.session_factory) as session:
            asset = await session.get(Asset, asset_id)
            assert asset is not None
            asset.review_status = "approved"
            asset.review_note = None
            await session.flush()

        restored = await client.get(
            f"/api/projects/{setup['project_id']}/workflow"
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["versions"][0]["can_download"] is True

        feedback = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/request_new_version",
            json={
                "expected_revision": view["project_revision"],
                "text": "背景再简洁一些，保留商品颜色",
            },
        )
        assert feedback.status_code == 200, feedback.text
        next_view = feedback.json()
        assert next_view["stage"] == "S03"
        assert next_view["status"] == "active"
        assert next_view["project_revision"] == view["project_revision"] + 1
        assert next_view["run_id"] != version["run_id"]
        assert len(next_view["versions"]) == 1
        assert (
            json.dumps(next_view["versions"][0], ensure_ascii=False, sort_keys=True)
            == old_snapshot
        )
        still_there = await client.get(f"/api/assets/{asset_id}?download=true")
        assert still_there.status_code == 200, still_there.text
        assert still_there.content == old_bytes
        assert len(llm.role_calls_received) == calls_after_publish
        assert len(visual.submit_calls) == submits_after_publish

        async with app.state.session_factory() as session:
            old_run = await session.get(WorkflowRun, version["run_id"])
            new_run = await session.get(WorkflowRun, next_view["run_id"])
            delivery = await session.scalar(
                select(Delivery).where(Delivery.project_id == setup["project_id"])
            )
        assert old_run is not None and old_run.status == "completed"
        assert new_run is not None and new_run.sequence_no == 2
        assert delivery is not None and delivery.status == "published"

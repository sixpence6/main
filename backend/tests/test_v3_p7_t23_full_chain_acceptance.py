from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.db import read_session
from app.workflow.tables import Delivery, GenerationJob, RevisionBudget, WorkflowRun
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import (
    _drain,
    _environment,
    _quality_response,
    _responder,
    _rows,
    _setup_s06,
)
from tests.test_v2_t40_prompt_review import png_bytes


def _confirmed_payload(setup: dict, request_id: str) -> dict:
    return {
        "expected_revision": setup["revision"],
        "payload": {
            "artifact_id": setup["final_artifact_id"],
            "artifact_digest": setup["final_digest"],
            "requirement_revision": setup["revision"],
            "request_id": request_id,
        },
    }


def _new_stubs(gate: asyncio.Event) -> tuple[LLMStub, VisualStub]:
    llm = LLMStub()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    return llm, visual


@pytest.mark.asyncio
async def test_r3_full_chain_from_send_to_read_only_result_and_new_version(
    full_settings,
) -> None:
    gate = asyncio.Event()
    llm, visual = _new_stubs(gate)
    request_id = str(uuid.uuid4())

    async with _environment(full_settings, llm, visual) as (app, client):
        setup = await _setup_s06(app, client)
        project_id = setup["project_id"]

        before = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert before["task_state"]["progress"]["phase"] == "requirements"
        assert before["task_state"]["resource_state"]["current_state"] == "none"
        assert llm.role_calls_received == []
        assert visual.submit_calls == []
        confirm_actions = [
            item for item in before["available_actions"] if item["id"] == "confirm_requirements"
        ]
        assert len(confirm_actions) == 1
        assert confirm_actions[0]["requires_confirmation"] is True

        confirmed = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_confirmed_payload(setup, request_id),
        )
        assert confirmed.status_code == 200, confirmed.text
        view = confirmed.json()
        assert view["stage"] == "S07"
        assert view["status"] == "active"
        assert view["usage"]["used"] == 1
        assert view["intent"]["request_id"] == request_id
        assert view["intent"]["status"] == "accepted"

        intents, usages, slots = await _rows(app, project_id)
        assert len(intents) == 1
        assert len(usages) == 1
        assert len(slots) == 3
        assert {slot.scope for slot in slots} == {"personal", "instance", "project"}

        replay = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_confirmed_payload(setup, request_id),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["intent"]["id"] == view["intent"]["id"]
        intents, usages, slots = await _rows(app, project_id)
        assert len(intents) == 1 and len(usages) == 1 and len(slots) == 3
        assert len(llm.role_calls_received) == 1
        assert visual.submit_calls == []

        llm.gate = None
        gate.set()
        await _drain(app)

        intents, usages, slots = await _rows(app, project_id)
        assert len(intents) == 1 and len(usages) == 1 and len(slots) == 3
        assert len(visual.submit_calls) == 1
        roles = [request.payload["task"]["role"] for request in llm.role_calls_received]
        assert roles == [
            "generation_engineer",
            "output_reviewer",
            "quality_compliance",
            "output_reviewer",
            "delivery_manager",
            "output_reviewer",
        ]
        async with read_session(app.state.session_factory) as session:
            budgets = {
                budget.artifact_kind: (budget.used, budget.max_allowed)
                for budget in (
                    await session.scalars(
                        select(RevisionBudget).where(
                            RevisionBudget.run_id == view["run_id"]
                        )
                    )
                ).all()
            }
        assert budgets["prompt_plan"] == (1, 3)

        preparing_response = await client.get(f"/api/projects/{project_id}/workflow")
        assert preparing_response.status_code == 200, preparing_response.text
        preparing = preparing_response.json()
        assert preparing["stage"] == "S10"
        assert preparing["status"] == "needs_input"
        assert preparing["task_state"]["resource_state"]["current_state"] == "pending"
        assert preparing["delivery"] is None
        assert preparing["versions"] == []
        assert all(
            action["id"] != "publish_delivery"
            for action in preparing["available_actions"]
        )
        async with read_session(app.state.session_factory) as session:
            job = await session.scalar(
                select(GenerationJob).where(GenerationJob.project_id == project_id)
            )
        assert job is not None and job.result_asset_id
        unavailable_preview = await client.get(f"/api/assets/{job.result_asset_id}")
        assert unavailable_preview.status_code == 409, unavailable_preview.text
        assert unavailable_preview.json()["error"]["code"] in {
            "ASSET_UNAVAILABLE",
            "LEGACY_UNREVIEWED",
        }

        approved = await client.post(
            f"/api/projects/{project_id}/workflow/actions/approve_delivery",
            json={"expected_revision": preparing["project_revision"]},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "needs_input"

        calls_before_publish = len(llm.role_calls_received)
        submits_before_publish = len(visual.submit_calls)
        published = await client.post(
            f"/api/projects/{project_id}/workflow/actions/publish_delivery",
            json={"expected_revision": approved.json()["project_revision"]},
        )
        assert published.status_code == 200, published.text
        published_view = published.json()
        assert published_view["stage"] == "S10"
        assert published_view["status"] == "completed"
        assert published_view["delivery"]["status"] == "published"
        assert len(published_view["versions"]) == 1

        version = published_view["versions"][0]
        assert version["can_download"] is True
        assert version["asset_id"] == published_view["delivery"]["asset_id"]
        assert version["width"] == 1024 and version["height"] == 1024
        assert version["brief"]["product_description"]
        task = published_view["task_state"]
        assert task["progress"]["phase"] == "review"
        assert task["progress"]["stage"] == "S10"
        assert task["progress"]["terminal"] is True
        assert task["resource_state"]["current_state"] == "published"
        assert task["resource_state"]["current_version"]["run_id"] == version["run_id"]
        assert task["resource_state"]["history"] == []
        assert task["binding"]["delivery_id"] == published_view["delivery"]["id"]
        assert task["binding"]["current"] is True

        preview = await client.get(f"/api/assets/{version['asset_id']}")
        download = await client.get(f"/api/assets/{version['asset_id']}?download=true")
        assert preview.status_code == 200, preview.text
        assert download.status_code == 200, download.text
        assert preview.content == download.content
        published_bytes = download.content

        refresh = await client.get(f"/api/projects/{project_id}/workflow")
        assert refresh.status_code == 200, refresh.text
        refreshed = refresh.json()
        assert refreshed["versions"][0] == version
        assert refreshed["delivery"]["status"] == "published"
        assert len(llm.role_calls_received) == calls_before_publish
        assert len(visual.submit_calls) == submits_before_publish
        old_version_snapshot = json.dumps(refreshed["versions"][0], sort_keys=True)

        feedback = await client.post(
            f"/api/projects/{project_id}/workflow/actions/request_new_version",
            json={
                "expected_revision": published_view["project_revision"],
                "text": "Make the background cleaner and keep the product colors.",
            },
        )
        assert feedback.status_code == 200, feedback.text
        next_view = feedback.json()
        assert next_view["stage"] == "S03"
        assert next_view["status"] == "active"
        assert next_view["project_revision"] == published_view["project_revision"] + 1
        assert next_view["run_id"] != version["run_id"]
        assert next_view["task_state"]["progress"]["phase"] == "requirements"
        assert next_view["task_state"]["resource_state"]["current_state"] == "history_only"
        assert len(next_view["versions"]) == 1
        assert json.dumps(next_view["versions"][0], sort_keys=True) == old_version_snapshot
        assert len(llm.role_calls_received) == calls_before_publish
        assert len(visual.submit_calls) == submits_before_publish
        still_there = await client.get(f"/api/assets/{version['asset_id']}?download=true")
        assert still_there.status_code == 200, still_there.text
        assert still_there.content == published_bytes

        async with read_session(app.state.session_factory) as session:
            old_run = await session.get(WorkflowRun, version["run_id"])
            new_run = await session.get(WorkflowRun, next_view["run_id"])
            delivery = await session.scalar(
                select(Delivery).where(Delivery.project_id == project_id)
            )
            job = await session.scalar(
                select(GenerationJob).where(GenerationJob.project_id == project_id)
            )
        assert old_run is not None and old_run.status == "completed"
        assert new_run is not None and new_run.sequence_no == 2
        assert delivery is not None and delivery.status == "published"
        assert job is not None and job.status == "published"


@pytest.mark.asyncio
async def test_r3_hard_quality_failure_keeps_no_downloadable_result(full_settings) -> None:
    gate = asyncio.Event()
    llm, visual = _new_stubs(gate)

    def rejecting_responder(request):
        if request.payload["task"]["role"] == "quality_compliance":
            return _quality_response(request, decision="fail")
        return _responder(request)

    async with _environment(
        full_settings, llm, visual, responder=rejecting_responder
    ) as (app, client):
        setup = await _setup_s06(app, client)
        project_id = setup["project_id"]
        confirmed = await client.post(
            f"/api/projects/{project_id}/workflow/actions/confirm_requirements",
            json=_confirmed_payload(setup, str(uuid.uuid4())),
        )
        assert confirmed.status_code == 200, confirmed.text
        gate.set()
        await _drain(app)

        intents, usages, slots = await _rows(app, project_id)
        assert len(intents) == 1 and intents[0].status == "failed"
        assert len(usages) == 1
        assert len(slots) == 3
        assert all(slot.released_at is not None for slot in slots)
        assert all(slot.release_reason == "generation_terminal" for slot in slots)
        assert len(visual.submit_calls) == 1
        async with read_session(app.state.session_factory) as session:
            budgets = list(
                (
                    await session.scalars(
                        select(RevisionBudget).where(
                            RevisionBudget.run_id == intents[0].run_id
                        )
                    )
                ).all()
            )
        budget_map = {
            budget.artifact_kind: (budget.used, budget.max_allowed) for budget in budgets
        }
        assert budget_map["prompt_plan"] == (1, 3)

        view_response = await client.get(f"/api/projects/{project_id}/workflow")
        assert view_response.status_code == 200, view_response.text
        view = view_response.json()
        assert view["stage"] == "S09"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "IMAGE_NOT_APPROVED"
        assert view["task_state"]["progress"]["phase"] == "review"
        assert view["task_state"]["resource_state"]["current_state"] == "failed"
        assert view["versions"] == []
        assert view.get("delivery") is None
        assert all(
            action["id"] != "publish_delivery" for action in view["available_actions"]
        )
        assert all(
            action["id"] != "download_delivery" for action in view["available_actions"]
        )
        roles = [request.payload["task"]["role"] for request in llm.role_calls_received]
        assert roles == [
            "generation_engineer",
            "output_reviewer",
            "quality_compliance",
            "output_reviewer",
        ]
        assert len(visual.submit_calls) == 1

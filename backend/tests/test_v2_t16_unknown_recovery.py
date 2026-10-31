"""T16 / AC-23, AC-34, AC-38: unknown receipt recovery and risk acknowledgement."""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.adapters.base import ProviderError, ProviderOutcome
from app.config import Settings
from app.db import read_session, write_transaction
from app.main import create_app
from app.models import Generation, Project
from app.workflow import state
from app.workflow.tables import DiagnosticEvent, GenerationJob
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import migrate
from tests.support.auth import authenticated_client

IMAGE_SIZE = "1024x1024"


def png_bytes() -> bytes:
    output = io.BytesIO()
    width, height = (int(part) for part in IMAGE_SIZE.split("x", 1))
    Image.new("RGB", (width, height), (200, 200, 200)).save(output, format="PNG")
    return output.getvalue()


def brief_payload() -> dict[str, Any]:
    return {
        "product_description": "A ceramic mug",
        "purpose": "E-commerce hero image",
        "facts": [],
        "creative_direction": "Clean studio light",
        "style_keywords": ["minimal"],
        "composition": "Centered with whitespace",
        "lighting": "Soft side light",
        "texts": [],
        "must_include": [],
        "must_preserve": [],
        "forbidden": [],
        "size": IMAGE_SIZE,
        "reference_asset_id": None,
        "reference_usage": "none",
        "assumptions": [],
        "unresolved": [],
    }


def prompt_payload(brief: dict[str, Any]) -> dict[str, Any]:
    positive = " | ".join(
        [
            brief["product_description"],
            brief["purpose"],
            brief["creative_direction"],
            "、".join(brief["style_keywords"]),
            brief["composition"],
            brief["lighting"],
        ]
    )
    coverage = [
        {
            "rule_id": "hard:subject",
            "binding": "positive_prompt",
            "value": brief["product_description"],
        },
        {"rule_id": "hard:purpose", "binding": "positive_prompt", "value": brief["purpose"]},
        {
            "rule_id": "hard:creative",
            "binding": "positive_prompt",
            "value": brief["creative_direction"],
        },
        {
            "rule_id": "hard:style",
            "binding": "positive_prompt",
            "value": "、".join(brief["style_keywords"]),
        },
        {
            "rule_id": "hard:composition",
            "binding": "positive_prompt",
            "value": brief["composition"],
        },
        {"rule_id": "hard:lighting", "binding": "positive_prompt", "value": brief["lighting"]},
        {"rule_id": "hard:size", "binding": "size", "value": IMAGE_SIZE},
        {"rule_id": "hard:count", "binding": "count", "value": "1"},
    ]
    return {
        "positive_prompt": positive,
        "negative_prompt": "",
        "size": IMAGE_SIZE,
        "count": 1,
        "reference_asset_id": None,
        "requirement_coverage": coverage,
    }


@dataclass
class SequenceVisual(VisualStub):
    """Visual adapter that scripts the provider result for each submit call."""

    results: list[BaseException | ProviderOutcome] = field(default_factory=list)

    async def submit(self, request):  # type: ignore[no-untyped-def]
        self.submit_calls.append(request)
        self.active_count += 1
        self.max_active_count = max(self.max_active_count, self.active_count)
        try:
            if self.gate is not None:
                await self.gate.wait()
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            if not self.results:
                raise AssertionError("no scripted submit result")
            result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
            if isinstance(result, BaseException):
                raise result
            return result
        finally:
            self.active_count -= 1


@asynccontextmanager
async def environment(settings: Settings, visual: VisualStub):
    migrate(settings)
    application = create_app(
        settings=settings,
        registry=build_test_registry(llm=LLMStub(), visual=visual),
    )
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client


async def seed_ready_project(app, client: AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]
    revision = response.json()["revision"]
    brief = brief_payload()
    prompt = prompt_payload(brief)
    async with write_transaction(app.state.session_factory) as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.product_description = brief["product_description"]
        project.purpose = brief["purpose"]
        project.style = "、".join(brief["style_keywords"])
        project.composition = brief["composition"]
        project.text_content = ""
        project.image_size = IMAGE_SIZE
        project.prompt = prompt["positive_prompt"]
        project.prompt_revision = revision
        project.confirmed_revision = revision
        project.brief_status = "confirmed"
        await session.flush()

    ctx = await state.load_context(app, project_id)
    await state.create_run(ctx, stage="S08")
    ctx = await state.reload_context(ctx)

    final = await state.start_artifact(ctx, "final_brief", brief)
    await state.add_review(
        ctx,
        final,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={
            "checks": [
                {
                    "rule_id": "G03_CONFIRMATION",
                    "outcome": "pass",
                    "evidence_ids": [final.id],
                    "note": "approved",
                }
            ]
        },
    )
    final = await state.latest_artifact(ctx, "final_brief", status="approved")
    assert final is not None
    await state.add_approval(
        ctx,
        approval_type="requirements_card",
        artifact=final,
        approved_by="user",
        checks={"confirmed": True},
    )

    prompt_artifact = await state.start_artifact(ctx, "prompt_plan", prompt)
    await state.add_review(
        ctx,
        prompt_artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={
            "checks": [
                {
                    "rule_id": "G03_CONFIRMATION",
                    "outcome": "pass",
                    "evidence_ids": [prompt_artifact.id],
                    "note": "approved",
                }
            ]
        },
    )
    prompt_artifact = await state.latest_artifact(ctx, "prompt_plan", status="approved")
    assert prompt_artifact is not None
    await state.add_approval(
        ctx,
        approval_type="generation_prompt",
        artifact=prompt_artifact,
        approved_by="output_reviewer",
        checks={"checks": []},
        requirement_digest=final.digest,
    )
    return {
        "project_id": project_id,
        "revision": revision,
        "brief": brief,
        "prompt": prompt,
        "final_digest": final.digest,
        "prompt_digest": prompt_artifact.digest,
    }


async def post_action(
    client: AsyncClient,
    setup: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> Any:
    body: dict[str, Any] = {"expected_revision": setup["revision"]}
    if payload is not None:
        body["payload"] = payload
    return await client.post(
        f"/api/projects/{setup['project_id']}/workflow/actions/submit_generation",
        json=body,
    )


async def current_revision(app, project_id: str) -> int:
    async with read_session(app.state.session_factory) as session:
        project = await session.get(Project, project_id)
        assert project is not None
        return project.revision


async def drain(app) -> None:
    for _ in range(400):
        tasks = list(app.state.generation_tasks)
        if not tasks:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("generation tasks did not finish")


async def latest_generation(app, project_id: str) -> Generation:
    async with read_session(app.state.session_factory) as session:
        generation = await session.scalar(
            select(Generation)
            .where(Generation.project_id == project_id)
            .order_by(Generation.version_no.desc())
            .limit(1)
        )
    assert generation is not None
    return generation


async def latest_job(app, project_id: str) -> GenerationJob:
    async with read_session(app.state.session_factory) as session:
        job = await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.project_id == project_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
    assert job is not None
    return job


async def generation_count(app, project_id: str) -> int:
    async with read_session(app.state.session_factory) as session:
        rows = (
            await session.scalars(select(Generation).where(Generation.project_id == project_id))
        ).all()
    return len(rows)


def success_outcome() -> ProviderOutcome:
    return ProviderOutcome(
        state="succeeded",
        remote_task_id="remote-new-task",
        image_bytes=png_bytes(),
        image_count=1,
    )


async def make_unknown(
    app, client: AsyncClient, visual: SequenceVisual, setup: dict[str, Any]
) -> tuple[Generation, GenerationJob]:
    response = await post_action(client, setup, {})
    assert response.status_code == 200, response.text
    await drain(app)
    generation = await latest_generation(app, setup["project_id"])
    job = await latest_job(app, setup["project_id"])
    assert generation.status == "unknown"
    assert job.status == "unknown"
    assert generation.result_asset_id is None
    assert len(visual.submit_calls) == 1
    return generation, job


@pytest.mark.asyncio
async def test_uncertain_submit_is_unconfirmed_and_preserves_original_snapshot(full_settings):
    """AC-34: an uncertain provider result is unconfirmed, not failed or cancelled."""
    visual = SequenceVisual(
        results=[ProviderError("provider timeout", evidence="uncertain"), success_outcome()]
    )
    async with environment(full_settings, visual) as (app, client):
        setup = await seed_ready_project(app, client)
        original, job = await make_unknown(app, client, visual, setup)

        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["status"] == "blocked", view
        assert view["issue"]["code"] == "GENERATION_UNKNOWN", view
        assert view["generation_job"]["id"] == job.id
        assert view["generation_job"]["status"] == "unknown"
        assert view["generation_job"]["submit_attempted"] == 1
        assert original.brief_snapshot_json
        assert original.request_canonical
        assert job.generation_id == original.id


@pytest.mark.asyncio
async def test_original_submission_replay_returns_same_task_without_provider_call(full_settings):
    """AC-23: replaying the original submission returns the same task without a provider call."""
    visual = SequenceVisual(
        results=[ProviderError("connection lost", evidence="uncertain"), success_outcome()]
    )
    async with environment(full_settings, visual) as (app, client):
        setup = await seed_ready_project(app, client)
        original, job = await make_unknown(app, client, visual, setup)

        replay = await post_action(client, setup, {})
        assert replay.status_code == 200, replay.text
        view = replay.json()
        assert view["generation_job"]["id"] == job.id
        assert view["generation_job"]["request_id"] == job.request_id
        assert await generation_count(app, setup["project_id"]) == 1
        assert len(visual.submit_calls) == 1
        assert original.id == (await latest_generation(app, setup["project_id"])).id


@pytest.mark.asyncio
async def test_no_risk_confirmation_cannot_create_a_new_generation(full_settings):
    """AC-23/AC-34: no risk acknowledgement means no new generation task."""
    visual = SequenceVisual(
        results=[ProviderError("timeout", evidence="uncertain"), success_outcome()]
    )
    async with environment(full_settings, visual) as (app, client):
        setup = await seed_ready_project(app, client)
        original, job = await make_unknown(app, client, visual, setup)

        rejected = await post_action(client, setup, {"acknowledge_unknown": False})
        assert rejected.status_code in (200, 409), rejected.text
        if rejected.status_code == 409:
            assert rejected.json()["error"]["code"] == "UNKNOWN_ACK_REQUIRED"
            assert rejected.json()["error"]["message"]
        assert await generation_count(app, setup["project_id"]) == 1
        assert len(visual.submit_calls) == 1
        assert (await latest_job(app, setup["project_id"])).id == job.id
        assert original.status == "unknown"


@pytest.mark.asyncio
async def test_explicit_risk_confirmation_creates_exactly_one_new_task(full_settings):
    """AC-34: explicit risk acknowledgement creates one new task and preserves the old one."""
    visual = SequenceVisual(
        results=[ProviderError("uncertain relay", evidence="uncertain"), success_outcome()]
    )
    async with environment(full_settings, visual) as (app, client):
        setup = await seed_ready_project(app, client)
        original, old_job = await make_unknown(app, client, visual, setup)

        response = await post_action(
            client,
            setup,
            {"acknowledge_unknown": True, "previous_generation_id": original.id},
        )
        assert response.status_code == 200, response.text
        await drain(app)
        assert await generation_count(app, setup["project_id"]) == 2
        assert len(visual.submit_calls) == 2
        new = await latest_generation(app, setup["project_id"])
        new_job = await latest_job(app, setup["project_id"])
        assert new.id != original.id
        assert new.version_no == original.version_no + 1
        assert new.request_id != original.request_id
        assert new_job.id != old_job.id
        assert new_job.generation_id == new.id
        assert new_job.submit_attempted == 1
        assert new.status == "succeeded", (
            new.status,
            new.error_code,
            new.error_message,
            new_job.error_code,
            new_job.error_message,
        )
        assert new.result_asset_id

        # The original unknown task and its snapshot must remain untouched.
        async with read_session(app.state.session_factory) as session:
            stored_original = await session.get(Generation, original.id)
        assert stored_original is not None
        assert stored_original.status == "unknown"
        assert stored_original.request_canonical == original.request_canonical

        canonical = json.loads(new.request_canonical)
        assert canonical["acknowledge_unknown"] is True
        assert canonical["previous_generation_id"] == original.id
        assert canonical["previous_request_id"] == original.request_id

        async with read_session(app.state.session_factory) as session:
            events = (
                await session.scalars(
                    select(DiagnosticEvent).where(DiagnosticEvent.project_id == setup["project_id"])
                )
            ).all()
        ack_events = [event for event in events if event.event == "generation_risk_acknowledged"]
        assert ack_events
        detail = json.loads(ack_events[-1].detail_json)
        assert detail["previous_generation_id"] == original.id
        assert detail["new_generation_id"] == new.id


@pytest.mark.asyncio
async def test_repeating_risk_confirmation_returns_same_new_task(full_settings):
    """AC-23: repeating the risk acknowledgement returns the same new task."""
    visual = SequenceVisual(
        results=[ProviderError("lost receipt", evidence="uncertain"), success_outcome()]
    )
    async with environment(full_settings, visual) as (app, client):
        setup = await seed_ready_project(app, client)
        original, _old_job = await make_unknown(app, client, visual, setup)
        payload = {"acknowledge_unknown": True, "previous_generation_id": original.id}

        first = await post_action(client, setup, payload)
        assert first.status_code == 200, first.text
        first_job_id = first.json()["generation_job"]["id"]
        second = await post_action(client, setup, payload)
        assert second.status_code == 200, second.text
        assert second.json()["generation_job"]["id"] == first_job_id
        await drain(app)
        assert await generation_count(app, setup["project_id"]) == 2
        assert len(visual.submit_calls) == 2


@pytest.mark.asyncio
async def test_concurrent_risk_confirmation_does_not_duplicate_task(full_settings):
    """AC-34: simultaneous confirmations cannot create a second paid task."""
    visual = SequenceVisual(
        results=[ProviderError("lost receipt", evidence="uncertain"), success_outcome()]
    )
    async with environment(full_settings, visual) as (app, client):
        setup = await seed_ready_project(app, client)
        original, _old_job = await make_unknown(app, client, visual, setup)
        payload = {"acknowledge_unknown": True, "previous_generation_id": original.id}

        responses = await asyncio.gather(
            post_action(client, setup, payload),
            post_action(client, setup, payload),
        )
        busy = [response for response in responses if response.status_code == 409]
        accepted = [response for response in responses if response.status_code == 200]
        assert accepted, [response.text for response in responses]
        for response in busy:
            assert response.json()["error"]["code"] == "PROJECT_BUSY"
        # A later retry of the busy request is the real risk-confirmation attempt;
        # it must return the one already-created task, not create another one.
        for _ in busy:
            retry = await post_action(client, setup, payload)
            assert retry.status_code == 200, retry.text
            accepted.append(retry)
        await drain(app)
        assert await generation_count(app, setup["project_id"]) == 2
        assert len(visual.submit_calls) == 2
        job_ids = {response.json()["generation_job"]["id"] for response in accepted}
        assert len(job_ids) == 1


@pytest.mark.asyncio
async def test_new_generation_requires_current_requirement_confirmation(full_settings):
    """AC-34: a changed requirement must be reconfirmed before a new paid task is submitted."""
    visual = SequenceVisual(
        results=[ProviderError("timeout", evidence="uncertain"), success_outcome()]
    )
    async with environment(full_settings, visual) as (app, client):
        setup = await seed_ready_project(app, client)
        original, _job = await make_unknown(app, client, visual, setup)

        async with write_transaction(app.state.session_factory) as session:
            project = await session.get(Project, setup["project_id"])
            assert project is not None
            project.revision += 1
            project.brief_status = "draft"
            await session.flush()
        next_revision = await current_revision(app, setup["project_id"])

        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/submit_generation",
            json={
                "expected_revision": next_revision,
                "payload": {
                    "acknowledge_unknown": True,
                    "previous_generation_id": original.id,
                },
            },
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] in {
            "CONFIRM_REQUIRED",
            "REVISION_CONFLICT",
            "PROMPT_NOT_APPROVED",
            "PROMPT_STALE",
        }
        assert await generation_count(app, setup["project_id"]) == 1
        assert len(visual.submit_calls) == 1
        stored = await latest_generation(app, setup["project_id"])
        assert stored.id == original.id
        assert stored.status == "unknown"
        assert stored.brief_snapshot_json == original.brief_snapshot_json


@pytest.mark.asyncio
async def test_unknown_task_survives_restart_and_is_not_a_download_or_retrieval_authorization(
    full_settings,
):
    """AC-34/AC-38: restart preserves unknown state; no retrieval/download path."""
    visual = SequenceVisual(
        results=[ProviderError("network down", evidence="uncertain"), success_outcome()]
    )
    async with environment(full_settings, visual) as (app, client):
        setup = await seed_ready_project(app, client)
        original, job = await make_unknown(app, client, visual, setup)
        original_id = original.id
        job_id = job.id

    restarted_visual = SequenceVisual(results=[success_outcome()])
    async with environment(full_settings, restarted_visual) as (app, client):
        detail = (await client.get(f"/api/projects/{setup['project_id']}")).json()
        assert detail["generations"][0]["id"] == original_id
        assert detail["generations"][0]["status"] == "unknown"
        assert detail["generations"][0]["result_asset"] is None
        assert detail["generations"][0]["result_asset_id"] is None

        workflow = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert workflow["generation_job"]["id"] == job_id
        assert workflow["generation_job"]["status"] == "unknown"
        assert restarted_visual.submit_calls == []

        generation = (await client.get(f"/api/generations/{original_id}")).json()
        assert generation["status"] == "unknown"
        assert generation["can_retrieve_result"] is False
        retrieve = await client.post(f"/api/generations/{original_id}/retrieve-result", json={})
        assert retrieve.status_code == 409, retrieve.text
        assert retrieve.json()["error"]["code"] in {
            "RESULT_NOT_RETRIEVABLE",
            "LEGACY_FLOW_DISABLED",
        }
        assert restarted_visual.submit_calls == []
        assert restarted_visual.poll_calls == []

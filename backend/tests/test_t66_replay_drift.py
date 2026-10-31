from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.workflow.stages.s03 import REVIEW_RULE_IDS
from app.workflow.tables import Approval, Artifact, WorkflowRun
from tests.support.adapters import LLMStub, VisualStub
from tests.support.auth import authenticated_client, login
from tests.test_t56_account_links import USER_PASSWORD, _client, _setup_user
from tests.test_t65_generation_cycle import (
    _drain,
    _environment,
    _responder,
    _rows,
    _setup_s06,
)
from tests.test_v2_t40_prompt_review import (
    base_brief,
    candidate_data,
    engineer_response,
    png_bytes,
    review_response,
)


def _confirmation_payload(setup: dict, request_id: str, **overrides) -> dict:
    payload = {
        "artifact_id": setup["final_artifact_id"],
        "artifact_digest": setup["final_digest"],
        "requirement_revision": setup["revision"],
        "request_id": request_id,
    }
    payload.update(overrides)
    return {"expected_revision": setup["revision"], "payload": payload}


def _generation_engineer_responder(extra_positive: str | None = None, *, fake: bool = False):
    def responder(request):
        role = request.payload["task"]["role"]
        if role == "generation_engineer":
            brief = request.payload["approved_inputs"]["final_brief"]
            return engineer_response(
                candidate_data(brief, extra_positive=extra_positive, fake=fake)
            )
        if role == "requirements_consultant":
            return _requirements_revision_responder(request)
        if role == "output_reviewer":
            targets = request.payload.get("review_targets") or []
            if targets and targets[0].get("target_kind") == "brief_draft":
                return _requirements_revision_responder(request)
        return _responder(request)

    return responder


def _requirements_revision_responder(request):
    role = request.payload["task"]["role"]
    if role == "requirements_consultant":
        data = base_brief()
        data["facts"] = []
        data["texts"] = []
        return {
            "role": "requirements_consultant",
            "status": "ready",
            "data": data,
            "issues": [],
            "questions": [],
        }
    if role == "output_reviewer":
        targets = request.payload.get("review_targets") or []
        if targets and targets[0].get("target_kind") == "brief_draft":
            return review_response(targets[0]["target_id"], rules=REVIEW_RULE_IDS)
    return _responder(request)


@pytest.mark.asyncio
async def test_cross_client_replay_returns_one_intent_and_one_submit(full_settings):
    llm = LLMStub()
    gate = asyncio.Event()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    request_id = str(uuid.uuid4())
    async with _environment(full_settings, llm, visual) as (app, client):
        setup = await _setup_s06(app, client)

        first_read = await client.get(f"/api/projects/{setup['project_id']}/workflow")
        second_read = await client.get(f"/api/projects/{setup['project_id']}/workflow")
        assert first_read.status_code == 200
        assert second_read.status_code == 200
        assert llm.role_calls_received == []
        assert visual.submit_calls == []

        payload = _confirmation_payload(setup, request_id)
        async with authenticated_client(app) as other_client:
            first = await client.post(
                f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
                json=payload,
            )
            replay = await other_client.post(
                f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
                json=payload,
            )
        assert first.status_code == 200, first.text
        assert replay.status_code == 200, replay.text
        assert first.json()["intent"]["id"] == replay.json()["intent"]["id"]
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert len(usages) == 1
        assert len(slots) == 3
        assert all(slot.released_at is None for slot in slots)

        # A read-only refresh and a second authenticated session must not call a model.
        async with authenticated_client(app) as observer:
            observed = await observer.get(
                f"/api/projects/{setup['project_id']}/workflow"
            )
        assert observed.status_code == 200
        assert len(llm.role_calls_received) <= 1
        assert visual.submit_calls == []

        gate.set()
        await _drain(app)
        intents, usages, slots = await _rows(app, setup["project_id"])
        async with app.state.session_factory() as dbg:
            run_dbg = await dbg.scalar(
                select(WorkflowRun).where(
                    WorkflowRun.project_id == setup["project_id"]
                )
            )
        assert run_dbg is not None
        assert run_dbg.stage == "S10" and run_dbg.status == "needs_input"
        assert len(intents) == 1
        assert intents[0].status == "executing"
        assert len(usages) == 1
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_tampered_bindings_are_rejected_without_intent_or_approval(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(full_settings, llm, visual) as (app, client):
        setup = await _setup_s06(app, client)
        cases = [
            ({"artifact_digest": "0" * 64}, "CARD_DIGEST_MISMATCH"),
            ({"artifact_id": "missing-card"}, "CARD_DIGEST_MISMATCH"),
            ({"requirement_revision": setup["revision"] + 1}, "REQUIREMENT_REVISION_CONFLICT"),
            ({"size": "2048x2048"}, "REQUIREMENTS_CHANGED"),
            ({"reference_usage": "style"}, "REQUIREMENTS_CHANGED"),
        ]
        for overrides, code in cases:
            response = await client.post(
                f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
                json=_confirmation_payload(setup, str(uuid.uuid4()), **overrides),
            )
            assert response.status_code == 409, (overrides, response.text)
            assert response.json()["error"]["code"] == code
            intents, usages, slots = await _rows(app, setup["project_id"])
            assert intents == [] and usages == [] and slots == []
            async with app.state.session_factory() as session:
                approval = await session.scalar(
                    select(Approval).where(
                        Approval.project_id == setup["project_id"],
                        Approval.approval_type == "requirements_card",
                    )
                )
            assert approval is None
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_same_request_with_changed_size_does_not_reuse_intent(full_settings):
    llm = LLMStub()
    gate = asyncio.Event()
    llm.gate = gate
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    request_id = str(uuid.uuid4())
    async with _environment(full_settings, llm, visual) as (app, client):
        setup = await _setup_s06(app, client)
        accepted = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, request_id),
        )
        assert accepted.status_code == 200, accepted.text
        intent_id = accepted.json()["intent"]["id"]

        changed = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, request_id, size="2048x2048"),
        )
        assert changed.status_code == 409, changed.text
        assert changed.json()["error"]["code"] in {
            "INTENT_REPLAY_CONFLICT",
            "REQUIREMENTS_CHANGED",
        }
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert [intent.id for intent in intents] == [intent_id]
        assert len(usages) == 1
        assert len(slots) == 3
        assert visual.submit_calls == []
        gate.set()
        await _drain(app)


@pytest.mark.asyncio
async def test_cross_account_request_identifier_cannot_reuse_project(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    request_id = str(uuid.uuid4())
    async with _environment(full_settings, llm, visual) as (app, client):
        setup = await _setup_s06(app, client)
        await _setup_user(app, "t66-other@example.com")
        async with _client(app) as other:
            await login(other, "t66-other@example.com", USER_PASSWORD)
            response = await other.post(
                f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
                json=_confirmation_payload(setup, request_id),
            )
            assert response.status_code == 404, response.text
            assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert intents == [] and usages == [] and slots == []


@pytest.mark.asyncio
async def test_prompt_drift_abandons_intent_and_guides_reconfirmation(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(
        full_settings,
        llm,
        visual,
        responder=_generation_engineer_responder(extra_positive="限时特价 9.9 元"),
    ) as (app, client):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        await _drain(app)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert intents[0].status == "abandoned"
        assert len(usages) == 1
        assert all(slot.released_at is not None for slot in slots)
        assert all(slot.release_reason == "prompt_requires_reconfirmation" for slot in slots)
        async with app.state.session_factory() as session:
            prompt_artifacts = (
                await session.scalars(
                    select(Artifact).where(
                        Artifact.project_id == setup["project_id"],
                        Artifact.kind == "prompt_plan",
                    )
                )
            ).all()
            prompt_approvals = (
                await session.scalars(
                    select(Approval).where(
                        Approval.project_id == setup["project_id"],
                        Approval.approval_type == "generation_prompt",
                    )
                )
            ).all()
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == setup["project_id"])
            )
        assert prompt_artifacts == []
        assert prompt_approvals == []
        assert run is not None and run.stage == "S07" and run.status == "blocked"
        assert run.issue_code == "PROMPT_REQUIRES_RECONFIRMATION"
        assert visual.submit_calls == []

        workflow = await client.get(f"/api/projects/{setup['project_id']}/workflow")
        assert workflow.status_code == 200, workflow.text
        view = workflow.json()
        assert view["issue"]["code"] == "PROMPT_REQUIRES_RECONFIRMATION"
        assert "revise_requirements" in [action["id"] for action in view["available_actions"]]
        assert "PROMPT_REQUIRES_RECONFIRMATION" not in str(
            [artifact["payload"] for artifact in view["artifacts"]]
        )

        revised = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/revise_requirements",
            json={
                "expected_revision": view["project_revision"],
                "text": "请补充一句：杯身蓝色鲸鱼图案，其他要求不变。",
            },
        )
        assert revised.status_code == 200, revised.text
        updated = revised.json()
        assert updated["stage"] == "S04"
        assert updated["requirement_revision"] > view["requirement_revision"]
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_weakened_prompt_coverage_requires_reconfirmation(full_settings):
    llm = LLMStub()
    visual = VisualStub()
    async with _environment(
        full_settings,
        llm,
        visual,
        responder=_generation_engineer_responder(fake=True),
    ) as (app, client):
        setup = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
            json=_confirmation_payload(setup, str(uuid.uuid4())),
        )
        assert response.status_code == 200, response.text
        await _drain(app)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert intents[0].status == "abandoned"
        assert len(usages) == 1
        assert all(slot.released_at is not None for slot in slots)
        async with app.state.session_factory() as session:
            prompt_artifacts = (
                await session.scalars(
                    select(Artifact).where(
                        Artifact.project_id == setup["project_id"],
                        Artifact.kind == "prompt_plan",
                    )
                )
            ).all()
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == setup["project_id"])
            )
        assert prompt_artifacts == []
        assert run is not None and run.stage == "S07" and run.status == "blocked"
        assert run.issue_code == "PROMPT_REQUIRES_RECONFIRMATION"
        assert visual.submit_calls == []

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderError, ProviderOutcome
from app.auth.models import Account
from app.db import write_transaction
from app.errors import ApiError
from app.models import Project
from app.services.recovery import repair_leftover_state
from app.timeutil import format_timestamp, utc_now
from app.workflow import state
from app.workflow.tables import (
    Artifact,
    GenerationJob,
    RevisionBudget,
    WorkflowRun,
)
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import (
    _drain,
    _environment,
    _rows,
    _setup_s06,
)
from tests.test_t68_recovery import _confirm, _normal_responder
from tests.test_v2_t40_prompt_review import png_bytes


def _first_producer_failure_responder():
    failed = {"value": False}

    def responder(request):
        role = request.payload["task"]["role"]
        if role == "generation_engineer" and not failed["value"]:
            failed["value"] = True
            raise ProviderError("model timeout", evidence="uncertain", error_code="LLM_TIMEOUT")
        return _normal_responder(request)

    return responder


def _first_reviewer_failure_responder():
    failed = {"value": False}

    def responder(request):
        role = request.payload["task"]["role"]
        if role == "output_reviewer" and not failed["value"]:
            failed["value"] = True
            raise ProviderError("review timeout", evidence="uncertain", error_code="LLM_TIMEOUT")
        return _normal_responder(request)

    return responder


def _visual() -> VisualStub:
    return VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )


async def _continue(client, project_id: str, revision: int):
    return await client.post(
        f"/api/projects/{project_id}/workflow/actions/continue_workflow",
        json={"expected_revision": revision, "payload": {}},
    )


async def _budget(app, project_id: str) -> RevisionBudget | None:
    async with app.state.session_factory() as session:
        run = await session.scalar(select(WorkflowRun).where(WorkflowRun.project_id == project_id))
        if run is None:
            return None
        return await session.get(RevisionBudget, (run.id, "prompt_plan"))


async def _artifacts(app, project_id: str) -> list[Artifact]:
    async with app.state.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(Artifact).where(
                        Artifact.project_id == project_id,
                        Artifact.kind == "prompt_plan",
                    )
                )
            ).all()
        )


async def _run(app, project_id: str) -> WorkflowRun:
    async with app.state.session_factory() as session:
        run = await session.scalar(select(WorkflowRun).where(WorkflowRun.project_id == project_id))
        assert run is not None
        return run


@pytest.mark.asyncio
async def test_s07_producer_failure_replays_exact_request_without_new_budget(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual, _first_producer_failure_responder()) as (
        app,
        client,
    ):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)

        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["stage"] == "S07"
        assert view["status"] == "failed"
        assert {item["id"] for item in view["available_actions"]} == {"continue_workflow"}
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and intents[0].status in {"accepted", "executing"}
        assert len(usages) == 1
        assert len(slots) == 3 and all(slot.released_at is None for slot in slots)
        budget = await _budget(app, setup["project_id"])
        assert budget is not None and budget.used == 1
        assert await _artifacts(app, setup["project_id"]) == []
        assert len(llm.role_calls_received) == 1
        first_payload = llm.role_calls_received[0].payload

        # Refresh/status reads never call a model or consume another candidate.
        for _ in range(2):
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        assert len(llm.role_calls_received) == 1

        response = await _continue(client, setup["project_id"], setup["revision"])
        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "S08"
        assert view["status"] == "active"

        assert len(llm.role_calls_received) == 3
        assert llm.role_calls_received[1].payload == first_payload
        artifacts = await _artifacts(app, setup["project_id"])
        assert len(artifacts) == 1
        assert artifacts[0].revision_no == 1
        assert artifacts[0].status == "approved"
        budget = await _budget(app, setup["project_id"])
        assert budget is not None and budget.used == 1
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert len(slots) == 3 and all(slot.released_at is None for slot in slots)
        assert visual.submit_calls == []

        # Repeating the same manual continue is idempotent and never replays the model.
        response = await _continue(client, setup["project_id"], setup["revision"])
        assert response.status_code == 200, response.text
        assert len(llm.role_calls_received) == 3


@pytest.mark.asyncio
async def test_s07_replay_budget_and_ledger_survive_process_restart(full_settings):
    llm = LLMStub()
    visual = _visual()
    responder = _first_producer_failure_responder()
    project_id = ""
    revision = 0
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        setup = await _setup_s06(app, client)
        project_id = setup["project_id"]
        revision = setup["revision"]
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        assert len(llm.role_calls_received) == 1

    async with _environment(full_settings, llm, visual, responder) as (app, client):
        view = (await client.get(f"/api/projects/{project_id}/workflow")).json()
        assert view["stage"] == "S07"
        assert view["status"] == "failed"
        assert len(llm.role_calls_received) == 1
        budget = await _budget(app, project_id)
        assert budget is not None and budget.used == 1

        response = await _continue(client, project_id, revision)
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "S08"
        assert len(llm.role_calls_received) == 3
        budget = await _budget(app, project_id)
        assert budget is not None and budget.used == 1
        artifacts = await _artifacts(app, project_id)
        assert len(artifacts) == 1 and artifacts[0].status == "approved"
        intents, usages, slots = await _rows(app, project_id)
        assert len(intents) == 1 and len(usages) == 1
        assert len(slots) == 3 and all(slot.released_at is None for slot in slots)
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_s07_recovery_stops_at_persisted_budget_before_model_call(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual, _first_producer_failure_responder()) as (
        app,
        client,
    ):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        assert len(llm.role_calls_received) == 1
        async with write_transaction(app.state.session_factory) as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == setup["project_id"])
            )
            assert run is not None
            budget = await session.get(RevisionBudget, (run.id, "prompt_plan"))
            assert budget is not None
            budget.used = budget.max_allowed
            await session.flush()

        response = await _continue(client, setup["project_id"], setup["revision"])
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVISION_BUDGET_EXHAUSTED"
        assert len(llm.role_calls_received) == 1
        assert await _artifacts(app, setup["project_id"]) == []
        assert visual.submit_calls == []
        run = await _run(app, setup["project_id"])
        assert run.recovery_claim_id is None


@pytest.mark.asyncio
async def test_s07_recovery_rejects_changed_dependency_before_model_call(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual, _first_producer_failure_responder()) as (
        app,
        client,
    ):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        assert len(llm.role_calls_received) == 1
        async with write_transaction(app.state.session_factory) as session:
            project = await session.get(Project, setup["project_id"])
            assert project is not None
            project.revision += 1
            await session.flush()

        response = await _continue(client, setup["project_id"], setup["revision"] + 1)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "STALE_RECOVERY"
        assert len(llm.role_calls_received) == 1
        budget = await _budget(app, setup["project_id"])
        assert budget is not None and budget.used == 1
        assert await _artifacts(app, setup["project_id"]) == []
        run = await _run(app, setup["project_id"])
        assert run.recovery_claim_id is None


@pytest.mark.asyncio
async def test_s07_recovery_stops_when_image_submission_is_unknown(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual, _first_producer_failure_responder()) as (
        app,
        client,
    ):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        assert len(llm.role_calls_received) == 1
        run = await _run(app, setup["project_id"])
        now = format_timestamp(utc_now())
        async with write_transaction(app.state.session_factory) as session:
            session.add(
                GenerationJob(
                    id=str(uuid.uuid4()),
                    project_id=setup["project_id"],
                    run_id=run.id,
                    request_id=str(uuid.uuid4()),
                    request_digest="a" * 64,
                    confirmed_snapshot_digest="b" * 64,
                    status="unknown",
                    submit_attempted=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.flush()

        response = await _continue(client, setup["project_id"], setup["revision"])
        assert response.status_code == 409, response.text
        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["stage"] == "S07"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "GENERATION_UNKNOWN"
        assert len(llm.role_calls_received) == 1
        budget = await _budget(app, setup["project_id"])
        assert budget is not None and budget.used == 1
        assert await _artifacts(app, setup["project_id"]) == []
        assert visual.submit_calls == []
        run = await _run(app, setup["project_id"])
        assert run.recovery_claim_id is None


@pytest.mark.asyncio
async def test_recovery_claim_is_atomic_across_independent_connections(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual, _normal_responder) as (app, client):
        setup = await _setup_s06(app, client)
        async with write_transaction(app.state.session_factory) as session:
            run = await session.scalar(
                select(WorkflowRun).where(WorkflowRun.project_id == setup["project_id"])
            )
            assert run is not None
            run.stage = "S07"
            run.status = "failed"
            run.issue_code = "SERVICE_UNAVAILABLE"
            await session.flush()

        first = await state.load_context(app, setup["project_id"])
        second = await state.load_context(app, setup["project_id"])
        results = await asyncio.gather(
            state.claim_recovery(first, "op-first", stage="S07"),
            state.claim_recovery(second, "op-second", stage="S07"),
            return_exceptions=True,
        )
        assert sum(result is None for result in results) == 1
        failures = [result for result in results if isinstance(result, ApiError)]
        assert len(failures) == 1 and failures[0].code == "PROJECT_BUSY"
        run = await _run(app, setup["project_id"])
        assert run.recovery_claim_id in {"op-first", "op-second"}
        assert run.recovery_claimed_at is not None

        # A hard crash may leave a claim behind; startup repair clears it so the
        # original failed run can be manually recovered after restart.
        await repair_leftover_state(app)
        run = await _run(app, setup["project_id"])
        assert run.recovery_claim_id is None
        assert run.recovery_claimed_at is None


@pytest.mark.asyncio
async def test_s12_review_resume_replays_exact_reviewer_request_without_new_budget(
    full_settings,
):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual, _first_reviewer_failure_responder()) as (
        app,
        client,
    ):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)

        view = (await client.get(f"/api/projects/{setup['project_id']}/workflow")).json()
        assert view["stage"] == "S07"
        assert view["status"] == "failed"
        assert view["issue"]["code"] == "REVIEW_UNAVAILABLE"
        assert {item["id"] for item in view["available_actions"]} == {"continue_workflow"}
        assert len(llm.role_calls_received) == 2
        first_review_payload = llm.role_calls_received[1].payload
        budget = await _budget(app, setup["project_id"])
        assert budget is not None and budget.used == 1
        artifacts = await _artifacts(app, setup["project_id"])
        assert len(artifacts) == 1 and artifacts[0].status == "candidate"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert len(slots) == 3 and all(slot.released_at is None for slot in slots)

        response = await _continue(client, setup["project_id"], setup["revision"])
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "S08"
        assert response.json()["status"] == "active"

        assert len(llm.role_calls_received) == 3
        assert llm.role_calls_received[2].payload == first_review_payload
        artifacts = await _artifacts(app, setup["project_id"])
        assert len(artifacts) == 1
        assert artifacts[0].revision_no == 1
        assert artifacts[0].status == "approved"
        budget = await _budget(app, setup["project_id"])
        assert budget is not None and budget.used == 1
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert len(slots) == 3 and all(slot.released_at is None for slot in slots)
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_concurrent_manual_recovery_shares_one_budget_and_one_replay(
    full_settings,
):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual, _first_producer_failure_responder()) as (
        app,
        client,
    ):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        assert len(llm.role_calls_received) == 1

        llm.gate = asyncio.Event()
        first = asyncio.create_task(_continue(client, setup["project_id"], setup["revision"]))
        for _ in range(200):
            if len(llm.role_calls_received) >= 2:
                break
            await asyncio.sleep(0.01)
        assert len(llm.role_calls_received) == 2

        second = await _continue(client, setup["project_id"], setup["revision"])
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "PROJECT_BUSY"
        llm.gate.set()
        first_response = await first
        assert first_response.status_code == 200, first_response.text
        assert first_response.json()["stage"] == "S08"

        assert len(llm.role_calls_received) == 3
        budget = await _budget(app, setup["project_id"])
        assert budget is not None and budget.used == 1
        artifacts = await _artifacts(app, setup["project_id"])
        assert len(artifacts) == 1 and artifacts[0].status == "approved"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert len(slots) == 3 and all(slot.released_at is None for slot in slots)
        assert visual.submit_calls == []
        run = await _run(app, setup["project_id"])
        assert run.recovery_claim_id is None


@pytest.mark.asyncio
async def test_disabled_account_blocks_manual_recovery_before_model_call(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual, _first_producer_failure_responder()) as (
        app,
        client,
    ):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        assert len(llm.role_calls_received) == 1

        async with write_transaction(app.state.session_factory) as session:
            project = await session.get(Project, setup["project_id"])
            assert project is not None and project.owner_account_id is not None
            account = await session.get(Account, project.owner_account_id)
            assert account is not None
            account.is_disabled = 1
            account.disabled_at = format_timestamp(app.state.clock.now())
            account.disabled_by = account.id
            await session.flush()

        response = await _continue(client, setup["project_id"], setup["revision"])
        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
        assert len(llm.role_calls_received) == 1
        budget = await _budget(app, setup["project_id"])
        assert budget is not None and budget.used == 1
        assert await _artifacts(app, setup["project_id"]) == []
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert len(slots) == 3 and all(slot.released_at is None for slot in slots)
        assert visual.submit_calls == []
        run = await _run(app, setup["project_id"])
        assert run.recovery_claim_id is None

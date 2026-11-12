from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.auth.models import AuditEvent
from app.db import read_session
from app.workflow.tables import GenerationJob
from tests.support.adapters import LLMStub, VisualStub
from tests.support.auth import login
from tests.test_t56_account_links import (
    USER_PASSWORD,
    _client,
    _create,
    _issue,
    _link_client,
    _submit,
    _token,
)
from tests.test_t65_generation_cycle import (
    _delivery_response,
    _drain,
    _environment,
    _quality_response,
    _review_response,
    _rows,
    _setup_s06,
)
from tests.test_v2_t40_prompt_review import (
    candidate_data,
    engineer_response,
    png_bytes,
)


async def _user_client(app, admin):
    email = f"closure-{uuid.uuid4().hex[:8]}@example.com"
    account = await _create(admin, email)
    issued = await _issue(admin, account["id"])
    anon = await _link_client(app)
    submitted = await _submit(anon, _token(issued["link"]), "setup", USER_PASSWORD)
    assert submitted.status_code == 200, submitted.text
    await anon.aclose()
    client = _client(app)
    await login(client, email, USER_PASSWORD)
    return account, client


def _responder(request):
    role = request.payload["task"]["role"]
    if role == "generation_engineer":
        brief = request.payload["approved_inputs"]["final_brief"]
        return engineer_response(candidate_data(brief))
    if role == "quality_compliance":
        return _quality_response(request)
    if role == "delivery_manager":
        return _delivery_response(request)
    if role == "output_reviewer":
        return _review_response(request)
    raise AssertionError(f"unexpected role {role}")


async def _confirm(client, setup, request_id: str):
    return await client.post(
        f"/api/projects/{setup['project_id']}/workflow/actions/confirm_requirements",
        json={
            "expected_revision": setup["revision"],
            "payload": {
                "artifact_id": setup["final_artifact_id"],
                "artifact_digest": setup["final_digest"],
                "requirement_revision": setup["revision"],
                "request_id": request_id,
            },
        },
    )


async def _latest_job(app, project_id: str) -> GenerationJob:
    async with app.state.session_factory() as session:
        job = await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.project_id == project_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        assert job is not None
        return job


async def _unknown_cycle(app, client, setup, visual: VisualStub) -> dict:
    response = await _confirm(client, setup, str(uuid.uuid4()))
    assert response.status_code == 200, response.text
    await _drain(app)
    view = (
        await client.get(f"/api/projects/{setup['project_id']}/workflow")
    ).json()
    assert view["stage"] == "S08"
    assert view["status"] == "blocked"
    assert view["issue"]["code"] == "GENERATION_UNKNOWN"
    assert "close_generation_tracking" in {
        item["id"] for item in view["available_actions"]
    }
    assert visual.submit_calls
    return view


@pytest.mark.asyncio
async def test_local_closure_preserves_count_releases_slots_and_audits(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-69-close",
            result_locator="https://cdn.invalid/69-close.png",
        ),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                remote_task_id="remote-69-close",
                error_code="VISUAL_OUTCOME_UNKNOWN",
            )
        ],
    )
    async with _environment(full_settings, llm, visual, _responder) as (app, client):
        setup = await _setup_s06(app, client)
        view = await _unknown_cycle(app, client, setup, visual)
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert len(slots) == 3 and all(slot.released_at is None for slot in slots)

        before_calls = (
            len(llm.role_calls_received),
            len(visual.submit_calls),
            len(visual.poll_calls),
        )
        for _ in range(3):
            refreshed = (
                await client.get(f"/api/projects/{setup['project_id']}/workflow")
            ).json()
            assert refreshed["issue"]["code"] == "GENERATION_UNKNOWN"
        assert (
            len(llm.role_calls_received),
            len(visual.submit_calls),
            len(visual.poll_calls),
        ) == before_calls

        closed = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert closed.status_code == 200, closed.text
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S08"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "LOCAL_TRACKING_CLOSED"
        assert {item["id"] for item in view["available_actions"]} == {
            "start_new_generation"
        }

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and intents[0].status == "closed"
        assert len(usages) == 1
        assert len(slots) == 3
        assert all(slot.released_at is not None for slot in slots)
        assert all(slot.release_reason == "local_tracking_closed" for slot in slots)
        async with read_session(app.state.session_factory) as session:
            audit = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.event_type == "generation_tracking_closed"
                )
            )
            assert audit is not None
            assert json.loads(audit.before_json)["active_slots"] == 3
            assert json.loads(audit.after_json) == {"status": "closed", "active_slots": 0}
            assert json.loads(audit.detail_json)["remote_cancelled"] is False

        repeated = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert repeated.status_code == 409
        intents_after, usages_after, slots_after = await _rows(app, setup["project_id"])
        assert len(intents_after) == 1 and len(usages_after) == 1
        assert all(slot.released_at is not None for slot in slots_after)
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_risk_confirmed_new_task_has_new_intent_count_slots_and_submit(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-69-risk",
            result_locator="https://cdn.invalid/69-risk.png",
        ),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                remote_task_id="remote-69-risk",
                error_code="VISUAL_OUTCOME_UNKNOWN",
            )
        ],
    )
    async with _environment(full_settings, llm, visual, _responder) as (app, client):
        setup = await _setup_s06(app, client)
        view = await _unknown_cycle(app, client, setup, visual)
        closed = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert closed.status_code == 200, closed.text
        job = await _latest_job(app, setup["project_id"])
        old_intent_id = job.intent_id
        assert old_intent_id is not None
        visual.poll_outcomes = [
            ProviderOutcome(
                state="succeeded",
                remote_task_id="remote-69-risk",
                image_bytes=png_bytes(1024, 1024),
                image_count=1,
            )
        ]
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        created = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/start_new_generation",
            json={
                "expected_revision": view["project_revision"],
                "payload": {
                    "acknowledge_risk": True,
                    "previous_generation_id": job.generation_id,
                },
            },
        )
        assert created.status_code == 200, created.text
        await _drain(app)

        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S10"
        assert view["status"] == "needs_input"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 2
        assert {intent.status for intent in intents} == {"closed", "executing"}
        assert len(usages) == 2
        assert len(slots) == 6
        released = [slot for slot in slots if slot.released_at is not None]
        active = [slot for slot in slots if slot.released_at is None]
        assert len(released) == 3 and len(active) == 3
        assert len(visual.submit_calls) == 2
        assert len(visual.poll_calls) == 2
        assert intents[1].request_id != intents[0].request_id

        repeated = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/start_new_generation",
            json={
                "expected_revision": view["project_revision"],
                "payload": {
                    "acknowledge_risk": True,
                    "previous_generation_id": job.generation_id,
                },
            },
        )
        assert repeated.status_code == 409
        intents_after, usages_after, _ = await _rows(app, setup["project_id"])
        assert len(intents_after) == 2 and len(usages_after) == 2
        assert len(visual.submit_calls) == 2


@pytest.mark.asyncio
async def test_closed_tracking_new_task_obeys_daily_limit_without_side_effects(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-69-quota",
            result_locator="https://cdn.invalid/69-quota.png",
        ),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                remote_task_id="remote-69-quota",
                error_code="VISUAL_OUTCOME_UNKNOWN",
            )
        ],
    )
    async with _environment(full_settings, llm, visual, _responder) as (app, client):
        setup = await _setup_s06(app, client)
        view = await _unknown_cycle(app, client, setup, visual)
        closed = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert closed.status_code == 200, closed.text
        job = await _latest_job(app, setup["project_id"])
        app.state.settings.generation_daily_limit = 1
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/start_new_generation",
            json={
                "expected_revision": view["project_revision"],
                "payload": {
                    "acknowledge_risk": True,
                    "previous_generation_id": job.generation_id,
                },
            },
        )
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "GENERATION_LIMIT_REACHED"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert all(slot.released_at is not None for slot in slots)
        assert len(visual.submit_calls) == 1
        assert len(llm.role_calls_received) == 2


@pytest.mark.asyncio
async def test_unknown_v3_task_cannot_reuse_legacy_acknowledge_path(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-69-ack",
            result_locator="https://cdn.invalid/69-ack.png",
        ),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                remote_task_id="remote-69-ack",
                error_code="VISUAL_OUTCOME_UNKNOWN",
            )
        ],
    )
    async with _environment(full_settings, llm, visual, _responder) as (app, client):
        setup = await _setup_s06(app, client)
        view = await _unknown_cycle(app, client, setup, visual)
        job = await _latest_job(app, setup["project_id"])
        response = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/submit_generation",
            json={
                "expected_revision": view["project_revision"],
                "payload": {
                    "acknowledge_unknown": True,
                    "previous_generation_id": job.generation_id,
                },
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "LOCAL_CLOSURE_REQUIRED"
        intents, usages, _ = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert len(visual.submit_calls) == 1


@pytest.mark.asyncio
async def test_local_closure_requires_owner_and_respects_disabled_account(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-69-permission",
            result_locator="https://cdn.invalid/69-permission.png",
        ),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                remote_task_id="remote-69-permission",
                error_code="VISUAL_OUTCOME_UNKNOWN",
            )
        ],
    )
    async with _environment(full_settings, llm, visual, _responder) as (app, admin):
        owner_account, owner = await _user_client(app, admin)
        setup = await _setup_s06(app, owner)
        view = await _unknown_cycle(app, owner, setup, visual)
        other_account, other = await _user_client(app, admin)

        denied = await other.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert denied.status_code == 404, denied.text
        assert denied.json()["error"]["code"] == "PROJECT_NOT_FOUND"

        disabled = await admin.post(
            f"/api/admin/accounts/{owner_account['id']}/disable"
        )
        assert disabled.status_code == 200, disabled.text
        blocked = await owner.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert blocked.status_code == 401, blocked.text
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and intents[0].status == "executing"
        assert len(usages) == 1
        assert all(slot.released_at is None for slot in slots)

        restored = await admin.post(
            f"/api/admin/accounts/{owner_account['id']}/restore"
        )
        assert restored.status_code == 200, restored.text
        relogin = _client(app)
        await login(relogin, owner_account["email"], USER_PASSWORD)
        closed = await relogin.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert closed.status_code == 200, closed.text
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert intents[0].status == "closed"
        assert len(usages) == 1
        assert all(slot.released_at is not None for slot in slots)
        assert other_account["id"] != owner_account["id"]


@pytest.mark.asyncio
async def test_concurrent_local_closure_is_single_and_audited_once(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-69-race",
            result_locator="https://cdn.invalid/69-race.png",
        ),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                remote_task_id="remote-69-race",
                error_code="VISUAL_OUTCOME_UNKNOWN",
            )
        ],
    )
    async with _environment(full_settings, llm, visual, _responder) as (app, admin):
        _account, owner = await _user_client(app, admin)
        setup = await _setup_s06(app, owner)
        view = await _unknown_cycle(app, owner, setup, visual)
        second = _client(app)
        await login(second, _account["email"], USER_PASSWORD)

        payload = {
            "expected_revision": view["project_revision"],
            "payload": {"acknowledge_local_close": True},
        }
        first_response, second_response = await asyncio.gather(
            owner.post(
                f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
                json=payload,
            ),
            second.post(
                f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
                json=payload,
            ),
        )
        assert sorted([first_response.status_code, second_response.status_code]) == [
            200,
            409,
        ], (first_response.text, second_response.text)

        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and intents[0].status == "closed"
        assert len(usages) == 1
        assert all(slot.released_at is not None for slot in slots)
        async with read_session(app.state.session_factory) as session:
            events = list(
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "generation_tracking_closed"
                    )
                )
            )
        assert len(events) == 1


@pytest.mark.asyncio
async def test_risk_confirmed_new_task_requires_active_owner(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-69-disabled-risk",
            result_locator="https://cdn.invalid/69-disabled-risk.png",
        ),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                remote_task_id="remote-69-disabled-risk",
                error_code="VISUAL_OUTCOME_UNKNOWN",
            )
        ],
    )
    async with _environment(full_settings, llm, visual, _responder) as (app, admin):
        owner_account, owner = await _user_client(app, admin)
        setup = await _setup_s06(app, owner)
        view = await _unknown_cycle(app, owner, setup, visual)
        closed = await owner.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert closed.status_code == 200, closed.text
        job = await _latest_job(app, setup["project_id"])
        disabled = await admin.post(
            f"/api/admin/accounts/{owner_account['id']}/disable"
        )
        assert disabled.status_code == 200, disabled.text
        denied = await owner.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/start_new_generation",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_risk": True},
            },
        )
        assert denied.status_code == 401, denied.text
        intents, usages, _ = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert len(visual.submit_calls) == 1

        restored = await admin.post(
            f"/api/admin/accounts/{owner_account['id']}/restore"
        )
        assert restored.status_code == 200, restored.text
        relogin = _client(app)
        await login(relogin, owner_account["email"], USER_PASSWORD)
        allowed = await relogin.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/start_new_generation",
            json={
                "expected_revision": view["project_revision"],
                "payload": {
                    "acknowledge_risk": True,
                    "previous_generation_id": job.generation_id,
                },
            },
        )
        assert allowed.status_code == 200, allowed.text
        intents, usages, _ = await _rows(app, setup["project_id"])
        assert len(intents) == 2 and len(usages) == 2


@pytest.mark.asyncio
async def test_concurrent_risk_confirmation_creates_one_new_intent(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-69-risk-race",
            result_locator="https://cdn.invalid/69-risk-race.png",
        ),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                remote_task_id="remote-69-risk-race",
                error_code="VISUAL_OUTCOME_UNKNOWN",
            )
        ],
    )
    async with _environment(full_settings, llm, visual, _responder) as (app, admin):
        account, owner = await _user_client(app, admin)
        setup = await _setup_s06(app, owner)
        view = await _unknown_cycle(app, owner, setup, visual)
        closed = await owner.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert closed.status_code == 200, closed.text
        job = await _latest_job(app, setup["project_id"])
        second = _client(app)
        await login(second, account["email"], USER_PASSWORD)
        payload = {
            "expected_revision": view["project_revision"],
            "payload": {"acknowledge_risk": True, "previous_generation_id": job.generation_id},
        }
        first_response, second_response = await asyncio.gather(
            owner.post(
                f"/api/projects/{setup['project_id']}/workflow/actions/start_new_generation",
                json=payload,
            ),
            second.post(
                f"/api/projects/{setup['project_id']}/workflow/actions/start_new_generation",
                json=payload,
            ),
        )
        assert sorted([first_response.status_code, second_response.status_code]) == [
            200,
            409,
        ], (first_response.text, second_response.text)
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 2
        assert len(usages) == 2
        assert len(slots) == 6
        assert len([slot for slot in slots if slot.released_at is None]) == 3

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import ASGITransport
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.auth.models import Account, AuthSession
from app.auth.passwords import hash_password
from app.auth.service import normalize_email
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.models import Generation, Project
from app.timeutil import format_timestamp
from app.workflow import roles, state
from app.workflow.stages import s10
from tests.support.adapters import LLMStub, VisualStub
from tests.support.auth import TEST_ORIGIN, AuthClient, login
from tests.support.t02_prompt import (
    SubmissionObservedVisual,
    final_click,
    ready_project,
)
from tests.support.t02_prompt import (
    responder as final_responder,
)
from tests.test_t65_generation_cycle import _environment as final_environment
from tests.test_v2_t11_generation import (
    db_job,
    drain,
    environment,
    png_bytes,
    setup_ready_project,
)
from tests.test_v2_t13_delivery import _delivery_env, _seed_ready_state

USER_PASSWORD = "correct horse battery staple 62"


async def _add_account(app: Any, email: str) -> Account:
    now = app.state.clock.now()
    password_hash = await app.state.hash_gate.run(hash_password, USER_PASSWORD)
    async with write_transaction(app.state.session_factory) as session:
        account = Account(
            id=str(uuid.uuid4()),
            email_normalized=normalize_email(email),
            password_hash=password_hash,
            password_set_at=format_timestamp(now),
            password_policy_version="r1-2026-10",
            is_admin=0,
            is_disabled=0,
            session_epoch=0,
            created_at=format_timestamp(now),
            updated_at=format_timestamp(now),
        )
        session.add(account)
        await session.flush()
        return account


async def _login_user(app: Any, email: str) -> AuthClient:
    client = AuthClient(transport=ASGITransport(app=app), base_url=TEST_ORIGIN)
    await login(client, email=email, password=USER_PASSWORD)
    return client


async def _wait_for_submit(visual: SubmissionObservedVisual) -> None:
    await asyncio.wait_for(visual.submit_started.wait(), timeout=10)
    assert len(visual.submit_calls) == 1


async def _job_state(app: Any, project_id: str) -> tuple[Any, Any]:
    job = await db_job(app, project_id)
    assert job is not None
    async with read_session(app.state.session_factory) as session:
        generation = await session.get(Generation, job.generation_id)
    assert generation is not None
    return job, generation


@pytest.mark.asyncio
async def test_logout_does_not_change_persisted_task_owner_or_stop_authorized_work(
    full_settings,
):
    gate = asyncio.Event()
    visual = SubmissionObservedVisual(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes(), image_count=1),
        gate=gate,
    )
    async with final_environment(full_settings, LLMStub(), visual, final_responder) as (
        app,
        _admin,
    ):
        owner = await _add_account(app, "owner62@example.com")
        other = await _add_account(app, "other62@example.com")
        owner_client = await _login_user(app, owner.email_normalized)
        setup = await ready_project(app, owner_client)

        started = await final_click(owner_client, setup)
        assert started.status_code == 200, started.text
        await _wait_for_submit(visual)
        job_before, _generation_before = await _job_state(app, setup["project_id"])
        assert job_before.owner_account_id == owner.id
        assert job_before.operator_account_id == owner.id
        assert job_before.authorization_ref

        logged_out = await owner_client.post("/api/auth/logout")
        assert logged_out.status_code == 204, logged_out.text
        other_client = await _login_user(app, other.email_normalized)
        forbidden = await other_client.get(f"/api/projects/{setup['project_id']}")
        assert forbidden.status_code == 404, forbidden.text

        gate.set()
        await drain(app)
        job, _generation = await _job_state(app, setup["project_id"])
        assert job.status == "approved", (
            job.status,
            job.error_code,
            job.error_message,
            _generation.error_code,
            _generation.error_message,
        )
        assert job.owner_account_id == owner.id
        assert job.operator_account_id == owner.id
        ctx = await state.load_context(app, setup["project_id"])
        assert ctx.run is not None
        assert ctx.run.stage == "S10"


@pytest.mark.asyncio
async def test_disable_stops_next_remote_call_and_keeps_remote_fact_without_publishing(
    full_settings,
):
    gate = asyncio.Event()
    visual = SubmissionObservedVisual(
        submit_outcome=ProviderOutcome(state="pending", remote_task_id="remote-62"),
        gate=gate,
    )
    async with final_environment(full_settings, LLMStub(), visual, final_responder) as (app, admin):
        owner = await _add_account(app, "disabled62@example.com")
        owner_client = await _login_user(app, owner.email_normalized)
        setup = await ready_project(app, owner_client)

        started = await final_click(owner_client, setup)
        assert started.status_code == 200, started.text
        await _wait_for_submit(visual)

        disabled = await admin.post(f"/api/admin/accounts/{owner.id}/disable")
        assert disabled.status_code == 200, disabled.text
        gate.set()
        await drain(app)

        job, generation = await _job_state(app, setup["project_id"])
        assert job.status == "unknown", job.status
        assert job.error_code == "ACCOUNT_DISABLED", job.error_code
        assert generation.remote_task_id == "remote-62"
        assert generation.result_asset_id is None
        assert generation.error_code == "ACCOUNT_DISABLED"
        assert visual.poll_calls == []
        ctx = await state.load_context(app, setup["project_id"])
        assert ctx.run is not None
        assert ctx.run.stage == "S08"
        assert ctx.run.status == "blocked"
        assert ctx.run.issue_code == "ACCOUNT_DISABLED"
        events = await admin.get("/api/admin/security-events")
        assert events.status_code == 200, events.text
        blocked_events = [
            item
            for item in events.json()["items"]
            if item["event_type"] == "generation_account_blocked"
        ]
        assert len(blocked_events) == 1, blocked_events
        assert blocked_events[0]["result"] == "denied"
        assert blocked_events[0]["detail"] == {
            "stage": "background",
            "remote_task_recorded": True,
        }

        restored = await admin.post(f"/api/admin/accounts/{owner.id}/restore")
        assert restored.status_code == 200, restored.text
        await drain(app)
        after_restore, after_generation = await _job_state(app, setup["project_id"])
        assert after_restore.status == "unknown"
        assert after_restore.error_code == "ACCOUNT_DISABLED"
        assert after_generation.remote_task_id == "remote-62"
        assert visual.poll_calls == []


@pytest.mark.asyncio
async def test_job_creation_persists_server_authorization_not_client_fields(
    full_settings,
):
    visual = SubmissionObservedVisual(
        submit_outcome=ProviderOutcome(state="succeeded", image_bytes=png_bytes(), image_count=1)
    )
    async with final_environment(full_settings, LLMStub(), visual, final_responder) as (
        app,
        _admin,
    ):
        owner = await _add_account(app, "authref62@example.com")
        owner_client = await _login_user(app, owner.email_normalized)
        setup = await ready_project(app, owner_client)
        forged = await owner_client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/submit_generation",
            json={
                "expected_revision": setup["revision"],
                "owner_account_id": "forged-owner",
                "is_admin": True,
            },
        )
        assert forged.status_code == 422, forged.text
        started = await final_click(owner_client, setup)
        assert started.status_code == 200, started.text
        await drain(app)
        job = await db_job(app, setup["project_id"])
        assert job is not None
        assert job.owner_account_id == owner.id
        assert job.operator_account_id == owner.id
        assert job.authorization_ref != "forged-ref"
        assert len(job.authorization_ref) == 64
        async with read_session(app.state.session_factory) as session:
            sessions = (
                await session.scalars(select(AuthSession).where(AuthSession.account_id == owner.id))
            ).all()
        assert sessions


@pytest.mark.asyncio
async def test_publish_rechecks_persisted_owner_before_writing_public_bytes(full_settings):
    stub = LLMStub()
    async with _delivery_env(full_settings, stub, _seed_ready_state) as env:
        owner = await _add_account(env.app, "publisher62@example.com")
        async with write_transaction(env.app.state.session_factory) as session:
            project = await session.get(Project, env.ids.project_id)
            assert project is not None
            project.owner_account_id = owner.id
            account = await session.get(Account, owner.id)
            assert account is not None
            account.is_disabled = 1
            account.disabled_at = format_timestamp(env.app.state.clock.now())
            account.disabled_by = owner.id
        ctx = await state.load_context(env.app, env.ids.project_id)
        with pytest.raises(Exception) as caught:
            await s10._publish(ctx)
        assert getattr(caught.value, "code", None) == "ACCOUNT_DISABLED"
        published_dir = full_settings.resolved_data_dir() / "published"
        assert not published_dir.exists() or not list(published_dir.iterdir())


@pytest.mark.asyncio
async def test_disabled_owner_cannot_make_another_model_call(
    full_settings,
):
    async with environment(full_settings, visual=VisualStub()) as (
        app,
        admin_client,
        llm,
        _visual,
        _registry,
    ):
        owner = await _add_account(app, "guard-owner62@example.com")
        setup = await setup_ready_project(app, admin_client)
        async with write_transaction(app.state.session_factory) as session:
            project = await session.get(Project, setup["project_id"])
            assert project is not None
            project.owner_account_id = owner.id

        disabled = await admin_client.post(f"/api/admin/accounts/{owner.id}/disable", json={})
        assert disabled.status_code == 200, disabled.text
        ctx = await state.load_context(app, setup["project_id"])
        with pytest.raises(ApiError) as error:
            await roles.run_role(ctx, "requirements_reviewer", "S01", {})
        assert error.value.code == "ACCOUNT_DISABLED"
        assert llm.role_calls_received == []

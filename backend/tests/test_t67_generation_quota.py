from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from app.acceptance import (
    GENERATION_WINDOW_MS,
    GenerationIntent,
    GenerationSlot,
    GenerationUsage,
    accept_in_session,
    release_slots_in_session,
    usage_snapshot,
)
from app.adapters.base import ProviderOutcome
from app.db import read_session, write_transaction
from app.errors import ApiError
from app.generation_quota import GenerationQuotaLimit
from app.main import create_app
from app.models import Project
from app.timeutil import format_timestamp
from app.workflow.tables import Artifact, WorkflowRun
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import TEST_ADMIN_EMAIL, login
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
    _drain,
    _environment,
    _setup_s06,
)

USER_EMAIL = "quota-user@example.com"


async def _user_client(app, admin: Any):
    account = await _create(admin, USER_EMAIL)
    issued = await _issue(admin, account["id"])
    anon = await _link_client(app)
    submitted = await _submit(anon, _token(issued["link"]), "setup", USER_PASSWORD)
    assert submitted.status_code == 200, submitted.text
    await anon.aclose()
    client = _client(app)
    await login(client, USER_EMAIL, USER_PASSWORD)
    return account, client


async def _accept_direct(
    app,
    setup: dict,
    *,
    now: datetime,
    request_id: str | None = None,
    image_size: str = "1024x1024",
):
    request_id = request_id or str(uuid.uuid4())
    async with write_transaction(app.state.session_factory) as session:
        project = await session.get(Project, setup["project_id"])
        assert project is not None
        run = await session.scalar(
            select(WorkflowRun).where(WorkflowRun.project_id == project.id)
        )
        assert run is not None
        artifact = await session.get(Artifact, setup["final_artifact_id"])
        assert artifact is not None
        return await accept_in_session(
            session,
            settings=app.state.settings,
            project=project,
            run=run,
            operator_account_id=project.owner_account_id,
            artifact=artifact,
            requirement_revision=project.revision,
            reference_usage="none",
            image_size=image_size,
            request_id=request_id,
            now=now,
        )


async def _release_slots(app, intent_id: str) -> None:
    async with write_transaction(app.state.session_factory) as session:
        await release_slots_in_session(session, intent_id=intent_id, reason="test_release")


async def _rows_for_project(app, project_id: str):
    async with read_session(app.state.session_factory) as session:
        intents = list(
            (
                await session.scalars(
                    select(GenerationIntent).where(
                        GenerationIntent.project_id == project_id
                    )
                )
            ).all()
        )
        usages = list(
            (
                await session.scalars(
                    select(GenerationUsage).where(GenerationUsage.project_id == project_id)
                )
            ).all()
        )
        slots = list(
            (
                await session.scalars(
                    select(GenerationSlot).where(
                        GenerationSlot.intent_id.in_([item.id for item in intents])
                    )
                )
            ).all()
        )
        return intents, usages, slots


async def _quota_rows(app) -> list[GenerationQuotaLimit]:
    async with read_session(app.state.session_factory) as session:
        return list((await session.scalars(select(GenerationQuotaLimit))).all())


async def _account_id(app, email: str) -> str:
    from app.auth.models import Account

    async with read_session(app.state.session_factory) as session:
        account = await session.scalar(
            select(Account).where(Account.email_normalized == email)
        )
        assert account is not None
        return account.id


def _visual() -> VisualStub:
    from tests.test_v2_t40_prompt_review import png_bytes

    return VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )


@pytest.mark.asyncio
async def test_defaults_and_admin_quota_updates_are_audited(full_settings, monkeypatch):
    async with _environment(full_settings, LLMStub(), _visual()) as (app, admin):
        account, _user = await _user_client(app, admin)
        defaults = await admin.get("/api/admin/generation-limits")
        assert defaults.status_code == 200, defaults.text
        body = defaults.json()
        assert body["policy"] == {
            "daily_limit": None,
            "personal_slots": None,
            "instance_slots": None,
            "updated_at": None,
        }
        assert body["defaults"] == {
            "daily_limit": 20,
            "personal_slots": 1,
            "instance_slots": 2,
            "project_slots": 1,
        }
        assert body["effective"] == body["defaults"]

        changed = await admin.put(
            "/api/admin/generation-limits",
            json={"daily_limit": 5, "personal_slots": 2, "instance_slots": 1},
        )
        assert changed.status_code == 200, changed.text
        changed_body = changed.json()
        assert changed_body["policy"]["daily_limit"] == 5
        assert changed_body["effective"] == {
            "daily_limit": 5,
            "personal_slots": 2,
            "instance_slots": 1,
            "project_slots": 1,
        }

        account_view = await admin.get(
            f"/api/admin/accounts/{account['id']}/generation-limits"
        )
        assert account_view.status_code == 200, account_view.text
        account_body = account_view.json()
        assert account_body["effective"]["daily_limit"] == 5
        assert set(account_body) == {"account_id", "email", "policy", "effective", "usage"}
        assert account["id"] not in str(account_body["usage"])

        account_changed = await admin.put(
            f"/api/admin/accounts/{account['id']}/generation-limits",
            json={"daily_limit": 3, "personal_slots": 1},
        )
        assert account_changed.status_code == 200, account_changed.text
        assert account_changed.json()["effective"] == {
            "daily_limit": 3,
            "personal_slots": 1,
            "instance_slots": 1,
            "project_slots": 1,
        }
        reset = await admin.put(
            f"/api/admin/accounts/{account['id']}/generation-limits",
            json={"daily_limit": None, "personal_slots": None},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["policy"] == {
            "daily_limit": None,
            "personal_slots": None,
            "instance_slots": None,
            "updated_at": None,
        }
        assert reset.json()["effective"]["daily_limit"] == 5

        events = await admin.get("/api/admin/security-events")
        assert events.status_code == 200, events.text
        quota_events = [
            item
            for item in events.json()["items"]
            if item["event_type"] == "generation_limits_updated"
        ]
        assert len(quota_events) == 3
        by_scope: dict[str, list[dict[str, Any]]] = {}
        for item in quota_events:
            by_scope.setdefault(item["detail"]["scope"], []).append(item)
        assert set(by_scope) == {"instance", "account"}
        assert len(by_scope["instance"]) == 1
        assert by_scope["instance"][0]["before"]["daily_limit"] is None
        assert by_scope["instance"][0]["after"]["daily_limit"] == 5
        assert len(by_scope["account"]) == 2

        user_client = _client(app)
        await login(user_client, USER_EMAIL, USER_PASSWORD)
        forbidden = await user_client.put(
            "/api/admin/generation-limits", json={"daily_limit": 1}
        )
        assert forbidden.status_code == 403, forbidden.text
        forged = await admin.put(
            "/api/admin/generation-limits",
            json={"daily_limit": 1, "is_admin": True},
        )
        assert forged.status_code == 422, forged.text
        non_positive = await admin.put(
            "/api/admin/generation-limits", json={"daily_limit": 0}
        )
        assert non_positive.status_code == 422, non_positive.text
        assert (await admin.get(
            f"/api/admin/accounts/{await _account_id(app, TEST_ADMIN_EMAIL)}/generation-limits"
        )).status_code == 404
        assert (await admin.get(
            "/api/admin/accounts/missing-account/generation-limits"
        )).status_code == 404
        await user_client.aclose()

        before_failure = (await admin.get("/api/admin/generation-limits")).json()
        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr("app.generation_quota.write_audit", boom)
        with pytest.raises(RuntimeError):
            await admin.put(
                "/api/admin/generation-limits",
                json={"daily_limit": 9, "personal_slots": 1, "instance_slots": 1},
            )
        monkeypatch.undo()
        after_failure = (await admin.get("/api/admin/generation-limits")).json()
        assert after_failure == before_failure
        assert len(await _quota_rows(app)) == 1
        await _user.aclose()


@pytest.mark.asyncio
async def test_rolling_window_lower_bound_is_exclusive(full_settings):
    full_settings.generation_daily_limit = 1
    async with _environment(full_settings, LLMStub(), _visual()) as (app, client):
        setup = await _setup_s06(app, client)
        start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        first = await _accept_direct(app, setup, now=start)
        assert first.replay is False
        await _release_slots(app, first.intent.id)

        just_before = await usage_snapshot(
            app.state.session_factory,
            settings=app.state.settings,
            account_id=first.intent.operator_account_id,
            project_id=setup["project_id"],
            now=start + timedelta(milliseconds=GENERATION_WINDOW_MS - 1),
        )
        assert just_before["used"] == 1
        with pytest.raises(ApiError) as failure:
            await _accept_direct(
                app,
                setup,
                now=start + timedelta(milliseconds=GENERATION_WINDOW_MS - 1),
            )
        assert failure.value.code == "GENERATION_LIMIT_REACHED"

        after_boundary = await _accept_direct(
            app, setup, now=start + timedelta(milliseconds=GENERATION_WINDOW_MS)
        )
        assert after_boundary.replay is False
        snapshot = await usage_snapshot(
            app.state.session_factory,
            settings=app.state.settings,
            account_id=after_boundary.intent.operator_account_id,
            project_id=setup["project_id"],
            now=start + timedelta(milliseconds=GENERATION_WINDOW_MS),
        )
        assert snapshot["used"] == 1
        assert snapshot["remaining"] == 0
        assert snapshot["window_started_at"] == format_timestamp(
            start + timedelta(milliseconds=GENERATION_WINDOW_MS)
            - timedelta(milliseconds=GENERATION_WINDOW_MS)
        )
        assert snapshot["window_expires_at"] == format_timestamp(
            start + timedelta(milliseconds=GENERATION_WINDOW_MS)
        )

        midnight = datetime(2026, 1, 1, 23, 59, 30, tzinfo=UTC)
        midnight_usage = await usage_snapshot(
            app.state.session_factory,
            settings=app.state.settings,
            account_id=after_boundary.intent.operator_account_id,
            project_id=setup["project_id"],
            now=midnight,
        )
        assert midnight_usage["used"] == 1


@pytest.mark.asyncio
async def test_refresh_restart_and_disable_do_not_reset_usage(full_settings):
    async with _environment(full_settings, LLMStub(), _visual()) as (app, admin):
        account, user = await _user_client(app, admin)
        setup = await _setup_s06(app, user)
        accepted = await _accept_direct(app, setup, now=app.state.clock.now())

        first_view = await user.get(f"/api/projects/{setup['project_id']}/workflow")
        assert first_view.status_code == 200, first_view.text
        assert first_view.json()["usage"]["used"] == 1
        refreshed = await user.get(f"/api/projects/{setup['project_id']}/workflow")
        assert refreshed.json()["usage"]["used"] == 1

        restart_registry = build_test_registry(llm=LLMStub(), visual=_visual())
        restarted = create_app(settings=full_settings, registry=restart_registry)
        async with restarted.router.lifespan_context(restarted):
            restarted_snapshot = await usage_snapshot(
                restarted.state.session_factory,
                settings=full_settings,
                account_id=accepted.intent.operator_account_id,
                project_id=setup["project_id"],
            )
            assert restarted_snapshot["used"] == 1

        disabled = await admin.post(f"/api/admin/accounts/{account['id']}/disable")
        assert disabled.status_code == 200, disabled.text
        async with read_session(app.state.session_factory) as session:
            remain = list(
                (
                    await session.scalars(
                        select(GenerationUsage).where(
                            GenerationUsage.account_id == account["id"]
                        )
                    )
                ).all()
            )
            assert len(remain) == 1
        assert (await user.get(f"/api/projects/{setup['project_id']}/workflow")).status_code == 401
        restored = await admin.post(f"/api/admin/accounts/{account['id']}/restore")
        assert restored.status_code == 200, restored.text
        again = _client(app)
        await login(again, USER_EMAIL, USER_PASSWORD)
        view = await again.get(f"/api/projects/{setup['project_id']}/workflow")
        assert view.status_code == 200, view.text
        assert view.json()["usage"]["used"] == 1
        await again.aclose()
        await user.aclose()


@pytest.mark.asyncio
async def test_effective_limits_reject_before_count_and_lowering_keeps_existing(
    full_settings,
):
    llm = LLMStub()
    llm.gate = asyncio.Event()
    async with _environment(full_settings, llm, _visual()) as (app, admin):
        account, user = await _user_client(app, admin)
        await admin.put(
            f"/api/admin/accounts/{account['id']}/generation-limits",
            json={"daily_limit": 2, "personal_slots": 1},
        )
        first = await _setup_s06(app, user)
        second = await _setup_s06(app, user)
        first_confirm = await user.post(
            f"/api/projects/{first['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": first["revision"],
                "payload": {
                    "artifact_id": first["final_artifact_id"],
                    "artifact_digest": first["final_digest"],
                    "requirement_revision": first["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert first_confirm.status_code == 200, first_confirm.text
        first_view = await user.get(f"/api/projects/{first['project_id']}/workflow")
        assert first_view.json()["usage"]["used"] == 1

        lowered = await admin.put(
            f"/api/admin/accounts/{account['id']}/generation-limits",
            json={"daily_limit": 1, "personal_slots": 1},
        )
        assert lowered.status_code == 200, lowered.text
        view = await user.get(f"/api/projects/{first['project_id']}/workflow")
        assert view.json()["usage"]["used"] == 1
        assert view.json()["usage"]["daily_limit"] == 1
        assert view.json()["usage"]["remaining"] == 0
        assert view.json()["intent"]["status"] == "accepted"
        intents, usages, slots = await _rows_for_project(app, first["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert sum(1 for slot in slots if slot.released_at is None) == 3

        calls_before = len(llm.role_calls_received)
        second_confirm = await user.post(
            f"/api/projects/{second['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": second["revision"],
                "payload": {
                    "artifact_id": second["final_artifact_id"],
                    "artifact_digest": second["final_digest"],
                    "requirement_revision": second["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert second_confirm.status_code == 429, second_confirm.text
        assert second_confirm.json()["error"]["code"] == "GENERATION_LIMIT_REACHED"
        await asyncio.sleep(0.05)
        assert len(llm.role_calls_received) == calls_before
        intents2, usages2, slots2 = await _rows_for_project(app, second["project_id"])
        assert intents2 == [] and usages2 == [] and slots2 == []
        async with read_session(app.state.session_factory) as session:
            project = await session.get(Project, second["project_id"])
            assert project is not None and project.brief_status != "confirmed"

        llm.gate.set()
        await _drain(app)
        await user.aclose()


@pytest.mark.asyncio
async def test_admin_creation_is_not_exempt_and_instance_slot_competes(full_settings):
    llm = LLMStub()
    llm.gate = asyncio.Event()
    async with _environment(full_settings, llm, _visual()) as (app, admin):
        await admin.put(
            "/api/admin/generation-limits",
            json={"daily_limit": 1, "personal_slots": 1, "instance_slots": 1},
        )
        admin_first = await _setup_s06(app, admin)
        admin_second = await _setup_s06(app, admin)
        first = await admin.post(
            f"/api/projects/{admin_first['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": admin_first["revision"],
                "payload": {
                    "artifact_id": admin_first["final_artifact_id"],
                    "artifact_digest": admin_first["final_digest"],
                    "requirement_revision": admin_first["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert first.status_code == 200, first.text
        second = await admin.post(
            f"/api/projects/{admin_second['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": admin_second["revision"],
                "payload": {
                    "artifact_id": admin_second["final_artifact_id"],
                    "artifact_digest": admin_second["final_digest"],
                    "requirement_revision": admin_second["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert second.status_code == 429, second.text
        assert second.json()["error"]["code"] == "GENERATION_LIMIT_REACHED"

        account, user = await _user_client(app, admin)
        await admin.put(
            f"/api/admin/accounts/{account['id']}/generation-limits",
            json={"daily_limit": 20, "personal_slots": 1},
        )
        user_project = await _setup_s06(app, user)
        blocked = await user.post(
            f"/api/projects/{user_project['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": user_project["revision"],
                "payload": {
                    "artifact_id": user_project["final_artifact_id"],
                    "artifact_digest": user_project["final_digest"],
                    "requirement_revision": user_project["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert blocked.status_code == 429, blocked.text
        assert blocked.json()["error"]["code"] == "GENERATION_SLOT_BUSY"
        intents, usages, slots = await _rows_for_project(app, user_project["project_id"])
        assert intents == [] and usages == [] and slots == []

        llm.gate.set()
        await _drain(app)
        await user.aclose()

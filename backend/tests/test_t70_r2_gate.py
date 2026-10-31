from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from app.auth.models import AuditEvent
from app.config import Settings
from app.db import build_session_factory, create_engine, read_session, write_transaction
from app.errors import ApiError
from app.models import Project
from app.workflow.tables import Approval, Artifact, WorkflowRun
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import _drain, _environment, _responder, _setup_s06
from tests.test_v2_t40_prompt_review import png_bytes


def _visual() -> VisualStub:
    return VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )


async def _load_target(session: AsyncSession, seed: dict[str, Any]):
    project = await session.get(Project, seed["project_id"])
    run = await session.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.project_id == seed["project_id"])
        .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
    )
    artifact = await session.get(Artifact, seed["final_artifact_id"])
    assert project is not None and run is not None and artifact is not None
    return project, run, artifact


async def _accept_via(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    seed: dict[str, Any],
    request_id: str,
    *,
    now: datetime | None = None,
):
    async with write_transaction(factory) as session:
        project, run, artifact = await _load_target(session, seed)
        return await accept_in_session(
            session,
            settings=settings,
            project=project,
            run=run,
            operator_account_id=project.owner_account_id or "",
            artifact=artifact,
            requirement_revision=seed["revision"],
            reference_usage="none",
            image_size=seed["brief"]["size"],
            request_id=request_id,
            now=now,
        )


async def _attempt(
    factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    seed: dict[str, Any],
    request_id: str,
    *,
    now: datetime | None = None,
) -> tuple[str, Any]:
    try:
        result = await _accept_via(factory, settings, seed, request_id, now=now)
    except ApiError as exc:
        return "error", exc.code
    return "ok", result


async def _independent_attempts(
    app,
    seeds: list[dict[str, Any]],
    request_ids: list[str],
    *,
    nows: list[datetime | None] | None = None,
):
    settings = app.state.settings
    engines = [create_engine(settings) for _ in seeds]
    factories = [build_session_factory(engine) for engine in engines]
    for engine in engines:
        async with engine.connect() as connection:
            await connection.exec_driver_sql("SELECT 1")
    try:
        return await asyncio.gather(
            *[
                _attempt(
                    factory,
                    settings,
                    seed,
                    request_id,
                    now=None if nows is None else nows[index],
                )
                for index, (factory, seed, request_id) in enumerate(
                    zip(factories, seeds, request_ids, strict=True)
                )
            ]
        )
    finally:
        for engine in engines:
            await engine.dispose()


async def _rows(app, project_id: str):
    async with read_session(app.state.session_factory) as session:
        intents = list(
            (
                await session.scalars(
                    select(GenerationIntent)
                    .where(GenerationIntent.project_id == project_id)
                    .order_by(GenerationIntent.created_at, GenerationIntent.id)
                )
            ).all()
        )
        usages = list(
            (
                await session.scalars(
                    select(GenerationUsage)
                    .where(GenerationUsage.project_id == project_id)
                    .order_by(GenerationUsage.created_at, GenerationUsage.id)
                )
            ).all()
        )
        slots = list(
            (
                await session.scalars(
                    select(GenerationSlot)
                    .where(
                        GenerationSlot.intent_id.in_(
                            [intent.id for intent in intents]
                        )
                    )
                    .order_by(GenerationSlot.created_at, GenerationSlot.id)
                )
            ).all()
        )
        return intents, usages, slots


async def _approval_count(app, project_id: str, approval_type: str = "requirements_card") -> int:
    async with read_session(app.state.session_factory) as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(Approval)
                .where(
                    Approval.project_id == project_id,
                    Approval.approval_type == approval_type,
                )
            )
            or 0
        )


async def _audit_count(app, event_type: str) -> int:
    async with read_session(app.state.session_factory) as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == event_type)
            )
            or 0
        )


async def _accept_with_flush_fault(
    app, seed: dict[str, Any], request_id: str, *, fail_after: int
):
    state = {"flushes": 0}

    async with write_transaction(app.state.session_factory) as session:
        def fail_on_flush(_session, _context) -> None:
            state["flushes"] += 1
            if state["flushes"] == fail_after:
                raise RuntimeError("injected flush failure")

        event.listen(session.sync_session, "after_flush", fail_on_flush)
        try:
            project, run, artifact = await _load_target(session, seed)
            return await accept_in_session(
                session,
                settings=app.state.settings,
                project=project,
                run=run,
                operator_account_id=project.owner_account_id or "",
                artifact=artifact,
                requirement_revision=seed["revision"],
                reference_usage="none",
                image_size=seed["brief"]["size"],
                request_id=request_id,
            )
        finally:
            event.remove(session.sync_session, "after_flush", fail_on_flush)


@pytest.mark.asyncio
async def test_each_acceptance_write_fault_rolls_back_intent_usage_and_slots(
    full_settings, monkeypatch
):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual) as (app, client):
        seeds = [await _setup_s06(app, client) for _ in range(3)]
        failure_modes = ["after-intent", "after-usage-slots", "after-audit"]
        for seed, mode in zip(seeds, failure_modes, strict=True):
            if mode == "after-intent":
                with pytest.raises(RuntimeError):
                    await _accept_with_flush_fault(
                        app, seed, str(uuid.uuid4()), fail_after=1
                    )
            elif mode == "after-usage-slots":
                with pytest.raises(RuntimeError):
                    await _accept_with_flush_fault(
                        app, seed, str(uuid.uuid4()), fail_after=2
                    )
            elif mode == "after-audit":
                with pytest.raises(RuntimeError):
                    await _accept_with_flush_fault(
                        app, seed, str(uuid.uuid4()), fail_after=3
                    )
            intents, usages, slots = await _rows(app, seed["project_id"])
            assert intents == []
            assert usages == []
            assert slots == []

        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("audit unavailable")

        seed = seeds[0]
        with monkeypatch.context() as patch:
            patch.setattr("app.acceptance.write_audit", boom)
            with pytest.raises(RuntimeError):
                await _accept_via(
                    app.state.session_factory,
                    app.state.settings,
                    seed,
                    str(uuid.uuid4()),
                )
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert intents == []
        assert usages == []
        assert slots == []
        assert await _audit_count(app, "generation_intent_accepted") == 0


@pytest.mark.asyncio
async def test_confirmation_audit_failure_leaves_no_confirmation_or_authorization(
    full_settings, monkeypatch
):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual) as (app, client):
        seed = await _setup_s06(app, client)
        assert await _approval_count(app, seed["project_id"]) == 0
        assert await _artifact_count(app, seed["project_id"], "prompt_plan") == 0

        def boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("audit unavailable")

        with monkeypatch.context() as patch:
            patch.setattr("app.acceptance.write_audit", boom)
            with pytest.raises(RuntimeError):
                await client.post(
                    f"/api/projects/{seed['project_id']}/workflow/actions/confirm_requirements",
                    json={
                        "expected_revision": seed["revision"],
                        "payload": {
                            "artifact_id": seed["final_artifact_id"],
                            "artifact_digest": seed["final_digest"],
                            "requirement_revision": seed["revision"],
                            "request_id": str(uuid.uuid4()),
                        },
                    },
                )

        intents, usages, slots = await _rows(app, seed["project_id"])
        assert intents == []
        assert usages == []
        assert slots == []
        assert await _approval_count(app, seed["project_id"]) == 0
        assert await _audit_count(app, "generation_intent_accepted") == 0
        assert llm.role_calls_received == []
        assert visual.submit_calls == []
        async with read_session(app.state.session_factory) as session:
            project = await session.get(Project, seed["project_id"])
            run = await session.scalar(
                select(WorkflowRun)
                .where(WorkflowRun.project_id == seed["project_id"])
                .order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc())
            )
            assert project is not None and project.brief_status == "review"
            assert run is not None and run.stage == "S06" and run.status == "active"


async def _artifact_count(app, project_id: str, kind: str) -> int:
    async with read_session(app.state.session_factory) as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(Artifact)
                .where(Artifact.project_id == project_id, Artifact.kind == kind)
            )
            or 0
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["personal", "instance", "project"])
async def test_independent_connections_guard_personal_instance_and_project_slots(
    full_settings, scope
):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual) as (app, client):
        first = await _setup_s06(app, client)
        second = await _setup_s06(app, client) if scope != "project" else first
        settings = app.state.settings
        settings.generation_daily_limit = 20
        if scope == "personal":
            settings.generation_personal_slots = 1
            settings.generation_instance_slots = 2
        elif scope == "instance":
            settings.generation_personal_slots = 2
            settings.generation_instance_slots = 1
        else:
            settings.generation_personal_slots = 2
            settings.generation_instance_slots = 2
        settings.generation_project_slots = 1

        seeds = [first, second]
        request_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
        results = await _independent_attempts(app, seeds, request_ids)
        assert sorted(result[0] for result in results) == ["error", "ok"]
        error = next(result for result in results if result[0] == "error")
        assert error[1] == "GENERATION_SLOT_BUSY"
        intents, usages, slots = [], [], []
        for candidate in {first["project_id"], second["project_id"]}:
            project_intents, project_usages, project_slots = await _rows(app, candidate)
            intents.extend(project_intents)
            usages.extend(project_usages)
            slots.extend(project_slots)
        assert len(intents) == 1
        assert len(usages) == 1
        assert len(slots) == 3
        assert all(slot.released_at is None for slot in slots)
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_concurrent_lost_response_replay_creates_one_intent_and_one_count(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual) as (app, client):
        seed = await _setup_s06(app, client)
        request_id = str(uuid.uuid4())
        results = await _independent_attempts(app, [seed, seed], [request_id, request_id])
        assert all(result[0] == "ok" for result in results)
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert len(intents) == 1
        assert len(usages) == 1
        assert len(slots) == 3
        assert len({result[1].intent.id for result in results}) == 1
        assert sorted(result[1].replay for result in results) == [False, True]
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_exact_rolling_window_boundary_is_utc_millisecond_exclusive(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual) as (app, client):
        seed = await _setup_s06(app, client)
        t0 = datetime(2026, 1, 3, 4, 5, 6, tzinfo=UTC)
        first = await _accept_via(
            app.state.session_factory,
            app.state.settings,
            seed,
            str(uuid.uuid4()),
            now=t0,
        )
        async with read_session(app.state.session_factory) as session:
            project = await session.get(Project, seed["project_id"])
            assert project is not None and project.owner_account_id is not None
            account_id = project.owner_account_id
        async with write_transaction(app.state.session_factory) as session:
            await release_slots_in_session(
                session,
                intent_id=first.intent.id,
                reason="window-test",
                now=t0,
            )
        boundary = t0 + timedelta(milliseconds=GENERATION_WINDOW_MS)
        inside = boundary - timedelta(milliseconds=1)
        before = await usage_snapshot(
            app.state.session_factory,
            settings=app.state.settings,
            account_id=account_id,
            project_id=seed["project_id"],
            now=inside,
        )
        at_boundary = await usage_snapshot(
            app.state.session_factory,
            settings=app.state.settings,
            account_id=account_id,
            project_id=seed["project_id"],
            now=boundary,
        )
        assert before["used"] == 1
        assert at_boundary["used"] == 0

        second_results = await _independent_attempts(
            app,
            [seed],
            [str(uuid.uuid4())],
            nows=[boundary],
        )
        assert second_results[0][0] == "ok", second_results
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert len(intents) == 2
        assert len(usages) == 2
        assert len(slots) == 6
        assert len([slot for slot in slots if slot.released_at is not None]) == 3
        after = await usage_snapshot(
            app.state.session_factory,
            settings=app.state.settings,
            account_id=account_id,
            project_id=seed["project_id"],
            now=boundary,
        )
        assert after["used"] == 1


@pytest.mark.asyncio
async def test_prompt_failure_keeps_one_count_and_slot_without_visual_call(full_settings):
    def failing_responder(request):
        role = request.payload["task"]["role"]
        if role == "generation_engineer":
            raise ApiError(503, "SERVICE_UNAVAILABLE", "模型暂不可用")
        return _responder(request)

    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual, failing_responder) as (app, client):
        seed = await _setup_s06(app, client)
        response = await client.post(
            f"/api/projects/{seed['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": seed["revision"],
                "payload": {
                    "artifact_id": seed["final_artifact_id"],
                    "artifact_digest": seed["final_digest"],
                    "requirement_revision": seed["revision"],
                    "request_id": str(uuid.uuid4()),
                },
            },
        )
        assert response.status_code == 200, response.text
        await _drain(app)
        view = (await client.get(f"/api/projects/{seed['project_id']}/workflow")).json()
        assert view["stage"] == "S07"
        assert view["status"] == "failed"
        assert len(llm.role_calls_received) == 1
        assert visual.submit_calls == []
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert len(intents) == 1
        assert len(usages) == 1
        assert len(slots) == 3
        assert all(slot.released_at is None for slot in slots)
        calls_before = len(llm.role_calls_received)
        for _ in range(3):
            refresh = await client.get(f"/api/projects/{seed['project_id']}/workflow")
            assert refresh.status_code == 200
        assert len(llm.role_calls_received) == calls_before
        assert visual.submit_calls == []


@pytest.mark.asyncio
async def test_unknown_closure_and_new_task_have_exact_counts_and_calls(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="unknown", error_code="GATE_TIMEOUT")
    )
    async with _environment(full_settings, llm, visual, _responder) as (app, client):
        seed = await _setup_s06(app, client)
        first_request = str(uuid.uuid4())
        response = await client.post(
            f"/api/projects/{seed['project_id']}/workflow/actions/confirm_requirements",
            json={
                "expected_revision": seed["revision"],
                "payload": {
                    "artifact_id": seed["final_artifact_id"],
                    "artifact_digest": seed["final_digest"],
                    "requirement_revision": seed["revision"],
                    "request_id": first_request,
                },
            },
        )
        assert response.status_code == 200, response.text
        await _drain(app)
        view = (await client.get(f"/api/projects/{seed['project_id']}/workflow")).json()
        assert view["stage"] == "S08"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "GENERATION_UNKNOWN"
        assert len(visual.submit_calls) == 1
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert len(intents) == 1 and len(usages) == 1 and len(slots) == 3
        assert all(slot.released_at is None for slot in slots)
        role_calls = len(llm.role_calls_received)

        for _ in range(3):
            assert (
                await client.get(f"/api/projects/{seed['project_id']}/workflow")
            ).status_code == 200
        assert len(llm.role_calls_received) == role_calls
        assert len(visual.submit_calls) == 1

        verify = await client.post(
            f"/api/projects/{seed['project_id']}/workflow/actions/verify_generation",
            json={"expected_revision": seed["revision"], "payload": {}},
        )
        assert verify.status_code == 200, verify.text
        assert len(visual.submit_calls) == 1
        assert len(llm.role_calls_received) == role_calls

        closed = await client.post(
            f"/api/projects/{seed['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": seed["revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert closed.status_code == 200, closed.text
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert intents[0].status == "closed"
        assert len(usages) == 1
        assert all(slot.released_at is not None for slot in slots)
        assert await _audit_count(app, "generation_tracking_closed") == 1

        started = await client.post(
            f"/api/projects/{seed['project_id']}/workflow/actions/start_new_generation",
            json={
                "expected_revision": seed["revision"],
                "payload": {"acknowledge_risk": True},
            },
        )
        assert started.status_code == 200, started.text
        await _drain(app)
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert len(intents) == 2
        assert len(usages) == 2
        assert len(slots) == 6
        assert len([slot for slot in slots if slot.released_at is not None]) == 3
        assert len(visual.submit_calls) == 2
        assert len(llm.role_calls_received) == role_calls


@pytest.mark.asyncio
async def test_legacy_confirmation_and_generation_entries_are_closed(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual) as (app, client):
        seed = await _setup_s06(app, client)
        legacy_confirm = await client.post(
            f"/api/projects/{seed['project_id']}/confirm",
            json={"revision": seed["revision"]},
        )
        assert legacy_confirm.status_code == 409
        assert legacy_confirm.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"
        legacy_generation = await client.post(
            f"/api/projects/{seed['project_id']}/generations",
            json={"request_id": str(uuid.uuid4()), "confirmed_revision": seed["revision"]},
        )
        assert legacy_generation.status_code == 409
        assert legacy_generation.json()["error"]["code"] == "LEGACY_FLOW_DISABLED"
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert intents == [] and usages == [] and slots == []


@pytest.mark.asyncio
async def test_ownership_and_database_failures_are_safe_and_recoverable(full_settings):
    llm = LLMStub()
    visual = _visual()
    async with _environment(full_settings, llm, visual) as (app, client):
        seed = await _setup_s06(app, client)
        async with write_transaction(app.state.session_factory) as session:
            project, run, artifact = await _load_target(session, seed)
            with pytest.raises(ApiError) as owned:
                await accept_in_session(
                    session,
                    settings=app.state.settings,
                    project=project,
                    run=run,
                    operator_account_id="other-account",
                    artifact=artifact,
                    requirement_revision=seed["revision"],
                    reference_usage="none",
                    image_size=seed["brief"]["size"],
                    request_id=str(uuid.uuid4()),
                )
            assert owned.value.code == "OWNERSHIP_MISMATCH"
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert intents == [] and usages == [] and slots == []

        engine = create_engine(app.state.settings)
        connection = await engine.connect()
        await connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            with pytest.raises(OperationalError):
                await _accept_via(
                    app.state.session_factory,
                    app.state.settings,
                    seed,
                    str(uuid.uuid4()),
                )
        finally:
            await connection.rollback()
            await connection.close()
            await engine.dispose()
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert intents == [] and usages == [] and slots == []

        result = await _accept_via(
            app.state.session_factory,
            app.state.settings,
            seed,
            str(uuid.uuid4()),
        )
        assert result.replay is False
        intents, usages, slots = await _rows(app, seed["project_id"])
        assert len(intents) == 1 and len(usages) == 1 and len(slots) == 3


def test_default_visual_size_is_the_single_v3_dimension():
    assert Settings.model_fields["visual_image_size"].default == "3840x2160"

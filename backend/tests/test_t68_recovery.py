from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.adapters.base import ProviderOutcome
from app.db import write_transaction
from app.errors import ApiError
from app.services.recovery import repair_leftover_state
from app.workflow.stages import s08 as s08_module
from app.workflow.tables import GenerationJob
from tests.support.adapters import LLMStub, VisualStub
from tests.test_t65_generation_cycle import (
    _delivery_response,
    _drain,
    _environment,
    _quality_response,
    _review_response,
    _rows,
    _setup_s06,
)
from tests.test_v2_t40_prompt_review import candidate_data, engineer_response, png_bytes


def _normal_responder(request):
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


async def _job(app, project_id: str) -> GenerationJob:
    async with app.state.session_factory() as session:
        job = await session.scalar(
            select(GenerationJob)
            .where(GenerationJob.project_id == project_id)
            .order_by(GenerationJob.created_at.desc())
            .limit(1)
        )
        assert job is not None
        return job


@pytest.mark.asyncio
async def test_prompt_failure_keeps_true_state_count_and_slot(full_settings):
    failure = ApiError(503, "SERVICE_UNAVAILABLE", "模型暂不可用")

    def responder(request):
        if request.payload["task"]["role"] == "generation_engineer":
            raise failure
        return _normal_responder(request)

    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm, visual, responder) as (app, client):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)

        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S07"
        assert view["status"] == "failed"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert intents[0].status in {"accepted", "executing"}
        assert len(usages) == 1
        assert all(slot.released_at is None for slot in slots)
        assert visual.submit_calls == []
        assert len(llm.role_calls_received) == 1


@pytest.mark.asyncio
async def test_unknown_verify_is_read_only_and_recover_polls_original(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-68",
            result_locator="https://cdn.invalid/68.png",
        ),
        poll_outcomes=[
            ProviderOutcome(
                state="unknown",
                remote_task_id="remote-68",
                error_code="VISUAL_OUTCOME_UNKNOWN",
            )
        ],
    )
    async with _environment(full_settings, llm, visual, _normal_responder) as (app, client):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)

        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S08"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "GENERATION_UNKNOWN"
        assert {item["id"] for item in view["available_actions"]} == {
            "verify_generation",
            "recover_generation",
            "close_generation_tracking",
        }
        assert visual.poll_calls == ["remote-68"]
        calls_before = (len(llm.role_calls_received), len(visual.submit_calls))
        verify = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/verify_generation",
            json={"expected_revision": view["project_revision"]},
        )
        assert verify.status_code == 200, verify.text
        assert (len(llm.role_calls_received), len(visual.submit_calls)) == calls_before
        assert visual.poll_calls == ["remote-68"]
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert all(slot.released_at is None for slot in slots)

        visual.poll_outcomes = [
            ProviderOutcome(
                state="succeeded",
                remote_task_id="remote-68",
                image_bytes=png_bytes(1024, 1024),
                image_count=1,
            )
        ]
        recovered = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/recover_generation",
            json={"expected_revision": view["project_revision"]},
        )
        assert recovered.status_code == 200, recovered.text
        await _drain(app)
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S10"
        assert view["status"] == "needs_input"
        assert len(visual.submit_calls) == 1
        assert visual.poll_calls == ["remote-68", "remote-68"]
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert intents[0].status == "executing"
        assert all(slot.released_at is None for slot in slots)


@pytest.mark.asyncio
async def test_recover_unknown_marks_job_unknown_and_closed_failed_task_can_start_new(
    full_settings,
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="failed",
            evidence="result_available",
            error_code="RESULT_FETCH_FAILED",
        )
    )
    async with _environment(full_settings, llm, visual, _normal_responder) as (app, client):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)

        awaiting = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert awaiting["stage"] == "S07"
        assert awaiting["prompt_preview"] is not None
        from tests.support.t02_prompt import final_click

        first_final = await final_click(
            client, {"project_id": setup["project_id"], "workflow": awaiting}
        )
        assert first_final.status_code == 200, first_final.text
        await _drain(app)

        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["issue"]["code"] == "RESULT_RETRIEVAL_PENDING"
        job = await _job(app, setup["project_id"])
        assert job.status == "failed"

        recovered = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/recover_generation",
            json={"expected_revision": view["project_revision"]},
        )
        assert recovered.status_code == 200, recovered.text
        job = await _job(app, setup["project_id"])
        assert job.status == "unknown"
        assert job.error_code == "VISUAL_OUTCOME_UNKNOWN"
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["issue"]["code"] == "GENERATION_UNKNOWN"

        closed = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/close_generation_tracking",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_local_close": True},
            },
        )
        assert closed.status_code == 200, closed.text

        # 模拟旧版本 recover_generation 留下的状态：授权已关闭但 job 仍是 failed。
        # 显式确认风险后必须仍能发起新任务，不能永久卡在 LOCAL_TRACKING_CLOSED。
        async with write_transaction(app.state.session_factory) as session:
            stored = await session.get(GenerationJob, job.id)
            assert stored is not None
            stored.status = "failed"
            await session.flush()

        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["issue"]["code"] == "LOCAL_TRACKING_CLOSED"
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
        awaiting = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert awaiting["stage"] == "S07"
        assert awaiting["status"] == "needs_input"
        assert awaiting["prompt_preview"] is not None
        # A new generation after a closed unknown task is still a new
        # preparation intent; it waits for an explicit final confirmation.
        assert len(visual.submit_calls) == 1
        from tests.support.t02_prompt import final_click

        final = await final_click(
            client,
            {"project_id": setup["project_id"], "workflow": awaiting},
            command_id=str(uuid.uuid4()),
        )
        assert final.status_code == 200, final.text
        await _drain(app)
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S10"
        intents, usages, _ = await _rows(app, setup["project_id"])
        assert len(intents) == 2
        assert len(usages) == 2


@pytest.mark.asyncio
async def test_result_fetch_failure_keeps_slot_and_recover_fetches_without_submit(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="failed",
            evidence="result_available",
            error_code="RESULT_FETCH_FAILED",
            result_locator="https://cdn.invalid/68.png",
        ),
        fetch_outcome=ProviderOutcome(
            state="succeeded",
            image_bytes=png_bytes(1024, 1024),
            image_count=1,
        ),
    )
    async with _environment(full_settings, llm, visual, _normal_responder) as (app, client):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)

        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "RESULT_RETRIEVAL_PENDING"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert intents[0].status == "executing"
        assert len(usages) == 1
        assert all(slot.released_at is None for slot in slots)

        recovered = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/recover_generation",
            json={"expected_revision": view["project_revision"]},
        )
        assert recovered.status_code == 200, recovered.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        assert visual.fetch_calls == ["https://cdn.invalid/68.png"]
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S10"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1


@pytest.mark.asyncio
async def test_process_interrupted_resume_reuses_intent_without_new_count(
    full_settings, monkeypatch
):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    original_schedule = s08_module._schedule
    async with _environment(full_settings, llm, visual, _normal_responder) as (app, client):
        monkeypatch.setattr(s08_module, "_schedule", lambda *args, **kwargs: None)
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        monkeypatch.setattr(s08_module, "_schedule", original_schedule)

        job = await _job(app, setup["project_id"])
        assert job.status == "submit_pending"
        assert visual.submit_calls == []

        await repair_leftover_state(app)
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "PROCESS_INTERRUPTED"
        assert {item["id"] for item in view["available_actions"]} == {
            "verify_generation",
            "recover_generation",
        }
        assert visual.submit_calls == []
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert all(slot.released_at is None for slot in slots)

        recovered = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/recover_generation",
            json={"expected_revision": view["project_revision"]},
        )
        assert recovered.status_code == 200, recovered.text
        await _drain(app)
        assert len(visual.submit_calls) == 1
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert intents[0].status == "executing"
        assert len(usages) == 1
        assert all(slot.released_at is None for slot in slots)


@pytest.mark.asyncio
async def test_terminal_provider_failure_counts_once_and_releases_slots(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="failed",
            evidence="rejected",
            error_code="VISUAL_TASK_FAILED",
            error_message="供应商明确拒绝",
        )
    )
    async with _environment(full_settings, llm, visual, _normal_responder) as (app, client):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "GENERATION_FAILED"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1
        assert intents[0].status == "failed"
        assert len(usages) == 1
        assert all(slot.released_at is not None for slot in slots)

@pytest.mark.asyncio
async def test_terminal_failure_can_retry_as_a_new_confirmed_attempt(full_settings):
    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="failed",
            evidence="rejected",
            error_code="VISUAL_TASK_FAILED",
            error_message="供应商明确拒绝",
        )
    )
    async with _environment(full_settings, llm, visual, _normal_responder) as (app, client):
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)

        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["issue"]["code"] == "GENERATION_FAILED"
        assert {item["id"] for item in view["available_actions"]} == {"retry_generation"}

        visual.submit_outcome = ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
        retried = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/retry_generation",
            json={
                "expected_revision": view["project_revision"],
                "payload": {"acknowledge_new_attempt": True},
            },
        )
        assert retried.status_code == 200, retried.text
        await _drain(app)

        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S10"
        assert view["status"] == "needs_input"
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 2
        assert intents[0].status == "failed"
        assert intents[1].status == "executing"
        assert len(usages) == 2
        assert len(slots) == 6
        assert len([slot for slot in slots if slot.released_at is not None]) == 3
        assert len(visual.submit_calls) == 2


@pytest.mark.asyncio
async def test_independent_process_crash_preserves_count_and_restarts_without_call(
    full_settings, monkeypatch
):
    import os
    import subprocess
    import sys

    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(state="pending", remote_task_id="remote-68")
    )
    original_schedule = s08_module._schedule
    async with _environment(full_settings, llm, visual, _normal_responder) as (app, client):
        monkeypatch.setattr(s08_module, "_schedule", lambda *args, **kwargs: None)
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        monkeypatch.setattr(s08_module, "_schedule", original_schedule)
        job = await _job(app, setup["project_id"])
        assert job.status == "submit_pending" and job.generation_id is not None
        generation_id = job.generation_id

    code = (
        "import os, sqlite3, sys\n"
        "db = os.path.join(os.environ['APP_DATA_DIR'], 'app.db')\n"
        "conn = sqlite3.connect(db)\n"
        "conn.execute(\"UPDATE generation_jobs SET status='submitted', submit_attempted=1, "
        "remote_task_id='remote-crash-68' WHERE id=?\", (sys.argv[1],))\n"
        "conn.execute(\"UPDATE generations SET status='running', stage='waiting_remote', "
        "submit_attempted=1, remote_task_id='remote-crash-68' WHERE id=?\", (sys.argv[2],))\n"
        "conn.commit(); conn.close(); os._exit(9)\n"
    )
    env = dict(os.environ)
    env["APP_DATA_DIR"] = str(full_settings.data_dir)
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", code, job.id, generation_id],
        env=env,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 9

    llm2 = LLMStub()
    visual2 = VisualStub(
        poll_outcomes=[
            ProviderOutcome(
                state="succeeded",
                remote_task_id="remote-crash-68",
                image_bytes=png_bytes(1024, 1024),
                image_count=1,
            )
        ]
    )
    async with _environment(full_settings, llm2, visual2, _normal_responder) as (app, client):
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S08"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "GENERATION_UNKNOWN"
        assert visual2.submit_calls == []
        assert visual2.poll_calls == []
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert all(slot.released_at is None for slot in slots)

        recovered = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/recover_generation",
            json={"expected_revision": view["project_revision"]},
        )
        assert recovered.status_code == 200, recovered.text
        await _drain(app)
        assert visual2.submit_calls == []
        assert visual2.poll_calls == ["remote-crash-68"]
        intents, usages, _ = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1

@pytest.mark.asyncio
async def test_independent_process_crash_before_schedule_resumes_original_intent(
    full_settings, monkeypatch
):
    import os
    import subprocess
    import sys

    llm = LLMStub()
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    original_schedule = s08_module._schedule
    async with _environment(full_settings, llm, visual, _normal_responder) as (app, client):
        monkeypatch.setattr(s08_module, "_schedule", lambda *args, **kwargs: None)
        setup = await _setup_s06(app, client)
        response = await _confirm(client, setup, str(uuid.uuid4()))
        assert response.status_code == 200, response.text
        await _drain(app)
        monkeypatch.setattr(s08_module, "_schedule", original_schedule)
        job = await _job(app, setup["project_id"])
        assert job.status == "submit_pending" and job.generation_id is not None
        job_id = job.id

    code = (
        "import os, sqlite3, sys\n"
        "db = os.path.join(os.environ['APP_DATA_DIR'], 'app.db')\n"
        "conn = sqlite3.connect(db)\n"
        "assert conn.execute('select count(*) from generation_jobs where id=?', "
        "(sys.argv[1],)).fetchone()[0] == 1\n"
        "conn.close(); os._exit(9)\n"
    )
    env = dict(os.environ)
    env["APP_DATA_DIR"] = str(full_settings.data_dir)
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", code, job_id],
        env=env,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 9

    llm2 = LLMStub()
    visual2 = VisualStub(
        submit_outcome=ProviderOutcome(
            state="succeeded", image_bytes=png_bytes(1024, 1024), image_count=1
        )
    )
    async with _environment(full_settings, llm2, visual2, _normal_responder) as (app, client):
        view = (
            await client.get(f"/api/projects/{setup['project_id']}/workflow")
        ).json()
        assert view["stage"] == "S08"
        assert view["status"] == "blocked"
        assert view["issue"]["code"] == "PROCESS_INTERRUPTED"
        assert visual2.submit_calls == []
        intents, usages, slots = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert all(slot.released_at is None for slot in slots)

        recovered = await client.post(
            f"/api/projects/{setup['project_id']}/workflow/actions/recover_generation",
            json={"expected_revision": view["project_revision"]},
        )
        assert recovered.status_code == 200, recovered.text
        await _drain(app)
        assert len(visual2.submit_calls) == 1
        intents, usages, _ = await _rows(app, setup["project_id"])
        assert len(intents) == 1 and len(usages) == 1
        assert intents[0].status == "executing"

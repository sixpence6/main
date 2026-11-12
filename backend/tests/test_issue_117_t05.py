"""Issue #117 T05: four-state recovery across real process restarts.

The seam is a logged-in user over the public HTTP workflow API, a real
temporary SQLite database and a controlled adapter. Each crash scenario exits
an independent uvicorn process at one exact boundary and then restarts the same
data directory. Startup may repair local facts; it must never call the provider
or mint a final authorization.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from app import recovery_states
from app.auth.models import AuditEvent
from app.config import Settings
from app.db import build_session_factory, create_engine, read_session, write_transaction
from app.errors import ApiError
from app.final_confirmation import FinalAuthorization
from app.models import Generation
from app.workflow.tables import GenerationJob
from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR, migrate
from tests.support.process_auth import prepare_process, process_auth_env, process_session


def test_four_recovery_states_and_allowed_actions() -> None:
    assert recovery_states.RECOVERY_STATES == (
        "waiting",
        "authorized_not_called",
        "submitted",
        "unknown",
    )
    assert recovery_states.allowed_actions("waiting") == ("continue_preparation",)
    assert recovery_states.allowed_actions("authorized_not_called") == (
        "consume_authorization",
    )
    assert recovery_states.allowed_actions("submitted") == (
        "verify_submission",
        "retrieve_result",
    )
    assert recovery_states.allowed_actions("unknown") == ("verify_submission",)


def test_classification_never_downgrades_a_potential_submit() -> None:
    assert (
        recovery_states.classify(recovery_states.RecoveryFacts())
        == recovery_states.WAITING
    )
    assert (
        recovery_states.classify(recovery_states.RecoveryFacts(has_authorization=True))
        == recovery_states.AUTHORIZED_NOT_CALLED
    )
    assert (
        recovery_states.classify(
            recovery_states.RecoveryFacts(
                has_authorization=True, submit_attempted=1
            )
        )
        == recovery_states.UNKNOWN
    )
    assert (
        recovery_states.classify(
            recovery_states.RecoveryFacts(has_authorization=True, submission_audit=True)
        )
        == recovery_states.UNKNOWN
    )
    assert (
        recovery_states.classify(
            recovery_states.RecoveryFacts(
                has_authorization=True, remote_task_id="remote-1"
            )
        )
        == recovery_states.SUBMITTED
    )
    assert (
        recovery_states.classify(
            recovery_states.RecoveryFacts(has_authorization=True, response_lost=True)
        )
        == recovery_states.UNKNOWN
    )
    assert (
        recovery_states.classify(
            recovery_states.RecoveryFacts(has_authorization=True, corrupt=True)
        )
        == recovery_states.UNKNOWN
    )


def test_only_provably_unsubmitted_authorization_may_be_consumed() -> None:
    clean = recovery_states.RecoveryFacts(has_authorization=True)
    assert recovery_states.can_consume_authorization(clean)
    for fact in (
        recovery_states.RecoveryFacts(has_authorization=False),
        recovery_states.RecoveryFacts(has_authorization=True, submit_attempted=1),
        recovery_states.RecoveryFacts(has_authorization=True, submission_audit=True),
        recovery_states.RecoveryFacts(has_authorization=True, remote_task_id="r"),
        recovery_states.RecoveryFacts(has_authorization=True, result_locator="{}"),
        recovery_states.RecoveryFacts(has_authorization=True, response_lost=True),
    ):
        assert not recovery_states.can_consume_authorization(fact)


def test_require_action_and_legacy_authorization_fail_closed() -> None:
    with pytest.raises(ApiError) as denied:
        recovery_states.require_action(recovery_states.WAITING, "verify_submission")
    assert denied.value.code == "RECOVERY_ACTION_NOT_ALLOWED"

    with pytest.raises(ApiError) as missing:
        recovery_states.require_legacy_authorization(None)
    assert missing.value.code == "FINAL_CONFIRMATION_REQUIRED"


def test_fresh_migration_creates_empty_final_authorizations(full_settings) -> None:
    import sqlite3

    migrate(full_settings)
    connection = sqlite3.connect(full_settings.database_path)
    try:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "final_authorizations" in names
        assert "prepared_prompts" in names
        assert (
            connection.execute("SELECT COUNT(*) FROM final_authorizations").fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def _start_server(
    settings: Settings, port: int, mode: str, call_log: Path
) -> subprocess.Popen[str]:
    settings.resolved_data_dir().mkdir(parents=True, exist_ok=True)
    migrate(settings)
    prepare_process(settings)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_", "T05_", "T17_"))
    }
    env.update(
        DATA_DIR=str(settings.data_dir),
        WEB_DIST_DIR=str(settings.web_dist_dir),
        PYTHONPATH=os.pathsep.join((str(MAIN_BACKEND_DIR), str(BACKEND_DIR))),
        PYTHONUNBUFFERED="1",
        LLM_ADAPTER="stub-llm",
        LLM_BASE_URL="https://llm.invalid",
        LLM_API_KEY="sk-test-t05",
        LLM_MODEL="stub-model",
        LLM_VISION_MODEL="stub-vision",
        LLM_TIMEOUT_SECONDS="5",
        VISUAL_ADAPTER="stub-visual",
        VISUAL_BASE_URL="https://visual.invalid",
        VISUAL_API_KEY="sk-test-t05",
        VISUAL_MODEL="stub-model",
        VISUAL_IMAGE_SIZE="1024x1024",
        VISUAL_TIMEOUT_SECONDS="5",
        T05_MODE=mode,
        T05_CALL_LOG=str(call_log),
    )
    env.update(process_auth_env(settings, port))
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.support.t05_restart_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
            "--log-level",
            "warning",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _stop(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)
    if process.stdout is not None:
        process.stdout.close()


def _kill(process: subprocess.Popen[str] | None) -> None:
    if process is None:
        return
    if process.poll() is None:
        process.kill()
    process.wait(timeout=20)
    if process.stdout is not None:
        process.stdout.close()


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _count_log(path: Path, prefix: str) -> int:
    lines = _lines(path)
    if prefix.endswith(":"):
        return sum(1 for line in lines if line.startswith(prefix))
    return sum(1 for line in lines if line == prefix)


def _wait_for_log(path: Path, prefix: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(line.startswith(prefix) for line in _lines(path)):
            return
        time.sleep(0.05)
    raise AssertionError(f"log {path} missing {prefix!r}: {_lines(path)!r}")


def _wait_for_exit(
    process: subprocess.Popen[str], call_log: Path, marker: str, timeout: float = 30.0
) -> None:
    _wait_for_log(call_log, marker, timeout)
    process.wait(timeout=timeout)


def _read(settings: Settings, loader):
    async def run():
        engine = create_engine(settings)
        try:
            async with read_session(build_session_factory(engine)) as session:
                return await loader(session)
        finally:
            await engine.dispose()

    last: OperationalError | None = None
    for attempt in range(20):
        try:
            return asyncio.run(run())
        except OperationalError as error:
            if "disk I/O error" not in str(error):
                raise
            last = error
            time.sleep(0.2 * (attempt + 1))
    assert last is not None
    raise last


def _count(settings: Settings, model) -> int:
    async def load(session):
        return len((await session.scalars(select(model))).all())

    return _read(settings, load)


def _project_rows(settings: Settings, project_id: str) -> dict[str, list[dict]]:
    async def load(session):
        authorizations = (
            await session.scalars(
                select(FinalAuthorization).where(
                    FinalAuthorization.project_id == project_id
                )
            )
        ).all()
        jobs = (
            await session.scalars(
                select(GenerationJob).where(GenerationJob.project_id == project_id)
            )
        ).all()
        generations = (
            await session.scalars(
                select(Generation).where(Generation.project_id == project_id)
            )
        ).all()
        return {
            "authorizations": [
                {
                    "id": row.id,
                    "job_id": row.job_id,
                    "generation_id": row.generation_id,
                }
                for row in authorizations
            ],
            "jobs": [
                {
                    "id": row.id,
                    "status": row.status,
                    "submit_attempted": row.submit_attempted or 0,
                    "generation_id": row.generation_id,
                    "remote_task_id": row.remote_task_id,
                    "result_asset_id": row.result_asset_id,
                    "error_code": row.error_code,
                }
                for row in jobs
            ],
            "generations": [
                {
                    "id": row.id,
                    "status": row.status,
                    "submit_attempted": row.submit_attempted or 0,
                    "remote_task_id": row.remote_task_id,
                    "result_locator_json": row.result_locator_json,
                    "result_asset_id": row.result_asset_id,
                    "error_code": row.error_code,
                }
                for row in generations
            ],
        }

    return _read(settings, load)


def _run_with_disk_io_retry(run) -> None:
    last: OperationalError | None = None
    for attempt in range(20):
        try:
            asyncio.run(run())
            return
        except OperationalError as error:
            if "disk I/O error" not in str(error):
                raise
            last = error
            time.sleep(0.2 * (attempt + 1))
    assert last is not None
    raise last


def _set_unknown(
    settings: Settings,
    job_id: str,
    generation_id: str,
    *,
    remote_task_id: str | None = None,
) -> None:
    async def run():
        engine = create_engine(settings)
        try:
            async with write_transaction(build_session_factory(engine)) as session:
                job = await session.get(GenerationJob, job_id)
                generation = await session.get(Generation, generation_id)
                assert job is not None and generation is not None
                job.status = "unknown"
                job.error_code = "VISUAL_OUTCOME_UNKNOWN"
                generation.status = "unknown"
                generation.error_code = "VISUAL_OUTCOME_UNKNOWN"
                if remote_task_id is not None:
                    job.remote_task_id = remote_task_id
                    generation.remote_task_id = remote_task_id
                await session.flush()
        finally:
            await engine.dispose()

    _run_with_disk_io_retry(run)


def _rewrite_attempt(
    settings: Settings,
    job_id: str,
    generation_id: str,
) -> None:
    async def run():
        engine = create_engine(settings)
        try:
            async with write_transaction(build_session_factory(engine)) as session:
                job = await session.get(GenerationJob, job_id)
                generation = await session.get(Generation, generation_id)
                assert job is not None and generation is not None
                job.status = "submit_pending"
                job.submit_attempted = 0
                job.error_code = None
                job.error_message = None
                generation.status = "queued"
                generation.submit_attempted = 0
                generation.error_code = None
                generation.error_message = None
                await session.flush()
        finally:
            await engine.dispose()

    _run_with_disk_io_retry(run)


def _audit_count(settings: Settings, event_type: str, target_id: str) -> int:
    async def load(session):
        rows = (
            await session.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == event_type,
                    AuditEvent.target_kind == "generation_job",
                    AuditEvent.target_id == target_id,
                )
            )
        ).all()
        return len(rows)

    return _read(settings, load)


def _delete_authorizations(settings: Settings, project_id: str) -> None:
    async def run():
        engine = create_engine(settings)
        try:
            async with write_transaction(build_session_factory(engine)) as session:
                await session.execute(
                    delete(FinalAuthorization).where(
                        FinalAuthorization.project_id == project_id
                    )
                )
                await session.flush()
        finally:
            await engine.dispose()

    _run_with_disk_io_retry(run)


def _session_headers(session) -> dict[str, str]:  # type: ignore[no-untyped-def]
    return {
        "Origin": session.origin,
        "X-CSRF-Token": session.csrf_token or "",
    }


async def _prepare_project(session) -> tuple[str, dict]:  # type: ignore[no-untyped-def]
    from tests.support.t01_prompt import prepared

    async with httpx.AsyncClient(
        base_url=session.base_url,
        cookies=session.cookies(),
        headers=_session_headers(session),
        timeout=60.0,
    ) as client:
        project_id = await prepared(client)
        deadline = time.monotonic() + 60.0
        last: dict | None = None
        while time.monotonic() < deadline:
            response = await client.get(f"/api/projects/{project_id}/workflow")
            assert response.status_code == 200, response.text
            last = response.json()
            if last.get("prompt_preview"):
                return project_id, last
            await asyncio.sleep(0.1)
    raise AssertionError(f"prompt preview did not appear: {last}")


def _confirm_or_crash(
    session, project_id: str, view: dict, command_id: str  # type: ignore[no-untyped-def]
) -> int | None:
    preview = view["prompt_preview"]

    async def run():
        async with httpx.AsyncClient(
            base_url=session.base_url,
            cookies=session.cookies(),
            headers=_session_headers(session),
            timeout=60.0,
        ) as client:
            response = await client.post(
                f"/api/projects/{project_id}/workflow/actions/confirm_prompt",
                json={
                    "expected_revision": view["project_revision"],
                    "payload": {
                        "snapshot_id": preview["id"],
                        "snapshot_digest": preview["request_digest"],
                        "command_id": command_id,
                        "command_context": preview.get("command_context", ""),
                    },
                },
            )
            if response.status_code == 502:
                # uvicorn's worker supervisor answers 502 when the worker exits.
                return None
            return response.status_code

    try:
        return asyncio.run(run())
    except httpx.HTTPError:
        return None


def _action(
    session,  # type: ignore[no-untyped-def]
    project_id: str,
    action: str,
    revision: int,
    payload: dict | None = None,
) -> tuple[int, dict]:
    body: dict = {"expected_revision": revision}
    if payload is not None:
        body["payload"] = payload
    return session.request(
        "POST",
        f"/api/projects/{project_id}/workflow/actions/{action}",
        body,
    )


def _workflow(session, project_id: str) -> tuple[int, dict]:  # type: ignore[no-untyped-def]
    return session.request("GET", f"/api/projects/{project_id}/workflow")


def _boot(settings: Settings, mode: str, call_log: Path):
    from tests.test_t01_process import free_port, wait_for_health

    last: OperationalError | None = None
    process: subprocess.Popen[str] | None = None
    for attempt in range(20):
        port = free_port()
        try:
            process = _start_server(settings, port, mode, call_log)
            break
        except OperationalError as error:
            # An abruptly killed uvicorn worker can leave the Windows SQLite
            # WAL handle in a transient state. Production startup recovers on
            # the next open; this test harness retries the same boundary.
            if "disk I/O error" not in str(error):
                raise
            last = error
            time.sleep(0.2 * (attempt + 1))
    else:
        assert last is not None
        raise last
    assert process is not None
    wait_for_health(process, port)
    return process, port


def _log_path(settings: Settings, name: str) -> Path:
    path = Path(settings.data_dir).parent / f"t05-{name}-{uuid.uuid4().hex}.log"
    path.unlink(missing_ok=True)
    return path


def test_waiting_survives_restart_and_only_explicit_confirm_submits(full_settings) -> None:
    log = _log_path(full_settings, "waiting")
    process = None
    try:
        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        project_id, view = asyncio.run(_prepare_project(session))
        preview = view["prompt_preview"]
        assert preview["status"] == "awaiting_confirmation"
        assert _count_log(log, "visual:") == 0
        _kill(process)
        process = None

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, restarted = _workflow(session, project_id)
        assert status == 200, restarted
        assert restarted["prompt_preview"]["id"] == preview["id"]
        assert (
            restarted["prompt_preview"]["request_digest"] == preview["request_digest"]
        )
        assert restarted["prompt_preview"]["status"] == "awaiting_confirmation"
        assert _count_log(log, "visual:") == 0

        command_id = "22222222-2222-4222-8222-222222222114"
        assert _confirm_or_crash(session, project_id, restarted, command_id) == 200
        _wait_for_log(log, "visual:submit")
        assert _count_log(log, "visual:submit") == 1
    finally:
        _stop(process)


def test_crash_before_final_commit_leaves_no_authorization(full_settings) -> None:
    log = _log_path(full_settings, "before-confirm")
    process = None
    try:
        process, port = _boot(full_settings, "crash-before-confirm", log)
        session = process_session(port)
        project_id, view = asyncio.run(_prepare_project(session))
        assert _confirm_or_crash(
            session, project_id, view, "22222222-2222-4222-8222-222222222115"
        ) is None
        _wait_for_exit(process, log, "crash:before-confirm")
        process = None

        assert _count(full_settings, FinalAuthorization) == 0
        assert _count(full_settings, GenerationJob) == 0
        assert _count(full_settings, Generation) == 0

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, restarted = _workflow(session, project_id)
        assert status == 200, restarted
        assert restarted["prompt_preview"]["id"] == view["prompt_preview"]["id"]
        assert restarted["prompt_preview"]["status"] == "awaiting_confirmation"
        assert _count_log(log, "visual:") == 0
        assert _count(full_settings, FinalAuthorization) == 0
    finally:
        _stop(process)


def test_crash_after_final_commit_resumes_only_on_explicit_recovery(full_settings) -> None:
    log = _log_path(full_settings, "after-confirm")
    process = None
    try:
        process, port = _boot(full_settings, "crash-after-confirm", log)
        session = process_session(port)
        project_id, view = asyncio.run(_prepare_project(session))
        assert _confirm_or_crash(
            session, project_id, view, "22222222-2222-4222-8222-222222222116"
        ) is None
        _wait_for_exit(process, log, "crash:after-confirm")
        process = None

        rows = _project_rows(full_settings, project_id)
        assert len(rows["authorizations"]) == 1
        assert len(rows["jobs"]) == 1
        assert rows["jobs"][0]["submit_attempted"] == 0
        assert rows["jobs"][0]["status"] == "submit_pending"
        assert _count_log(log, "visual:") == 0

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, blocked = _workflow(session, project_id)
        assert status == 200, blocked
        assert blocked["stage"] == "S08"
        assert blocked["status"] == "blocked"
        assert blocked["issue"]["code"] == "PROCESS_INTERRUPTED"
        assert blocked["generation_job"]["status"] == "failed"
        assert blocked["generation_job"]["submit_attempted"] is False
        assert _count_log(log, "visual:") == 0

        status, recovered = _action(
            session,
            project_id,
            "recover_generation",
            blocked["project_revision"],
        )
        assert status == 200, recovered
        _wait_for_log(log, "visual:submit")
        assert _count_log(log, "visual:submit") == 1
        rows = _project_rows(full_settings, project_id)
        assert len(rows["authorizations"]) == 1
        assert len(rows["jobs"]) == 1
        assert rows["jobs"][0]["id"] == blocked["generation_job"]["id"]
    finally:
        _stop(process)


def test_crash_after_submit_marker_is_unknown_and_never_resubmits(full_settings) -> None:
    log = _log_path(full_settings, "after-submit-mark")
    process = None
    try:
        process, port = _boot(full_settings, "crash-after-submit-mark", log)
        session = process_session(port)
        project_id, view = asyncio.run(_prepare_project(session))
        _confirm_or_crash(
            session, project_id, view, "22222222-2222-4222-8222-222222222117"
        )
        _wait_for_exit(process, log, "crash:after-submit-mark")
        process = None

        rows = _project_rows(full_settings, project_id)
        assert rows["jobs"][0]["submit_attempted"] == 1
        assert _count_log(log, "visual:") == 0

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, blocked = _workflow(session, project_id)
        assert status == 200, blocked
        assert blocked["status"] == "blocked"
        assert blocked["generation_job"]["status"] == "unknown"
        assert blocked["generation_job"]["submit_attempted"] is True

        status, verified = _action(
            session, project_id, "verify_generation", blocked["project_revision"]
        )
        assert status == 200, verified
        status, recovered = _action(
            session, project_id, "recover_generation", blocked["project_revision"]
        )
        assert status == 200, recovered
        assert _count_log(log, "visual:") == 0
        rows = _project_rows(full_settings, project_id)
        assert rows["jobs"][0]["status"] == "unknown"
        assert len(rows["authorizations"]) == 1
    finally:
        _stop(process)


def test_submit_audit_trace_prevents_unsubmitted_downgrade(full_settings) -> None:
    log = _log_path(full_settings, "audit-trace")
    process = None
    try:
        process, port = _boot(full_settings, "crash-after-submit-mark", log)
        session = process_session(port)
        project_id, view = asyncio.run(_prepare_project(session))
        _confirm_or_crash(
            session, project_id, view, "22222222-2222-4222-8222-222222222121"
        )
        _wait_for_exit(process, log, "crash:after-submit-mark")
        process = None

        rows = _project_rows(full_settings, project_id)
        job_id = rows["jobs"][0]["id"]
        generation_id = rows["generations"][0]["id"]
        assert _audit_count(full_settings, "generation.submit_started", job_id) == 1
        # Simulate a local repair that lost the counters but not the durable
        # submit-boundary audit trace. It must still be treated as unknown.
        _rewrite_attempt(full_settings, job_id, generation_id)

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, blocked = _workflow(session, project_id)
        assert status == 200, blocked
        assert blocked["status"] == "blocked"
        assert blocked["issue"]["code"] == "GENERATION_UNKNOWN", blocked
        assert blocked["generation_job"]["status"] == "unknown"
        assert _count_log(log, "visual:") == 0

        status, recovered = _action(
            session, project_id, "recover_generation", blocked["project_revision"]
        )
        assert status == 200, recovered
        assert _count_log(log, "visual:") == 0
        assert _project_rows(full_settings, project_id)["jobs"][0]["status"] == "unknown"
    finally:
        _stop(process)


@pytest.mark.parametrize(
    ("mode", "marker"),
    [
        ("crash-in-submit", "crash:in-submit"),
        ("crash-after-submit", "crash:after-submit"),
    ],
)
def test_provider_boundary_crash_is_unknown_without_a_second_submit(
    full_settings, mode: str, marker: str
) -> None:
    log = _log_path(full_settings, mode)
    process = None
    try:
        process, port = _boot(full_settings, mode, log)
        session = process_session(port)
        project_id, view = asyncio.run(_prepare_project(session))
        _confirm_or_crash(
            session, project_id, view, "22222222-2222-4222-8222-222222222118"
        )
        _wait_for_exit(process, log, marker)
        process = None
        assert _count_log(log, "visual:submit") == 1

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, blocked = _workflow(session, project_id)
        assert status == 200, blocked
        assert blocked["status"] == "blocked"
        assert blocked["generation_job"]["status"] == "unknown"
        assert blocked["generation_job"]["submit_attempted"] is True

        status, recovered = _action(
            session, project_id, "recover_generation", blocked["project_revision"]
        )
        assert status == 200, recovered
        assert _count_log(log, "visual:submit") == 1
        rows = _project_rows(full_settings, project_id)
        assert rows["jobs"][0]["status"] == "unknown"
    finally:
        _stop(process)


def test_lost_poll_response_verifies_the_original_remote_task(full_settings) -> None:
    log = _log_path(full_settings, "in-poll")
    process = None
    try:
        process, port = _boot(full_settings, "crash-in-poll", log)
        session = process_session(port)
        project_id, view = asyncio.run(_prepare_project(session))
        _confirm_or_crash(
            session, project_id, view, "22222222-2222-4222-8222-222222222119"
        )
        _wait_for_exit(process, log, "crash:in-poll")
        process = None

        rows = _project_rows(full_settings, project_id)
        assert rows["generations"][0]["remote_task_id"] == "remote-t05"
        assert rows["jobs"][0]["submit_attempted"] == 1
        assert _count_log(log, "visual:submit") == 1

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, blocked = _workflow(session, project_id)
        assert status == 200, blocked
        assert blocked["status"] == "blocked"
        assert blocked["generation_job"]["status"] == "unknown"
        assert _count_log(log, "visual:submit") == 1

        status, recovered = _action(
            session, project_id, "recover_generation", blocked["project_revision"]
        )
        assert status == 200, recovered
        assert _count_log(log, "visual:submit") == 1
        assert _count_log(log, "visual:poll:remote-t05") == 2
        rows = _project_rows(full_settings, project_id)
        assert rows["generations"][0]["result_asset_id"] is not None
    finally:
        _stop(process)


def test_legacy_rows_without_final_authorization_cannot_execute(full_settings) -> None:
    log = _log_path(full_settings, "legacy")
    process = None
    try:
        process, port = _boot(full_settings, "crash-after-confirm", log)
        session = process_session(port)
        project_id, view = asyncio.run(_prepare_project(session))
        assert _confirm_or_crash(
            session, project_id, view, "22222222-2222-4222-8222-222222222120"
        ) is None
        _wait_for_exit(process, log, "crash:after-confirm")
        process = None
        _delete_authorizations(full_settings, project_id)
        assert _count(full_settings, FinalAuthorization) == 0

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, blocked = _workflow(session, project_id)
        assert status == 200, blocked
        assert blocked["status"] == "blocked"
        revision = blocked["project_revision"]

        for action in ("submit_generation", "recover_generation"):
            status, body = _action(session, project_id, action, revision, {})
            assert status == 409, (action, status, body)
            assert body["error"]["code"] == "FINAL_CONFIRMATION_REQUIRED", body
        for action in ("retry_generation", "start_new_generation"):
            status, body = _action(session, project_id, action, revision, {})
            assert status == 409, (action, status, body)
            # These actions are not recovery choices for PROCESS_INTERRUPTED;
            # rejecting them as unavailable is stronger than offering a retry
            # that could later bypass the missing final authorization.
            assert body["error"]["code"] == "ACTION_NOT_AVAILABLE", body
        status, body = session.request(
            "POST",
            f"/api/projects/{project_id}/generations",
            {
                "request_id": str(uuid.uuid4()),
                "confirmed_revision": revision,
            },
        )
        assert status == 409, body
        # The direct legacy API has an earlier project-level closure for v2
        # projects; either way this old entry can never reach an image call.
        assert body["error"]["code"] in {
            "LEGACY_FLOW_DISABLED",
            "FINAL_CONFIRMATION_REQUIRED",
        }, body
        assert _count_log(log, "visual:") == 0
        assert _count(full_settings, FinalAuthorization) == 0

        rows = _project_rows(full_settings, project_id)
        assert len(rows["jobs"]) == 1
        _stop(process)
        process = None
        _set_unknown(
            full_settings,
            rows["jobs"][0]["id"],
            rows["generations"][0]["id"],
            remote_task_id="remote-legacy",
        )

        process, port = _boot(full_settings, "normal", log)
        session = process_session(port)
        status, blocked = _workflow(session, project_id)
        assert status == 200, blocked
        status, body = _action(
            session, project_id, "recover_generation", blocked["project_revision"]
        )
        assert status == 200, body
        # A legacy task with a remote id may be verified/retrieved, but it must
        # never be submitted again and no authorization may be minted.
        assert _count_log(log, "visual:submit") == 0
        assert _count_log(log, "visual:poll:remote-legacy") == 1
        rows = _project_rows(full_settings, project_id)
        assert rows["generations"][0]["result_asset_id"] is not None
        assert _count(full_settings, FinalAuthorization) == 0
    finally:
        _stop(process)

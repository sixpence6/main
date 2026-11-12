from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.db import build_session_factory, create_engine, read_session, write_transaction
from app.main import create_app
from app.models import Asset, Generation, Project
from app.services.assets import inspect_image, write_asset_file
from app.timeutil import format_timestamp, utc_now
from app.workflow import state
from app.workflow.contracts import BriefData
from app.workflow.tables import Approval, GenerationJob, RevisionBudget
from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR, migrate
from tests.support.adapters import build_test_registry
from tests.support.auth import authenticated_client
from tests.support.process_auth import prepare_process, process_auth_env, process_session

# AC-35/AC-36: a real uvicorn subprocess is force-killed while an independent
# image review is in flight. Restart must be read-only, keep the private
# candidate quarantined, preserve its original TTL, and expose exactly one
# manual continuation that re-reviews the same bytes without generation.


def _png(size: tuple[int, int] = (64, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (210, 220, 235)).save(buffer, format="PNG")
    return buffer.getvalue()


@dataclass
class Seed:
    project_id: str
    run_id: str
    asset_id: str
    job_id: str
    generation_id: str
    revision: int
    completed_at: str | None
    generated_bytes: bytes


async def _seed_s09(settings: Settings, *, completed_at: str | None) -> Seed:
    migrate(settings)
    application = create_app(settings=settings, registry=build_test_registry())
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            response = await client.post("/api/projects", json={})
            assert response.status_code == 201, response.text
            project_id = response.json()["id"]

            now = format_timestamp(utc_now())
            async with write_transaction(application.state.session_factory) as session:
                project = await session.get(Project, project_id)
                assert project is not None
                project.brief_status = "confirmed"
                project.confirmed_revision = project.revision
                project.image_size = "64x64"
                project.product_description = "白色陶瓷马克杯"
                project.updated_at = now

            ctx = await state.load_context(application, project_id)
            await state.create_run(ctx, "S09")
            brief = BriefData(
                product_description="白色陶瓷马克杯，容量 350ml",
                purpose="电商主图",
                facts=[],
                creative_direction="简洁棚拍",
                style_keywords=["clean"],
                composition="商品居中，留白充足",
                lighting="柔和棚拍光",
                texts=[],
                must_include=["商品主体完整"],
                must_preserve=["杯身颜色"],
                forbidden=["水印"],
                size="64x64",
                reference_asset_id=None,
                reference_usage="none",
                assumptions=[],
                unresolved=[],
            )
            brief_artifact = await state.start_artifact(ctx, "final_brief", brief)
            await state.add_review(
                ctx,
                brief_artifact,
                reviewer_role="output_reviewer",
                decision="pass",
                checks=[
                    {
                        "rule_id": "review:final_brief",
                        "outcome": "pass",
                        "evidence_ids": [],
                        "note": "需求卡通过",
                    }
                ],
            )
            snapshot_digest = await state.requirement_snapshot_digest(ctx)

            generated_bytes = _png()
            generated_relative = f"generated/{uuid.uuid4()}.png"
            write_asset_file(settings, generated_relative, generated_bytes)
            info = inspect_image(generated_bytes)
            asset_id = str(uuid.uuid4())
            job_id = str(uuid.uuid4())
            generation_id = str(uuid.uuid4())
            async with write_transaction(application.state.session_factory) as session:
                session.add(
                    Asset(
                        id=asset_id,
                        project_id=project_id,
                        kind="generated",
                        relative_path=generated_relative,
                        mime_type=info.mime_type,
                        width=info.width,
                        height=info.height,
                        byte_size=len(generated_bytes),
                        review_status="quarantined",
                        created_at=now,
                    )
                )
                session.add(
                    GenerationJob(
                        id=job_id,
                        project_id=project_id,
                        run_id=ctx.run.id,
                        request_id=str(uuid.uuid4()),
                        request_digest="b" * 64,
                        confirmed_snapshot_digest=snapshot_digest,
                        status="quarantined",
                        submit_attempted=0,
                        generation_id=generation_id,
                        result_asset_id=asset_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
                session.add(
                    Generation(
                        id=generation_id,
                        project_id=project_id,
                        version_no=1,
                        request_id=str(uuid.uuid4()),
                        request_canonical="{}",
                        request_digest="c" * 64,
                        confirmed_revision=project.revision,
                        status="succeeded",
                        stage="done",
                        brief_snapshot_json=json.dumps({"product_description": "白色陶瓷马克杯"}),
                        prompt="product photo",
                        image_size="64x64",
                        adapter_id="stub-visual",
                        model="stub-model",
                        service_fingerprint="d" * 64,
                        result_asset_id=asset_id,
                        submit_attempted=1,
                        workflow_job_id=job_id,
                        created_at=now,
                        started_at=now,
                        completed_at=completed_at,
                    )
                )
                await session.flush()
            return Seed(
                project_id=project_id,
                run_id=ctx.run.id,
                asset_id=asset_id,
                job_id=job_id,
                generation_id=generation_id,
                revision=ctx.project.revision,
                completed_at=completed_at,
                generated_bytes=generated_bytes,
            )


def _seed(
    settings: Settings, *, expired: bool = False, missing_completed: bool = False
) -> Seed:
    completed = (
        None
        if missing_completed
        else format_timestamp(utc_now() + timedelta(hours=-25 if expired else -1))
    )
    return asyncio.run(_seed_s09(settings, completed_at=completed))



@dataclass
class GenerationSeed:
    project_id: str
    revision: int
    job_id: str | None
    generation_id: str | None


def _prompt_payload(brief: dict[str, Any]) -> dict[str, Any]:
    parts = [
        brief["product_description"],
        brief["purpose"],
        brief["creative_direction"],
        "、".join(brief["style_keywords"]),
        brief["composition"],
        brief["lighting"],
        *brief["texts"],
        *[fact["text"] for fact in brief["facts"]],
        *brief["must_include"],
        *brief["must_preserve"],
    ]
    def rule(rule_id: str, binding: str, value: str) -> dict[str, str]:
        return {"rule_id": rule_id, "binding": binding, "value": value}

    coverage = [
        rule("hard:subject", "positive_prompt", brief["product_description"]),
        rule("hard:purpose", "positive_prompt", brief["purpose"]),
        rule("hard:creative", "positive_prompt", brief["creative_direction"]),
        rule("hard:style", "positive_prompt", "、".join(brief["style_keywords"])),
        rule("hard:composition", "positive_prompt", brief["composition"]),
        rule("hard:lighting", "positive_prompt", brief["lighting"]),
        rule("hard:text:0", "positive_prompt", brief["texts"][0]),
        rule("hard:fact:0", "positive_prompt", brief["facts"][0]["text"]),
        rule("hard:must_include:0", "positive_prompt", brief["must_include"][0]),
        rule("hard:must_preserve:0", "positive_prompt", brief["must_preserve"][0]),
        rule("hard:forbidden:0", "negative_prompt", brief["forbidden"][0]),
        rule("hard:forbidden:1", "negative_prompt", brief["forbidden"][1]),
        rule("hard:size", "size", brief["size"]),
        rule("hard:count", "count", "1"),
    ]
    return {
        "positive_prompt": "；".join(parts),
        "negative_prompt": "；".join(brief["forbidden"]),
        "size": brief["size"],
        "count": 1,
        "reference_asset_id": None,
        "requirement_coverage": coverage,
    }


async def _seed_s08_async(settings: Settings, *, pending_job: bool) -> GenerationSeed:
    migrate(settings)
    application = create_app(settings=settings, registry=build_test_registry())
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            response = await client.post("/api/projects", json={})
            assert response.status_code == 201, response.text
            created = response.json()
            project_id = created["id"]
            revision = created["revision"]

            now = format_timestamp(utc_now())
            brief = BriefData(
                product_description="白色陶瓷马克杯，容量 350ml",
                purpose="电商主图",
                facts=[
                    {
                        "text": "杯身有蓝色鲸鱼图案",
                        "basis": "user_declared",
                        "evidence_ids": ["seed"],
                    }
                ],
                creative_direction="清晨窗边自然光，简洁温暖",
                style_keywords=["极简", "自然"],
                composition="主体居中，留白充足",
                lighting="柔和侧光",
                texts=["WARM MORNING"],
                must_include=["杯身蓝色鲸鱼图案"],
                must_preserve=["杯口金色描边"],
                forbidden=["价格标签", "促销文案"],
                size="1024x1024",
                reference_asset_id=None,
                reference_usage="none",
                assumptions=[],
                unresolved=[],
            ).model_dump(mode="json")
            async with write_transaction(application.state.session_factory) as session:
                project = await session.get(Project, project_id)
                assert project is not None
                project.product_description = brief["product_description"]
                project.purpose = brief["purpose"]
                project.style = "、".join(brief["style_keywords"])
                project.composition = brief["composition"]
                project.text_content = "、".join(brief["texts"])
                project.image_size = "1024x1024"
                project.brief_status = "confirmed"
                project.confirmed_revision = revision
                project.updated_at = now

            ctx = await state.load_context(application, project_id)
            await state.create_run(ctx, "S08")
            ctx = await state.reload_context(ctx)
            final_artifact = await state.start_artifact(ctx, "final_brief", brief)
            await state.add_review(
                ctx,
                final_artifact,
                reviewer_role="output_reviewer",
                decision="pass",
                checks=[
                    {
                        "rule_id": "G03_CONFIRMATION",
                        "outcome": "pass",
                        "evidence_ids": [final_artifact.id],
                        "note": "人工确认",
                    }
                ],
            )
            approved_final = await state.latest_artifact(ctx, "final_brief", status="approved")
            assert approved_final is not None
            await state.add_approval(
                ctx,
                approval_type="requirements_card",
                artifact=approved_final,
                approved_by="user",
                checks={"confirmed": True},
            )
            prompt = _prompt_payload(brief)
            prompt_artifact = await state.start_artifact(ctx, "prompt_plan", prompt)
            await state.add_review(
                ctx,
                prompt_artifact,
                reviewer_role="output_reviewer",
                decision="pass",
                checks=[
                    {
                        "rule_id": "G03_CONFIRMATION",
                        "outcome": "pass",
                        "evidence_ids": [prompt_artifact.id],
                        "note": "独立审查",
                    }
                ],
            )
            approved_prompt = await state.latest_artifact(ctx, "prompt_plan", status="approved")
            assert approved_prompt is not None
            await state.add_approval(
                ctx,
                approval_type="generation_prompt",
                artifact=approved_prompt,
                approved_by="output_reviewer",
                checks={"checks": []},
                requirement_digest=approved_final.digest,
            )

            job_id: str | None = None
            generation_id: str | None = None
            if pending_job:
                job_id = str(uuid.uuid4())
                generation_id = str(uuid.uuid4())
                async with write_transaction(application.state.session_factory) as session:
                    session.add(
                        Generation(
                            id=generation_id,
                            project_id=project_id,
                            version_no=1,
                            request_id=str(uuid.uuid4()),
                            request_canonical="{}",
                            request_digest="e" * 64,
                            confirmed_revision=revision,
                            status="queued",
                            stage="waiting_slot",
                            brief_snapshot_json=json.dumps(brief),
                            prompt=prompt["positive_prompt"],
                            image_size="1024x1024",
                            adapter_id="stub-visual",
                            model="stub-model",
                            service_fingerprint="f" * 64,
                            submit_attempted=0,
                            workflow_job_id=job_id,
                            created_at=now,
                        )
                    )
                    session.add(
                        GenerationJob(
                            id=job_id,
                            project_id=project_id,
                            run_id=ctx.run.id,
                            request_id=str(uuid.uuid4()),
                            request_digest="e" * 64,
                            prompt_artifact_id=approved_prompt.id,
                            confirmed_snapshot_digest=approved_final.digest,
                            status="submit_pending",
                            submit_attempted=0,
                            generation_id=generation_id,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    await session.flush()
            return GenerationSeed(
                project_id=project_id,
                revision=revision,
                job_id=job_id,
                generation_id=generation_id,
            )


def _seed_s08(settings: Settings, *, pending_job: bool = False) -> GenerationSeed:
    return asyncio.run(_seed_s08_async(settings, pending_job=pending_job))


def _read(settings: Settings, loader):
    async def run():
        engine = create_engine(settings)
        try:
            async with read_session(build_session_factory(engine)) as session:
                return await loader(session)
        finally:
            await engine.dispose()

    # Windows can briefly report a WAL I/O error while a force-killed child
    # process is still releasing its SQLite handle. This retry is test-only and
    # does not hide assertion failures: a real missing table/row still raises.
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

def _count_rows(settings: Settings, model) -> int:
    async def load(session):
        return len((await session.scalars(select(model))).all())

    return _read(settings, load)
def _request(port: int, method: str, path: str, payload: Any = None) -> tuple[int, Any]:
    return process_session(port).request(method, path, payload)


def _start_server(
    settings: Settings, port: int, mode: str, call_log: Path
) -> subprocess.Popen[str]:
    prepare_process(settings)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_", "T17_"))
    }
    env.update(
        DATA_DIR=str(settings.data_dir),
        WEB_DIST_DIR=str(settings.web_dist_dir),
        PYTHONPATH=os.pathsep.join((str(MAIN_BACKEND_DIR), str(BACKEND_DIR))),
        PYTHONUNBUFFERED="1",
        LLM_ADAPTER="stub-llm",
        LLM_BASE_URL="https://llm.invalid",
        LLM_API_KEY="sk-test-t17",
        LLM_MODEL="stub-model",
        LLM_VISION_MODEL="stub-vision",
        LLM_TIMEOUT_SECONDS="5",
        VISUAL_ADAPTER="stub-visual",
        VISUAL_BASE_URL="https://visual.invalid",
        VISUAL_API_KEY="sk-test-t17",
        VISUAL_MODEL="stub-model",
        VISUAL_IMAGE_SIZE="1024x1024",
        VISUAL_TIMEOUT_SECONDS="5",
        T17_MODE=mode,
        T17_CALL_LOG=str(call_log),
    )
    env.update(process_auth_env(settings, port))
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.support.t17_restart_app:app",
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


def _kill(process: subprocess.Popen[str]) -> None:
    process.kill()
    process.wait(timeout=20)
    if process.stdout is not None:
        process.stdout.close()


def _log_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _wait_for_log(path: Path, prefix: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(line.startswith(prefix) for line in _log_lines(path)):
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {prefix!r}: {_log_lines(path)}")


def _job(settings: Settings, job_id: str) -> GenerationJob:
    async def load(session):
        row = await session.get(GenerationJob, job_id)
        assert row is not None
        return row

    return _read(settings, load)
def _generation(settings: Settings, generation_id: str) -> Generation:
    async def load(session):
        row = await session.get(Generation, generation_id)
        assert row is not None
        return row

    return _read(settings, load)
def _derived_expiry(completed_at: str) -> str:
    completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    return format_timestamp(completed + timedelta(hours=24))


def _image_approvals(settings: Settings, project_id: str) -> list[Approval]:
    async def load(session):
        return list(
            (
                await session.scalars(
                    select(Approval).where(
                        Approval.project_id == project_id,
                        Approval.approval_type == "image",
                    )
                )
            ).all()
        )

    return _read(settings, load)
def _budget_used(settings: Settings, run_id: str, kind: str = "quality_report") -> int:
    async def load(session):
        row = await session.get(RevisionBudget, (run_id, kind))
        return 0 if row is None else row.used

    return _read(settings, load)
def _delete_job(settings: Settings, job_id: str) -> None:
    async def drop() -> None:
        application = create_app(settings=settings, registry=build_test_registry())
        async with application.router.lifespan_context(application):
            async with write_transaction(application.state.session_factory) as session:
                await session.execute(delete(GenerationJob).where(GenerationJob.id == job_id))

    asyncio.run(drop())

def test_force_kill_during_review_restart_is_read_only_and_continue_reuses_same_image(
    full_settings,
) -> None:
    from tests.test_t01_process import free_port, stop_server, wait_for_health

    seed = _seed(full_settings)
    generations_before = _count_rows(full_settings, Generation)
    jobs_before = _count_rows(full_settings, GenerationJob)
    call_log = Path(full_settings.data_dir).parent / "t17-calls.log"
    call_log.unlink(missing_ok=True)

    port = free_port()
    first = _start_server(full_settings, port, "block-review", call_log)
    request_error: list[BaseException] = []

    def fire_review() -> None:
        try:
            _request(
                port,
                "POST",
                f"/api/projects/{seed.project_id}/workflow/actions/review_candidate",
                {"expected_revision": seed.revision},
            )
        except BaseException as error:  # noqa: BLE001 - the process is killed mid-request
            request_error.append(error)

    try:
        wait_for_health(first, port)
        thread = threading.Thread(target=fire_review, daemon=True)
        thread.start()
        _wait_for_log(call_log, "review:1:")
        _kill(first)
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        if first.poll() is None:
            stop_server(first)

    assert request_error  # the killed in-flight connection cannot return a candidate

    port = free_port()
    second = _start_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(second, port)
        before = len(_log_lines(call_log))
        status, view = _request(port, "GET", f"/api/projects/{seed.project_id}/workflow")
        assert status == 200, view
        assert view["stage"] == "S09"
        assert view["status"] == "failed"
        assert [item["id"] for item in view["available_actions"]] == ["continue_workflow"]
        assert view["generation_job"]["result_asset_id"] == seed.asset_id
        assert len(_log_lines(call_log)) == before  # startup + status are read-only

        status, hidden = _request(port, "GET", f"/api/assets/{seed.asset_id}?download=true")
        assert status == 409, hidden

        def post_continue(_: int) -> tuple[int, Any]:
            return _request(
                port,
                "POST",
                f"/api/projects/{seed.project_id}/workflow/actions/continue_workflow",
                {"expected_revision": view["project_revision"]},
            )

        # Two clicks race on the same interrupted checkpoint. The project lock
        # may serialize them; whichever wins must be the only recovery, and a
        # stale-revision / busy response is acceptable for the loser.
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(post_continue, range(2)))
        assert any(status == 200 for status, _ in responses), responses
        for status, body in responses:
            assert status == 200 or (
                status == 409
                and body.get("error", {}).get("code") in {"PROJECT_BUSY", "REVISION_CONFLICT"}
            ), body
        continued = next(body for status, body in responses if status == 200)
        assert continued["stage"] == "S10"
        assert continued["status"] == "active"
        report = next(
            item for item in continued["artifacts"] if item["kind"] == "quality_report"
        )
        assert report["status"] == "approved"
        image_approvals = [
            item for item in continued["approvals"] if item["approval_type"] == "image"
        ]
        assert len(image_approvals) == 1
        assert _budget_used(full_settings, seed.run_id) == 1

        # A repeated click is an idempotent no-op: it returns the same approved
        # state and must not start a second quality/review call or approval.
        status, repeated = _request(
            port,
            "POST",
            f"/api/projects/{seed.project_id}/workflow/actions/continue_workflow",
            {"expected_revision": continued["project_revision"]},
        )
        assert status == 200, repeated
        assert repeated["stage"] == "S10"
        assert repeated["status"] == "active"
        image_approvals = [
            item for item in repeated["approvals"] if item["approval_type"] == "image"
        ]
        assert len(image_approvals) == 1
    finally:
        stop_server(second)

    log = _log_lines(call_log)
    assert [line for line in log if line.startswith("quality:")] == ["quality:1"]
    assert [line for line in log if line.startswith("review:")] == [
        "review:1:quality_report:images=1",
        "review:1:quality_report:images=1",
    ]
    assert not any(line.startswith("visual:") for line in log)

    job = _job(full_settings, seed.job_id)
    assert job.status == "approved"
    assert _count_rows(full_settings, Generation) == generations_before
    assert _count_rows(full_settings, GenerationJob) == jobs_before
    generation = _generation(full_settings, seed.generation_id)
    assert generation.completed_at == seed.completed_at
    assert _derived_expiry(generation.completed_at or "") == _derived_expiry(seed.completed_at)
    assert len(_image_approvals(full_settings, seed.project_id)) == 1

    # A second restart preserves the approved state and performs no new work.
    port = free_port()
    third = _start_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(third, port)
        before = len(_log_lines(call_log))
        status, same = _request(port, "GET", f"/api/projects/{seed.project_id}/workflow")
        assert status == 200, same
        assert same["stage"] == "S10"
        assert same["status"] == "active"
        assert len(_log_lines(call_log)) == before
    finally:
        stop_server(third)
    assert _generation(full_settings, seed.generation_id).completed_at == seed.completed_at
    assert len(_image_approvals(full_settings, seed.project_id)) == 1
    assert _budget_used(full_settings, seed.run_id) == 1


def test_missing_completed_at_candidate_is_never_reviewed_or_regenerated(
    full_settings,
) -> None:
    from tests.test_t01_process import free_port, wait_for_health

    seed = _seed(full_settings, missing_completed=True)
    call_log = Path(full_settings.data_dir).parent / "t17-missing-completed.log"
    call_log.unlink(missing_ok=True)
    port = free_port()
    server = _start_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(server, port)
        status, view = _request(port, "GET", f"/api/projects/{seed.project_id}/workflow")
        assert status == 200, view
        status, rejected = _request(
            port,
            "POST",
            f"/api/projects/{seed.project_id}/workflow/actions/review_candidate",
            {"expected_revision": view["project_revision"]},
        )
        assert status == 409, rejected
        assert rejected["error"]["code"] == "CANDIDATE_EXPIRED"
        assert _budget_used(full_settings, seed.run_id) == 0
    finally:
        _kill(server)
    assert not any(line.startswith("visual:") for line in _log_lines(call_log))
    assert not any(line.startswith("quality:") for line in _log_lines(call_log))


def test_s09_without_generation_job_never_falls_back_to_project_asset(
    full_settings,
) -> None:
    from tests.test_t01_process import free_port, wait_for_health

    seed = _seed(full_settings)
    _delete_job(full_settings, seed.job_id)
    call_log = Path(full_settings.data_dir).parent / "t17-no-job.log"
    call_log.unlink(missing_ok=True)
    port = free_port()
    server = _start_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(server, port)
        status, view = _request(port, "GET", f"/api/projects/{seed.project_id}/workflow")
        assert status == 200, view
        status, rejected = _request(
            port,
            "POST",
            f"/api/projects/{seed.project_id}/workflow/actions/review_candidate",
            {"expected_revision": view["project_revision"]},
        )
        assert status == 409, rejected
        assert rejected["error"]["code"] == "CANDIDATE_UNAVAILABLE"
    finally:
        _kill(server)
    assert not any(line.startswith("visual:") for line in _log_lines(call_log))


def test_expired_pending_candidate_is_never_exposed_or_regenerated(full_settings) -> None:
    from tests.test_t01_process import free_port, wait_for_health

    seed = _seed(full_settings, expired=True)
    call_log = Path(full_settings.data_dir).parent / "t17-expired.log"
    call_log.unlink(missing_ok=True)
    port = free_port()
    server = _start_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(server, port)
        status, view = _request(port, "GET", f"/api/projects/{seed.project_id}/workflow")
        assert status == 200, view
        status, hidden = _request(port, "GET", f"/api/assets/{seed.asset_id}?download=true")
        assert status == 409, hidden
        before = len(_log_lines(call_log))
        status, rejected = _request(
            port,
            "POST",
            f"/api/projects/{seed.project_id}/workflow/actions/review_candidate",
            {"expected_revision": seed.revision},
        )
        assert status == 409, rejected
        assert rejected["error"]["code"] == "CANDIDATE_EXPIRED"
        assert len(_log_lines(call_log)) == before
    finally:
        _kill(server)
    assert not any(line.startswith("visual:") for line in _log_lines(call_log))


def test_force_kill_during_text_requirements_restart_is_read_only_and_manual_continue(
    full_settings,
) -> None:
    from tests.test_t01_process import free_port, stop_server, wait_for_health

    call_log = Path(full_settings.data_dir).parent / "t17-text.log"
    call_log.unlink(missing_ok=True)
    migrate(full_settings)
    port = free_port()
    first = _start_server(full_settings, port, "block-requirements", call_log)
    request_error: list[BaseException] = []
    project_id = ""
    try:
        wait_for_health(first, port)
        status, project = _request(port, "POST", "/api/projects", {})
        assert status == 201, project
        project_id = project["id"]
        status, view = _request(
            port,
            "POST",
            f"/api/projects/{project_id}/workflow/actions/submit_request",
            {"expected_revision": project["revision"], "text": "做一张米色托特包的电商主图"},
        )
        assert status == 200, view
        assert view["stage"] == "S03"

        def fire_compose() -> None:
            try:
                _request(
                    port,
                    "POST",
                    f"/api/projects/{project_id}/workflow/actions/compose_brief",
                    {"expected_revision": view["project_revision"]},
                )
            except BaseException as error:  # noqa: BLE001 - killed mid-request
                request_error.append(error)

        thread = threading.Thread(target=fire_compose, daemon=True)
        thread.start()
        _wait_for_log(call_log, "requirements:1")
        _kill(first)
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        if first.poll() is None:
            first.kill()
    assert request_error

    port = free_port()
    second = _start_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(second, port)
        before = len(_log_lines(call_log))
        status, view = _request(port, "GET", f"/api/projects/{project_id}/workflow")
        assert status == 200, view
        assert view["stage"] == "S03"
        assert view["status"] == "failed"
        assert [item["id"] for item in view["available_actions"]] == ["continue_workflow"]
        assert len(_log_lines(call_log)) == before

        status, continued = _request(
            port,
            "POST",
            f"/api/projects/{project_id}/workflow/actions/continue_workflow",
            {"expected_revision": view["project_revision"]},
        )
        assert status == 200, continued
        assert continued["stage"] == "S04"
        assert continued["status"] == "active"
        briefs = [item for item in continued["artifacts"] if item["kind"] == "brief_draft"]
        assert len(briefs) == 1 and briefs[0]["status"] == "approved"
    finally:
        stop_server(second)

    log = _log_lines(call_log)
    assert [
        line for line in log if line.startswith("requirements:")
    ] == ["requirements:1", "requirements:1"]
    assert not any(line.startswith("visual:") for line in log)


def test_force_kill_during_vision_reference_restart_is_read_only_and_manual_continue(
    full_settings,
) -> None:
    from tests.test_t01_process import free_port, wait_for_health

    call_log = Path(full_settings.data_dir).parent / "t17-vision.log"
    call_log.unlink(missing_ok=True)
    reference_bytes = _png((96, 96))
    migrate(full_settings)
    port = free_port()
    first = _start_server(full_settings, port, "block-reference", call_log)
    request_error: list[BaseException] = []
    project_id = ""
    reference_id = ""
    try:
        wait_for_health(first, port)
        status, project = _request(port, "POST", "/api/projects", {})
        assert status == 201, project
        project_id = project["id"]
        with process_session(port).httpx_client() as client:
            uploaded = client.post(
                f"/api/projects/{project_id}/reference",
                files={"file": ("reference.png", reference_bytes, "image/png")},
                data={"expected_revision": str(project["revision"])},
            )
        assert uploaded.status_code == 200, uploaded.text
        uploaded_body = uploaded.json()
        reference_id = uploaded_body["reference_asset"]["id"]

        def fire_submit() -> None:
            try:
                _request(
                    port,
                    "POST",
                    f"/api/projects/{project_id}/workflow/actions/submit_request",
                    {"expected_revision": uploaded_body["revision"], "text": "按参考图做电商主图"},
                )
            except BaseException as error:  # noqa: BLE001 - killed mid-request
                request_error.append(error)

        thread = threading.Thread(target=fire_submit, daemon=True)
        thread.start()
        _wait_for_log(call_log, "reference:1")
        _kill(first)
        thread.join(timeout=10)
        assert not thread.is_alive()
    finally:
        if first.poll() is None:
            first.kill()
    assert request_error

    port = free_port()
    second = _start_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(second, port)
        before = len(_log_lines(call_log))
        status, view = _request(port, "GET", f"/api/projects/{project_id}/workflow")
        assert status == 200, view
        assert view["stage"] == "S02"
        assert view["status"] == "failed"
        assert [item["id"] for item in view["available_actions"]] == ["continue_workflow"]
        assert len(_log_lines(call_log)) == before

        status, continued = _request(
            port,
            "POST",
            f"/api/projects/{project_id}/workflow/actions/continue_workflow",
            {"expected_revision": view["project_revision"]},
        )
        assert status == 200, continued
        assert continued["stage"] == "S03"
        assert continued["status"] == "active"
        with process_session(port).httpx_client() as client:
            reference = client.get(f"/api/assets/{reference_id}")
        assert reference.status_code == 200, reference.text
        assert reference.content == reference_bytes
    finally:
        _kill(second)

    log = _log_lines(call_log)
    assert [line for line in log if line.startswith("reference:")] == ["reference:1", "reference:1"]
    assert not any(line.startswith("visual:") for line in log)


def test_force_kill_at_generation_submit_boundary_replays_same_task_without_retry(
    full_settings,
) -> None:
    from tests.test_t01_process import free_port, wait_for_health

    seed = _seed_s08(full_settings)
    call_log = Path(full_settings.data_dir).parent / "t17-submit.log"
    call_log.unlink(missing_ok=True)
    port = free_port()
    first = _start_server(full_settings, port, "block-submit", call_log)
    job_id = ""
    try:
        wait_for_health(first, port)
        status, submitted = _request(
            port,
            "POST",
            f"/api/projects/{seed.project_id}/workflow/actions/submit_generation",
            {"expected_revision": seed.revision},
        )
        assert status == 200, submitted
        job_id = submitted["generation_job"]["id"]
        _wait_for_log(call_log, "visual:submit")
        _kill(first)
    finally:
        if first.poll() is None:
            first.kill()

    jobs_before = _count_rows(full_settings, GenerationJob)
    generations_before = _count_rows(full_settings, Generation)
    port = free_port()
    second = _start_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(second, port)
        before = len(_log_lines(call_log))
        status, view = _request(port, "GET", f"/api/projects/{seed.project_id}/workflow")
        assert status == 200, view
        assert view["stage"] == "S08"
        assert view["status"] == "blocked"
        assert view["generation_job"]["id"] == job_id
        assert view["generation_job"]["status"] == "unknown"
        assert view["generation_job"]["result_asset_id"] is None
        assert len(_log_lines(call_log)) == before

        status, replay = _request(
            port,
            "POST",
            f"/api/projects/{seed.project_id}/workflow/actions/submit_generation",
            {"expected_revision": view["project_revision"]},
        )
        assert status == 200, replay
        assert replay["generation_job"]["id"] == job_id
        assert replay["generation_job"]["status"] == "unknown"
        assert _count_rows(full_settings, GenerationJob) == jobs_before
        assert _count_rows(full_settings, Generation) == generations_before
    finally:
        _kill(second)
    assert [
        line for line in _log_lines(call_log) if line.startswith("visual:")
    ] == ["visual:submit"]


def test_provably_unsubmitted_generation_is_only_marked_interrupted(full_settings) -> None:
    from tests.test_t01_process import free_port, wait_for_health

    seed = _seed_s08(full_settings, pending_job=True)
    assert seed.job_id and seed.generation_id
    call_log = Path(full_settings.data_dir).parent / "t17-unsubmitted.log"
    call_log.unlink(missing_ok=True)
    port = free_port()
    server = _start_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(server, port)
        status, view = _request(port, "GET", f"/api/projects/{seed.project_id}/workflow")
        assert status == 200, view
        assert view["stage"] == "S08"
        assert view["status"] == "blocked"
        assert view["generation_job"]["id"] == seed.job_id
        assert view["generation_job"]["status"] == "failed"
        job = _job(full_settings, seed.job_id)
        assert job.error_code == "PROCESS_INTERRUPTED"
        generation = _generation(full_settings, seed.generation_id)
        assert generation.status == "failed"
        assert generation.completed_at

        status, replay = _request(
            port,
            "POST",
            f"/api/projects/{seed.project_id}/workflow/actions/submit_generation",
            {"expected_revision": view["project_revision"]},
        )
        assert status == 200, replay
        assert replay["generation_job"]["id"] == seed.job_id
        assert replay["generation_job"]["status"] == "failed"
    finally:
        _kill(server)
    assert not any(line.startswith("visual:") for line in _log_lines(call_log))

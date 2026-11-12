from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from app.adapters.base import ProviderError
from app.config import Settings
from app.db import write_transaction
from app.main import create_app
from app.models import Project
from app.workflow import state
from app.workflow.stages.s02 import REQUIRED_REVIEW_RULES as VISION_RULES
from app.workflow.stages.s03 import REVIEW_RULE_IDS
from app.workflow.tables import Artifact, RevisionBudget, RoleCall
from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR, migrate
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.auth import authenticated_client
from tests.support.process_auth import prepare_process, process_auth_env, process_session

# AC-16/AC-17: technical failures are paused, persisted, and resumed only by an
# explicit manual continue. Every assertion below uses the public workflow HTTP
# API, a real temporary SQLite database and the real application lifecycle; only
# the external model adapter is scripted.


def _ready_brief(request: Any) -> dict[str, Any]:
    evidence = request.payload["task"]["allowed_evidence_ids"][0]
    return {
        "role": "requirements_consultant",
        "status": "ready",
        "data": {
            "product_description": "米色托特包",
            "purpose": "电商商品主图",
            "facts": [
                {
                    "text": "用户声明商品为米色托特包",
                    "basis": "user_declared",
                    "evidence_ids": [evidence],
                }
            ],
            "creative_direction": None,
            "style_keywords": [],
            "composition": None,
            "lighting": None,
            "texts": [],
            "must_include": ["一只米色托特包"],
            "must_preserve": [],
            "forbidden": [],
            "size": request.payload["capabilities"]["size"] or "1024x1024",
            "reference_asset_id": None,
            "reference_usage": "none",
            "assumptions": [],
            "unresolved": [],
        },
        "issues": [],
        "questions": [],
    }


def _creative_ready() -> dict[str, Any]:
    return {
        "role": "creative_planner",
        "status": "ready",
        "data": {
            "direction": "简洁背景突出托特包外观",
            "selling_points": [],
            "copy_lines": [],
        },
        "issues": [],
        "questions": [],
    }


def _review_pass(target_id: str, rules: tuple[str, ...] = REVIEW_RULE_IDS) -> dict[str, Any]:
    return {
        "role": "output_reviewer",
        "status": "ready",
        "data": {
            "results": [
                {
                    "target_id": target_id,
                    "decision": "pass",
                    "checks": [
                        {
                            "rule_id": rule,
                            "outcome": "pass",
                            "evidence_ids": [target_id],
                            "note": "",
                        }
                        for rule in rules
                    ],
                }
            ]
        },
        "issues": [],
        "questions": [],
    }


class RecoveryScript:
    def __init__(
        self,
        *,
        brief_responder: Callable[[Any], Any] | None = None,
        creative_responder: Callable[[Any], Any] | None = None,
        fail_requirements: bool = False,
        fail_brief_review: bool = False,
        fail_creative: bool = False,
        fail_creative_review: bool = False,
        fail_reference: bool = False,
        fail_reference_review: bool = False,
    ) -> None:
        self.brief_responder = brief_responder or _ready_brief
        self.creative_responder = creative_responder or (lambda request: _creative_ready())
        self.fail_requirements = fail_requirements
        self.fail_brief_review = fail_brief_review
        self.fail_creative = fail_creative
        self.fail_creative_review = fail_creative_review
        self.fail_reference = fail_reference
        self.fail_reference_review = fail_reference_review
        self.role_requests: list[Any] = []
        self.requirements_calls = 0
        self.creative_calls = 0
        self.brief_review_calls = 0
        self.creative_review_calls = 0
        self.reference_calls = 0
        self.reference_review_calls = 0

    def __call__(self, request: Any) -> Any:
        self.role_requests.append(request)
        task = request.payload["task"]
        role = str(task["role"])
        if role == "intent_router":
            evidence = task["allowed_evidence_ids"][0]
            return {
                "role": "intent_router",
                "status": "ready",
                "data": {
                    "route": "image_request",
                    "action": "new_request",
                    "evidence_ids": [evidence],
                },
                "issues": [],
                "questions": [],
            }
        if role == "output_reviewer":
            target = request.payload["review_targets"][0]
            target_kind = target.get("target_kind")
            target_id = str(target["target_id"])
            if target_kind == "intent_route":
                return _review_pass(target_id, ("G01_SCOPE", "G01_ACTION"))
            if target_kind == "brief_draft":
                self.brief_review_calls += 1
                if self.fail_brief_review and self.brief_review_calls == 1:
                    raise ProviderError(
                        "review timeout",
                        evidence="uncertain",
                        error_code="LLM_TIMEOUT",
                    )
                return _review_pass(target_id)
            if target_kind == "reference_report":
                self.reference_review_calls += 1
                if self.fail_reference_review and self.reference_review_calls == 1:
                    raise ProviderError(
                        "vision review timeout",
                        evidence="uncertain",
                        error_code="LLM_TIMEOUT",
                    )
                image_evidence = "reference:" + str(target.get("reference_asset_id"))
                return {
                    "role": "output_reviewer",
                    "status": "ready",
                    "data": {
                        "results": [
                            {
                                "target_id": target_id,
                                "decision": "pass",
                                "checks": [
                                    {
                                        "rule_id": rule,
                                        "outcome": "pass",
                                        "evidence_ids": [target_id, image_evidence],
                                        "note": "",
                                    }
                                    for rule in VISION_RULES
                                ],
                            }
                        ]
                    },
                    "issues": [],
                    "questions": [],
                }
            if target_kind == "creative_direction":
                self.creative_review_calls += 1
                if self.fail_creative_review and self.creative_review_calls == 1:
                    raise ProviderError(
                        "review network down",
                        evidence="uncertain",
                        error_code="LLM_NETWORK_ERROR",
                    )
                return _review_pass(
                    target_id,
                    ("G03_SOURCE", "G03_COPY", "G03_REQUIREMENTS", "G03_SINGLE_DIRECTION"),
                )
            raise AssertionError(f"unexpected review target {target_kind!r}")
        if role == "requirements_consultant":
            self.requirements_calls += 1
            if self.fail_requirements and self.requirements_calls == 1:
                raise ProviderError(
                    "provider network down",
                    evidence="uncertain",
                    error_code="LLM_NETWORK_ERROR",
                )
            return self.brief_responder(request)
        if role == "reference_analyst":
            self.reference_calls += 1
            if self.fail_reference and self.reference_calls == 1:
                raise ProviderError(
                    "vision network down",
                    evidence="uncertain",
                    error_code="LLM_NETWORK_ERROR",
                )
            evidence = task["allowed_evidence_ids"][0]
            return {
                "role": "reference_analyst",
                "status": "ready",
                "data": {
                    "reference_usage": "product",
                    "observations": [
                        {
                            "kind": "product",
                            "description": "米色托特包",
                            "confidence": "clear",
                            "evidence_ids": [evidence],
                        }
                    ],
                    "texts": [],
                    "uncertainties": [],
                },
                "issues": [],
                "questions": [],
            }
        if role == "creative_planner":
            self.creative_calls += 1
            if self.fail_creative and self.creative_calls == 1:
                raise ProviderError(
                    "creative timeout",
                    evidence="uncertain",
                    error_code="LLM_TIMEOUT",
                )
            return self.creative_responder(request)
        raise AssertionError(f"unexpected role {role!r}")


@asynccontextmanager
async def _environment(
    settings: Settings, script: RecoveryScript
) -> AsyncIterator[tuple[Any, AsyncClient, LLMStub, VisualStub]]:
    stub = LLMStub(role_responder=script)
    visual = VisualStub()
    registry = build_test_registry(llm=stub, visual=visual)
    migrate(settings)
    application = create_app(settings=settings, registry=registry)
    async with application.router.lifespan_context(application):
        async with authenticated_client(application) as client:
            yield application, client, stub, visual


async def _create_project(client: AsyncClient) -> dict[str, Any]:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    return response.json()


async def _act(
    client: AsyncClient,
    project_id: str,
    action: str,
    *,
    expected_revision: int,
    text: str | None = None,
) -> Any:
    body: dict[str, Any] = {"expected_revision": expected_revision}
    if text is not None:
        body["text"] = text
    return await client.post(f"/api/projects/{project_id}/workflow/actions/{action}", json=body)


async def _view(client: AsyncClient, project_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/projects/{project_id}/workflow")
    assert response.status_code == 200, response.text
    return response.json()


async def _prepare_approved_brief(application: Any, project_id: str) -> str:
    ctx = await state.load_context(application, project_id)
    await state.create_run(ctx, "S04")
    ctx = await state.reload_context(ctx)
    artifact = await state.start_artifact(
        ctx,
        "brief_draft",
        {
            "product_description": "米色托特包",
            "purpose": "电商商品主图",
            "facts": [],
            "creative_direction": None,
            "style_keywords": [],
            "composition": None,
            "lighting": None,
            "texts": [],
            "must_include": [],
            "must_preserve": [],
            "forbidden": [],
            "size": "1024x1024",
            "reference_asset_id": None,
            "reference_usage": "none",
            "assumptions": [],
            "unresolved": [],
        },
    )
    await state.add_review(
        ctx,
        artifact,
        reviewer_role="output_reviewer",
        decision="pass",
        checks={
            "checks": [
                {"rule_id": "G03_BRIEF", "outcome": "pass", "evidence_ids": ["u:1"], "note": ""}
            ]
        },
    )
    return ctx.run_id


async def _role_calls(application: Any, run_id: str) -> list[RoleCall]:
    async with application.state.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(RoleCall).where(RoleCall.run_id == run_id).order_by(RoleCall.started_at)
                )
            ).all()
        )


async def _artifacts(application: Any, run_id: str, kind: str) -> list[Artifact]:
    async with application.state.session_factory() as session:
        return list(
            (
                await session.scalars(
                    select(Artifact)
                    .where(Artifact.run_id == run_id, Artifact.kind == kind)
                    .order_by(Artifact.revision_no)
                )
            ).all()
        )


async def test_text_failure_requires_explicit_continue_and_does_not_replay(full_settings):
    script = RecoveryScript(fail_requirements=True)
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        first = await _act(
            client,
            project["id"],
            "submit_request",
            text="做一张米色托特包商品主图",
            expected_revision=0,
        )
        assert first.status_code == 200, first.text
        started = first.json()
        assert started["stage"] == "S03"

        failed = await _act(
            client, project["id"], "compose_brief", expected_revision=started["project_revision"]
        )
        assert failed.status_code == 503, failed.text
        view = await _view(client, project["id"])
        assert view["stage"] == "S03"
        assert view["status"] == "failed"
        assert view["issue"]["template_id"] == "SERVICE_UNAVAILABLE"
        assert [item["id"] for item in view["available_actions"]] == ["continue_workflow"]
        assert view["artifacts"] == []
        assert view["generation_job"] is None

        async with application.state.session_factory() as session:
            calls = (await session.scalars(select(RoleCall))).all()
        assert len(calls) == 3
        assert calls[-1].role == "requirements_consultant"
        assert calls[-1].status == "error"
        assert calls[-1].error_code == "LLM_NETWORK_ERROR"
        assert script.requirements_calls == 1

        before = len(script.role_requests)
        refresh = await _view(client, project["id"])
        assert refresh["status"] == "failed"
        assert len(script.role_requests) == before

        continued = await _act(
            client, project["id"], "continue_workflow", expected_revision=view["project_revision"]
        )
        assert continued.status_code == 200, continued.text
        recovered = continued.json()
        assert recovered["stage"] == "S04"
        assert recovered["status"] == "active"
        draft = [item for item in recovered["artifacts"] if item["kind"] == "brief_draft"]
        assert len(draft) == 1 and draft[0]["status"] == "approved"
        assert script.requirements_calls == 2
        assert len(script.role_requests) == before + 2  # one producer + one reviewer
        assert visual.submit_calls == []

        duplicate = await _act(
            client, project["id"], "continue_workflow", expected_revision=view["project_revision"]
        )
        assert duplicate.status_code == 200, duplicate.text
        assert len(script.role_requests) == before + 2
        assert script.requirements_calls == 2


async def test_review_failure_resumes_same_candidate_without_reproduction(full_settings):
    script = RecoveryScript(fail_brief_review=True)
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        first = await _act(
            client, project["id"], "submit_request", text="做一张商品主图", expected_revision=0
        )
        assert first.status_code == 200, first.text
        started = first.json()
        failed = await _act(
            client, project["id"], "compose_brief", expected_revision=started["project_revision"]
        )
        assert failed.status_code == 503, failed.text
        assert failed.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        view = await _view(client, project["id"])
        assert view["stage"] == "S03"
        assert view["status"] == "failed"
        assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
        artifacts = await _artifacts(application, started["run_id"], "brief_draft")
        assert len(artifacts) == 1 and artifacts[0].status == "candidate"
        assert script.requirements_calls == 1
        assert script.brief_review_calls == 1

        continued = await _act(
            client, project["id"], "continue_workflow", expected_revision=view["project_revision"]
        )
        assert continued.status_code == 200, continued.text
        recovered = continued.json()
        assert recovered["stage"] == "S04"
        assert recovered["status"] == "active"
        assert script.requirements_calls == 1
        assert script.brief_review_calls == 2
        approved = [item for item in recovered["artifacts"] if item["kind"] == "brief_draft"]
        assert approved[0]["status"] == "approved"
        assert approved[0]["id"] == artifacts[0].id
        assert visual.submit_calls == []


async def test_creative_review_failure_resumes_same_candidate(full_settings):
    script = RecoveryScript(fail_creative_review=True)
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        await _prepare_approved_brief(application, project["id"])
        failed = await _act(client, project["id"], "plan_creative", expected_revision=0)
        assert failed.status_code == 503, failed.text
        assert failed.json()["error"]["code"] == "REVIEW_UNAVAILABLE"
        view = await _view(client, project["id"])
        assert view["stage"] == "S04"
        assert view["status"] == "blocked"
        assert view["issue"]["template_id"] == "REVIEW_UNAVAILABLE"
        assert [item["id"] for item in view["available_actions"]] == ["continue_workflow"]
        assert script.creative_calls == 1
        assert script.creative_review_calls == 1

        continued = await _act(client, project["id"], "continue_workflow", expected_revision=0)
        assert continued.status_code == 200, continued.text
        recovered = continued.json()
        assert recovered["stage"] == "S05"
        assert recovered["status"] == "active"
        assert script.creative_calls == 1
        assert script.creative_review_calls == 2
        creative = [item for item in recovered["artifacts"] if item["kind"] == "creative_direction"]
        assert creative[0]["status"] == "approved"
        assert visual.submit_calls == []


async def test_stale_recovery_after_input_change_is_rejected(full_settings):
    script = RecoveryScript(fail_requirements=True)
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        first = await _act(
            client, project["id"], "submit_request", text="做一张商品主图", expected_revision=0
        )
        assert first.status_code == 200, first.text
        started = first.json()
        failed = await _act(
            client, project["id"], "compose_brief", expected_revision=started["project_revision"]
        )
        assert failed.status_code == 503, failed.text
        view = await _view(client, project["id"])
        before = len(script.role_requests)
        async with write_transaction(application.state.session_factory) as session:
            row = await session.get(Project, project["id"])
            row.revision += 1
            await session.flush()
        new_revision = view["project_revision"] + 1
        stale = await _act(
            client, project["id"], "continue_workflow", expected_revision=new_revision
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "STALE_RECOVERY"
        assert len(script.role_requests) == before
        assert visual.submit_calls == []


async def test_recovery_budget_is_not_reset_by_refresh_or_restart(full_settings):
    script = RecoveryScript(fail_requirements=True)
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        first = await _act(
            client, project["id"], "submit_request", text="做一张商品主图", expected_revision=0
        )
        started = first.json()
        failed = await _act(
            client, project["id"], "compose_brief", expected_revision=started["project_revision"]
        )
        assert failed.status_code == 503, failed.text
        view = await _view(client, project["id"])
        async with write_transaction(application.state.session_factory) as session:
            session.add(
                RevisionBudget(
                    run_id=started["run_id"],
                    artifact_kind="brief_draft",
                    used=3,
                    max_allowed=3,
                )
            )
            await session.flush()
        before = len(script.role_requests)
        blocked = await _act(
            client, project["id"], "continue_workflow", expected_revision=view["project_revision"]
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "REVISION_BUDGET_EXHAUSTED"
        assert len(script.role_requests) == before
        assert visual.submit_calls == []

    async with _environment(full_settings, RecoveryScript()) as (
        _application,
        client,
        _stub,
        _visual,
    ):
        view = await _view(client, project["id"])
        assert view["status"] == "failed"
        response = await _act(
            client, project["id"], "continue_workflow", expected_revision=view["project_revision"]
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "REVISION_BUDGET_EXHAUSTED"


def _t15_process_request(port: int, method: str, path: str, payload: Any = None) -> tuple[int, Any]:
    return process_session(port).request(method, path, payload)


def _start_t15_server(
    settings: Settings, port: int, mode: str, call_log: Path
) -> subprocess.Popen[str]:
    from tests.test_t01_process import free_port  # noqa: F401 - import-time validation only

    prepare_process(settings)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_", "T15_"))
    }
    env.update(
        DATA_DIR=str(settings.data_dir),
        WEB_DIST_DIR=str(settings.web_dist_dir),
        PYTHONPATH=os.pathsep.join((str(MAIN_BACKEND_DIR), str(BACKEND_DIR))),
        PYTHONUNBUFFERED="1",
        LLM_ADAPTER="stub-llm",
        LLM_BASE_URL="https://llm.invalid",
        LLM_API_KEY="sk-test-t15",
        LLM_MODEL="stub-model",
        LLM_VISION_MODEL="stub-vision",
        LLM_TIMEOUT_SECONDS="5",
        VISUAL_ADAPTER="stub-visual",
        VISUAL_BASE_URL="https://visual.invalid",
        VISUAL_API_KEY="sk-test-t15",
        VISUAL_MODEL="stub-model",
        VISUAL_IMAGE_SIZE="1024x1024",
        VISUAL_TIMEOUT_SECONDS="5",
        T15_MODE=mode,
        T15_CALL_LOG=str(call_log),
    )
    env.update(process_auth_env(settings, port))
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.support.t15_recovery_app:app",
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


def test_real_process_restart_preserves_failed_checkpoint_and_manual_continue(full_settings):
    from tests.test_t01_process import free_port, stop_server, wait_for_health

    migrate(full_settings)
    call_log = Path(full_settings.data_dir).parent / "t15-calls.log"
    call_log.unlink(missing_ok=True)
    port = free_port()
    first = _start_t15_server(full_settings, port, "fail", call_log)
    try:
        wait_for_health(first, port)
        status, project = _t15_process_request(port, "POST", "/api/projects", {})
        assert status == 201, project
        status, started = _t15_process_request(
            port,
            "POST",
            f"/api/projects/{project['id']}/workflow/actions/submit_request",
            {"expected_revision": 0, "text": "做一张米色托特包商品主图"},
        )
        assert status == 200, started
        assert started["stage"] == "S03"
        status, failed = _t15_process_request(
            port,
            "POST",
            f"/api/projects/{project['id']}/workflow/actions/compose_brief",
            {"expected_revision": 1},
        )
        assert status == 503, failed
        status, view = _t15_process_request(port, "GET", f"/api/projects/{project['id']}/workflow")
        assert status == 200, view
        assert view["stage"] == "S03"
        assert view["status"] == "failed"
        assert [item["id"] for item in view["available_actions"]] == ["continue_workflow"]
        run_id = view["run_id"]
    finally:
        stop_server(first)

    port = free_port()
    second = _start_t15_server(full_settings, port, "success", call_log)
    try:
        wait_for_health(second, port)
        status, same = _t15_process_request(port, "GET", f"/api/projects/{project['id']}/workflow")
        assert status == 200, same
        assert same["run_id"] == run_id
        assert same["stage"] == "S03"
        assert same["status"] == "failed"
        status, continued = _t15_process_request(
            port,
            "POST",
            f"/api/projects/{project['id']}/workflow/actions/continue_workflow",
            {"expected_revision": 1},
        )
        assert status == 200, continued
        assert continued["stage"] == "S04"
        assert continued["status"] == "active"
        approved = [item for item in continued["artifacts"] if item["kind"] == "brief_draft"]
        assert approved and approved[0]["status"] == "approved"
    finally:
        stop_server(second)

    log = call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
    assert len([line for line in log if line.startswith("requirements:")]) == 2
    assert not any(line.startswith("submit") for line in log)


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (256, 256), (240, 240, 240)).save(output, format="PNG")
    return output.getvalue()


async def _upload_reference(client: AsyncClient, project_id: str, revision: int) -> dict[str, Any]:
    response = await client.post(
        f"/api/projects/{project_id}/reference",
        data={"expected_revision": str(revision)},
        files={"file": ("reference.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_vision_producer_failure_resumes_with_original_image(full_settings):
    script = RecoveryScript(fail_reference=True)
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        uploaded = await _upload_reference(client, project["id"], 0)
        failed = await _act(
            client,
            project["id"],
            "submit_request",
            text="做一张米色托特包商品主图",
            expected_revision=uploaded["revision"],
        )
        assert failed.status_code == 503, failed.text
        failed_view = await _view(client, project["id"])
        assert failed_view["stage"] == "S02"
        assert failed_view["status"] == "failed"
        assert [item["id"] for item in failed_view["available_actions"]] == ["continue_workflow"]
        assert script.reference_calls == 1
        assert len(stub.role_calls_received[-1].images) == 1
        assert stub.role_calls_received[-1].images[0].data == _png_bytes()

        continued = await _act(
            client,
            project["id"],
            "continue_workflow",
            expected_revision=failed_view["project_revision"],
        )
        assert continued.status_code == 200, continued.text
        recovered = continued.json()
        assert recovered["stage"] == "S03"
        assert recovered["status"] == "active"
        reports = [item for item in recovered["artifacts"] if item["kind"] == "reference_report"]
        assert reports and reports[0]["status"] == "approved"
        assert script.reference_calls == 2
        assert script.reference_review_calls == 1
        assert len(stub.role_calls_received[-2].images) == 1
        assert stub.role_calls_received[-2].images[0].data == _png_bytes()
        assert len(stub.role_calls_received[-1].images) == 1
        assert stub.role_calls_received[-1].images[0].data == _png_bytes()
        assert visual.submit_calls == []


async def test_vision_review_failure_resumes_same_report_with_original_image(full_settings):
    script = RecoveryScript(fail_reference_review=True)
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        uploaded = await _upload_reference(client, project["id"], 0)
        failed = await _act(
            client,
            project["id"],
            "submit_request",
            text="做一张米色托特包商品主图",
            expected_revision=uploaded["revision"],
        )
        assert failed.status_code == 503, failed.text
        failed_view = await _view(client, project["id"])
        assert failed_view["stage"] == "S02"
        assert failed_view["status"] == "failed"
        assert script.reference_calls == 1
        assert script.reference_review_calls == 1
        reports = await _artifacts(application, failed_view["run_id"], "reference_report")
        assert len(reports) == 1 and reports[0].status == "candidate"

        continued = await _act(
            client,
            project["id"],
            "continue_workflow",
            expected_revision=failed_view["project_revision"],
        )
        assert continued.status_code == 200, continued.text
        recovered = continued.json()
        assert recovered["stage"] == "S03"
        assert recovered["status"] == "active"
        assert script.reference_calls == 1
        assert script.reference_review_calls == 2
        assert len(stub.role_calls_received[-1].images) == 1
        assert stub.role_calls_received[-1].images[0].data == _png_bytes()
        assert visual.submit_calls == []


async def test_unconfigured_text_capability_stays_blocked_until_explicit_continue_after_fix(
    full_settings,
):
    broken = full_settings.model_copy(
        update={"llm_adapter": None, "llm_model": None, "llm_api_key": None}
    )
    script = RecoveryScript()
    async with _environment(broken, script) as (_application, client, stub, visual):
        project = await _create_project(client)
        response = await _act(
            client, project["id"], "submit_request", text="做一张商品主图", expected_revision=0
        )
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "CAPABILITY_NOT_READY"
        view = await _view(client, project["id"])
        assert view["stage"] == "S01"
        assert view["status"] == "failed"
        assert view["issue"]["template_id"] == "CAPABILITY_NOT_READY"
        assert [item["id"] for item in view["available_actions"]] == ["continue_workflow"]
        assert script.role_requests == []

        still = await _act(
            client, project["id"], "continue_workflow", expected_revision=view["project_revision"]
        )
        assert still.status_code == 503, still.text
        assert still.json()["error"]["code"] == "CAPABILITY_NOT_READY"
        assert script.role_requests == []
        assert visual.submit_calls == []

    fixed = RecoveryScript()
    async with _environment(full_settings, fixed) as (_application, client, _stub, _visual):
        view = await _view(client, project["id"])
        assert view["stage"] == "S01"
        assert view["status"] == "failed"
        continued = await _act(
            client, project["id"], "continue_workflow", expected_revision=view["project_revision"]
        )
        assert continued.status_code == 200, continued.text
        assert continued.json()["stage"] == "S03"
        assert continued.json()["status"] == "active"


async def test_unconfigured_vision_capability_blocks_until_fixed_and_resumes_original_request(
    full_settings,
):
    broken = full_settings.model_copy(update={"llm_vision_model": None})
    script = RecoveryScript()
    async with _environment(broken, script) as (_application, client, stub, visual):
        project = await _create_project(client)
        uploaded = await _upload_reference(client, project["id"], 0)
        failed = await _act(
            client,
            project["id"],
            "submit_request",
            text="做一张米色托特包商品主图",
            expected_revision=uploaded["revision"],
        )
        assert failed.status_code == 503, failed.text
        assert failed.json()["error"]["code"] == "CAPABILITY_NOT_READY"
        failed_view = await _view(client, project["id"])
        assert failed_view["stage"] == "S02"
        assert failed_view["status"] == "failed"
        assert [item["id"] for item in failed_view["available_actions"]] == ["continue_workflow"]
        assert script.reference_calls == 0

        still = await _act(
            client,
            project["id"],
            "continue_workflow",
            expected_revision=failed_view["project_revision"],
        )
        assert still.status_code == 503, still.text
        assert still.json()["error"]["code"] == "CAPABILITY_NOT_READY"
        assert script.reference_calls == 0

    fixed = RecoveryScript()
    async with _environment(full_settings, fixed) as (_application, client, _stub, _visual):
        view = await _view(client, project["id"])
        assert view["stage"] == "S02"
        assert view["status"] == "failed"
        continued = await _act(
            client, project["id"], "continue_workflow", expected_revision=view["project_revision"]
        )
        assert continued.status_code == 200, continued.text
        assert continued.json()["stage"] == "S03"
        assert fixed.reference_calls == 1
        assert fixed.reference_review_calls == 1
        assert len(fixed.role_requests[-2].images) == 1


async def test_concurrent_continue_is_single_execution(full_settings):
    script = RecoveryScript(fail_requirements=True)
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        first = await _act(client, project["id"], "submit_request", text="做一张商品主图", expected_revision=0)
        started = first.json()
        failed = await _act(client, project["id"], "compose_brief", expected_revision=started["project_revision"])
        assert failed.status_code == 503, failed.text
        view = await _view(client, project["id"])
        stub.gate = asyncio.Event()
        task = asyncio.create_task(
            _act(client, project["id"], "continue_workflow", expected_revision=view["project_revision"])
        )
        for _ in range(200):
            if script.requirements_calls == 2:
                break
            await asyncio.sleep(0.01)
        second = await _act(client, project["id"], "continue_workflow", expected_revision=view["project_revision"])
        assert second.status_code == 409, second.text
        assert second.json()["error"]["code"] == "PROJECT_BUSY"
        stub.gate.set()
        continued = await task
        assert continued.status_code == 200, continued.text
        assert continued.json()["stage"] == "S04"
        assert script.requirements_calls == 2
        assert visual.submit_calls == []


async def test_late_producer_result_is_superseded_when_input_changes(full_settings):
    script = RecoveryScript(fail_requirements=True)
    async with _environment(full_settings, script) as (application, client, stub, visual):
        project = await _create_project(client)
        started = (await _act(client, project["id"], "submit_request", text="做一张商品主图", expected_revision=0)).json()
        failed = await _act(client, project["id"], "compose_brief", expected_revision=started["project_revision"])
        assert failed.status_code == 503, failed.text
        view = await _view(client, project["id"])
        stub.gate = asyncio.Event()
        task = asyncio.create_task(
            _act(client, project["id"], "continue_workflow", expected_revision=view["project_revision"])
        )
        for _ in range(200):
            if script.requirements_calls == 2:
                break
            await asyncio.sleep(0.01)
        async with write_transaction(application.state.session_factory) as session:
            row = await session.get(Project, project["id"])
            row.revision += 1
            await session.flush()
        stub.gate.set()
        response = await task
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "STALE_RECOVERY"
        current = await _view(client, project["id"])
        assert current["stage"] == "S03"
        assert current["status"] == "blocked"
        assert current["issue"]["template_id"] == "RUN_SUPERSEDED"
        artifacts = [item for item in current["artifacts"] if item["kind"] == "brief_draft"]
        assert artifacts and artifacts[-1]["status"] == "superseded"
        assert visual.submit_calls == []

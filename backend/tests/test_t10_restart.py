from __future__ import annotations

import asyncio
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image

from app.adapters.base import BriefData, OrganizeRequest, OrganizeResult, ProviderOutcome
from app.config import Settings
from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR, migrate
from tests.support.adapters import LLMStub, VisualStub, build_test_registry
from tests.support.apps import run_client
from tests.support.process_auth import prepare_process, process_auth_env, process_session
from tests.test_t01_process import free_port, stop_server, wait_for_health


def png_bytes(width: int = 1024, height: int = 1024, color=(240, 240, 240)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color).save(output, format="PNG")
    return output.getvalue()


def review_brief(request: OrganizeRequest, **overrides) -> BriefData:
    values = {
        "product_description": "白色陶瓷马克杯，容量 350ml",
        "purpose": "电商主图",
        "style": "干净明亮",
        "composition": "居中构图",
        "text_content": "限时 9 折",
        "image_size": request.brief.image_size,
        "reference_asset_id": None,
        "prompt": "一只白色陶瓷马克杯放在浅色木桌上，柔和自然光，居中构图，电商主图",
    }
    values.update(overrides)
    return BriefData(**values)


def review_responder(**overrides):
    def respond(request: OrganizeRequest) -> OrganizeResult:
        return OrganizeResult(
            outcome="review",
            assistant_message="需求已整理完成，请确认。",
            brief=review_brief(request, **overrides),
        )

    return respond


@asynccontextmanager
async def environment(settings: Settings, visual: VisualStub):
    llm = LLMStub(responder=review_responder())
    registry = build_test_registry(llm=llm, visual=visual)
    async with run_client(settings, registry) as client:
        yield client, registry, llm


async def create_confirmed_project(client) -> dict:
    response = await client.post("/api/projects", json={})
    assert response.status_code == 201, response.text
    project = response.json()
    exchange = await client.post(
        f"/api/projects/{project['id']}/messages",
        json={"content": "做一个白色马克杯的电商主图", "expected_revision": 0},
    )
    assert exchange.status_code == 200, exchange.text
    reviewed = exchange.json()["project"]
    assert reviewed["brief_status"] == "review"
    confirmed = await client.post(
        f"/api/projects/{project['id']}/confirm",
        json={"revision": reviewed["revision"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


async def wait_status(
    client, generation_id: str, expected: set[str], timeout_seconds: float = 5.0
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last = None
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/generations/{generation_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in expected:
            return last
        await asyncio.sleep(0.01)
    raise AssertionError(f"generation did not reach {expected}: {last}")


async def wait_generation_remote(client, generation_id: str, timeout_seconds: float = 5.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last = None
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/generations/{generation_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == "running" and last["stage"] == "waiting_remote":
            return last
        await asyncio.sleep(0.01)
    raise AssertionError(f"generation did not persist a remote task: {last}")


async def wait_project_status(client, project_id: str, expected: str, timeout_seconds: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last = None
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/projects/{project_id}")
        assert response.status_code == 200, response.text
        last = response.json()["project"]
        if last["brief_status"] == expected:
            return last
        await asyncio.sleep(0.01)
    raise AssertionError(f"project did not reach {expected}: {last}")


def generation_payload(project: dict, request_id: str | None = None, **overrides) -> dict:
    payload = {
        "request_id": request_id or str(uuid4()),
        "confirmed_revision": project["revision"],
    }
    payload.update(overrides)
    return payload


def read_generation_row(settings: Settings, generation_id: str) -> dict:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM generations WHERE id = ?", (generation_id,)
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    return dict(row)


@pytest.mark.asyncio
async def test_restart_repairs_organizing_queued_and_running_once(full_settings):
    image = png_bytes()
    visual = VisualStub(submit_outcome=ProviderOutcome(state="succeeded", image_bytes=image))
    async with environment(full_settings, visual) as (client, registry, llm):
        success_project = await create_confirmed_project(client)
        success_response = await client.post(
            f"/api/projects/{success_project['id']}/generations",
            json=generation_payload(success_project),
        )
        assert success_response.status_code == 202, success_response.text
        success_generation = await wait_status(
            client, success_response.json()["generation"]["id"], {"succeeded"}
        )
        success_asset_id = success_generation["result_asset_id"]
        assert success_asset_id

        visual.submit_outcome = ProviderOutcome(
            state="pending",
            remote_task_id="remote-t10",
            result_locator="https://cdn.invalid/private-t10.png",
        )
        visual.poll_gate = asyncio.Event()
        running_project = await create_confirmed_project(client)
        running_request_id = str(uuid4())
        running_response = await client.post(
            f"/api/projects/{running_project['id']}/generations",
            json=generation_payload(running_project, running_request_id),
        )
        assert running_response.status_code == 202, running_response.text
        running_id = running_response.json()["generation"]["id"]
        running = await wait_generation_remote(client, running_id)
        assert running["remote_task_id"] == "remote-t10"

        queued_project = await create_confirmed_project(client)
        queued_request_id = str(uuid4())
        queued_response = await client.post(
            f"/api/projects/{queued_project['id']}/generations",
            json=generation_payload(queued_project, queued_request_id),
        )
        assert queued_response.status_code == 202, queued_response.text
        queued_id = queued_response.json()["generation"]["id"]
        queued = await wait_status(client, queued_id, {"queued"})
        assert queued["stage"] == "waiting_slot"
        assert queued["remote_task_id"] is None

        llm.gate = asyncio.Event()
        organizing_project_response = await client.post("/api/projects", json={})
        assert organizing_project_response.status_code == 201
        organizing_project = organizing_project_response.json()
        organizing_task = asyncio.create_task(
            client.post(
                f"/api/projects/{organizing_project['id']}/messages",
                json={"content": "BLOCK_ORGANIZE 制作海报", "expected_revision": 0},
            )
        )
        organizing = await wait_project_status(client, organizing_project["id"], "organizing")
        assert organizing["revision"] == 1
        organizing_task.cancel()
        await asyncio.gather(organizing_task, return_exceptions=True)

        submit_count = len(visual.submit_calls)
        poll_count = len(visual.poll_calls)
        llm_count = len(llm.calls)
        assert submit_count == 2
        assert poll_count == 1
        assert llm_count == 4

    assert visual.submit_calls
    assert llm.gate is not None
    llm.gate = None
    visual.poll_gate.set()

    for attempt in range(2):
        async with environment(full_settings, visual) as (client, _registry, _llm):
            if attempt == 0:
                detail = (await client.get(f"/api/projects/{success_project['id']}")).json()
                listed = next(
                    item for item in detail["generations"] if item["id"] == success_generation["id"]
                )
                assert listed["status"] == "succeeded"
                asset = await client.get(f"/api/assets/{success_asset_id}")
                assert asset.status_code == 409
                assert asset.json()["error"]["code"] == "ASSET_NOT_REVIEWED"
                stored = full_settings.generated_dir / f"{success_asset_id}.png"
                with Image.open(stored) as decoded:
                    assert decoded.size == (1024, 1024)

            running = (await client.get(f"/api/generations/{running_id}")).json()
            assert running["status"] == "unknown"
            assert running["stage"] == "waiting_remote"
            assert running["remote_task_id"] == "remote-t10"
            assert running["error"]["code"] == "PROCESS_INTERRUPTED"
            assert running["completed_at"] is not None
            assert running["result_asset_id"] is None
            row = read_generation_row(full_settings, running_id)
            assert "https://cdn.invalid/private-t10.png" in row["result_locator_json"]

            queued = (await client.get(f"/api/generations/{queued_id}")).json()
            assert queued["status"] == "unknown"
            assert queued["stage"] == "waiting_slot"
            assert queued["remote_task_id"] is None
            assert queued["error"]["code"] == "PROCESS_INTERRUPTED"
            assert queued["completed_at"] is not None

            organizing = (await client.get(f"/api/projects/{organizing_project['id']}")).json()
            assert organizing["project"]["brief_status"] == "draft"
            assert organizing["project"]["brief_error"]["code"] == "ORGANIZATION_INTERRUPTED"
            assert organizing["project"]["revision"] == 1
            assert organizing["project"]["prompt_revision"] is None
            assert organizing["project"]["confirmed_revision"] is None
            assert [message["role"] for message in organizing["messages"]] == ["user"]
            assert organizing["messages"][0]["content"].startswith("BLOCK_ORGANIZE")
            assert all(message["role"] != "assistant" for message in organizing["messages"])

            assert len(visual.submit_calls) == submit_count
            assert len(visual.poll_calls) == poll_count
            assert len(llm.calls) == llm_count

    assert len(visual.submit_calls) == submit_count
    assert len(visual.poll_calls) == poll_count
    assert len(llm.calls) == llm_count


@pytest.mark.asyncio
async def test_restart_unknown_supports_old_identity_and_acknowledged_new_attempt(full_settings):
    visual = VisualStub(
        submit_outcome=ProviderOutcome(
            state="pending",
            remote_task_id="remote-old",
            result_locator="https://cdn.invalid/private-old.png",
        )
    )
    visual.poll_gate = asyncio.Event()
    async with environment(full_settings, visual) as (client, _registry, _llm):
        project = await create_confirmed_project(client)
        request_id = str(uuid4())
        payload = generation_payload(project, request_id)
        response = await client.post(f"/api/projects/{project['id']}/generations", json=payload)
        assert response.status_code == 202, response.text
        old_id = response.json()["generation"]["id"]
        old = await wait_generation_remote(client, old_id)
        assert old["remote_task_id"] == "remote-old"

    visual.poll_gate.set()
    async with environment(full_settings, visual) as (client, _registry, _llm):
        old = (await client.get(f"/api/generations/{old_id}")).json()
        assert old["status"] == "unknown"
        assert old["remote_task_id"] == "remote-old"

        old_request = await client.post(f"/api/projects/{project['id']}/generations", json=payload)
        assert old_request.status_code == 200, old_request.text
        assert old_request.json()["generation"]["id"] == old_id
        assert old_request.json()["generation"]["version_no"] == 1

        detail = (await client.get(f"/api/projects/{project['id']}")).json()
        generation_count = len(detail["generations"])
        no_ack = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project),
        )
        assert no_ack.status_code == 409, no_ack.text
        assert no_ack.json()["error"]["code"] == "UNKNOWN_ACK_REQUIRED"
        after_no_ack = (await client.get(f"/api/projects/{project['id']}")).json()
        assert len(after_no_ack["generations"]) == generation_count

        visual.submit_outcome = ProviderOutcome(state="succeeded", image_bytes=png_bytes())
        new_request_id = str(uuid4())
        confirmed = await client.post(
            f"/api/projects/{project['id']}/generations",
            json=generation_payload(project, new_request_id, acknowledge_unknown=True),
        )
        assert confirmed.status_code == 202, confirmed.text
        new_id = confirmed.json()["generation"]["id"]
        assert new_id != old_id
        new_generation = await wait_status(client, new_id, {"succeeded"})
        assert new_generation["version_no"] == 2
        assert new_generation["request_id"] == new_request_id

        old_after = (await client.get(f"/api/generations/{old_id}")).json()
        assert old_after["status"] == "unknown"
        assert old_after["error"]["code"] == "PROCESS_INTERRUPTED"
        assert len(visual.submit_calls) == 2


def request_json(port: int, method: str, path: str, payload=None):
    return process_session(port).request(method, path, payload)


def request_bytes(port: int, path: str):
    return process_session(port).bytes(path)


def start_restart_server(
    settings: Settings, port: int, marker: Path, call_log: Path
) -> subprocess.Popen[str]:
    prepare_process(settings)
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("LLM_", "VISUAL_"))
    }
    env.update(
        DATA_DIR=str(settings.data_dir),
        WEB_DIST_DIR=str(settings.web_dist_dir),
        PYTHONPATH=os.pathsep.join((str(MAIN_BACKEND_DIR), str(BACKEND_DIR))),
        PYTHONUNBUFFERED="1",
        LLM_ADAPTER="stub-llm",
        LLM_BASE_URL="https://llm.invalid",
        LLM_API_KEY="sk-test-process",
        LLM_MODEL="stub-model",
        LLM_TIMEOUT_SECONDS="600",
        VISUAL_ADAPTER="stub-visual",
        VISUAL_BASE_URL="https://visual.invalid",
        VISUAL_API_KEY="sk-test-process",
        VISUAL_MODEL="stub-model",
        VISUAL_IMAGE_SIZE="1024x1024",
        VISUAL_TIMEOUT_SECONDS="600",
        T10_BLOCK_MARKER=str(marker),
        T10_CALL_LOG=str(call_log),
    )
    env.update(process_auth_env(settings, port))
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.support.restart_app:app",
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


def create_confirmed_project_process(port: int) -> dict:
    status, project = request_json(port, "POST", "/api/projects", {})
    assert status == 201, project
    status, exchange = request_json(
        port,
        "POST",
        f"/api/projects/{project['id']}/messages",
        {"content": "做一个白色马克杯的电商主图", "expected_revision": 0},
    )
    assert status == 200, exchange
    reviewed = exchange["project"]
    assert reviewed["brief_status"] == "review"
    status, confirmed = request_json(
        port, "POST", f"/api/projects/{project['id']}/confirm", {"revision": reviewed["revision"]}
    )
    assert status == 200, confirmed
    return confirmed


def wait_generation_process(port: int, generation_id: str, expected: str = "succeeded") -> dict:
    deadline = time.monotonic() + 20
    last = None
    while time.monotonic() < deadline:
        status, last = request_json(port, "GET", f"/api/generations/{generation_id}")
        assert status == 200, last
        if last["status"] == expected:
            return last
        time.sleep(0.05)
    raise AssertionError(f"generation did not reach {expected}: {last}")


def wait_remote_process(port: int, generation_id: str) -> dict:
    deadline = time.monotonic() + 20
    last = None
    while time.monotonic() < deadline:
        status, last = request_json(port, "GET", f"/api/generations/{generation_id}")
        assert status == 200, last
        if last["status"] == "running" and last["stage"] == "waiting_remote":
            return last
        time.sleep(0.05)
    raise AssertionError(f"generation did not persist a remote task: {last}")


def wait_project_process(port: int, project_id: str, expected: str) -> dict:
    deadline = time.monotonic() + 20
    last = None
    while time.monotonic() < deadline:
        status, detail = request_json(port, "GET", f"/api/projects/{project_id}")
        assert status == 200, detail
        last = detail["project"]
        if last["brief_status"] == expected:
            return detail
        time.sleep(0.05)
    raise AssertionError(f"project did not reach {expected}: {last}")


def post_blocking_organize(port: int, project_id: str) -> None:
    try:
        request_json(
            port,
            "POST",
            f"/api/projects/{project_id}/messages",
            {"content": "BLOCK_ORGANIZE 制作海报", "expected_revision": 0},
        )
    except Exception:
        return


def read_call_log(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def test_real_process_restart_preserves_unknown_state_and_respects_ack(full_settings):
    migrate(full_settings)
    marker = Path(full_settings.data_dir).parent / "t10-block.marker"
    call_log = Path(full_settings.data_dir).parent / "t10-calls.log"
    marker.unlink(missing_ok=True)
    call_log.unlink(missing_ok=True)
    port = free_port()
    server = start_restart_server(full_settings, port, marker, call_log)
    try:
        wait_for_health(server, port)
        success_project = create_confirmed_project_process(port)
        status, created = request_json(
            port,
            "POST",
            f"/api/projects/{success_project['id']}/generations",
            {"request_id": str(uuid4()), "confirmed_revision": success_project["revision"]},
        )
        assert status == 202, created
        success_generation = wait_generation_process(port, created["generation"]["id"])
        success_asset_id = success_generation["result_asset_id"]
        assert success_asset_id
        status, _headers, body = request_bytes(port, f"/api/assets/{success_asset_id}")
        assert status == 409
        assert json.loads(body.decode("utf-8"))["error"]["code"] == "ASSET_NOT_REVIEWED"
        with Image.open(full_settings.generated_dir / f"{success_asset_id}.png") as decoded:
            assert decoded.size == (1024, 1024)

        running_project = create_confirmed_project_process(port)
        queued_project = create_confirmed_project_process(port)
        organizing_project_status, organizing_project = request_json(
            port, "POST", "/api/projects", {}
        )
        assert organizing_project_status == 201

        marker.write_text("blocked", encoding="utf-8")
        running_payload = {
            "request_id": str(uuid4()),
            "confirmed_revision": running_project["revision"],
        }
        status, running_created = request_json(
            port,
            "POST",
            f"/api/projects/{running_project['id']}/generations",
            running_payload,
        )
        assert status == 202, running_created
        running_id = running_created["generation"]["id"]
        running = wait_remote_process(port, running_id)
        assert running["remote_task_id"] == f"remote-{running_project['id'][:8]}"

        status, queued_created = request_json(
            port,
            "POST",
            f"/api/projects/{queued_project['id']}/generations",
            {"request_id": str(uuid4()), "confirmed_revision": queued_project["revision"]},
        )
        assert status == 202, queued_created
        queued_id = queued_created["generation"]["id"]
        status, queued = request_json(port, "GET", f"/api/generations/{queued_id}")
        assert status == 200
        assert queued["status"] == "queued"
        assert queued["stage"] == "waiting_slot"

        threading.Thread(
            target=post_blocking_organize,
            args=(port, organizing_project["id"]),
            daemon=True,
        ).start()
        organizing_detail = wait_project_process(port, organizing_project["id"], "organizing")
        assert organizing_detail["project"]["revision"] == 1
        calls_before_restart = read_call_log(call_log)
        assert sum(line.startswith("submit:") for line in calls_before_restart) == 2
        assert sum(line.startswith("poll:") for line in calls_before_restart) == 1
    finally:
        stop_server(server)

    for _attempt in range(2):
        current_port = free_port()
        current = start_restart_server(full_settings, current_port, marker, call_log)
        try:
            wait_for_health(current, current_port)
            status, _headers, body = request_bytes(
                current_port, f"/api/assets/{success_asset_id}"
            )
            assert status == 409
            assert json.loads(body.decode("utf-8"))["error"]["code"] == "ASSET_NOT_REVIEWED"
            with Image.open(full_settings.generated_dir / f"{success_asset_id}.png") as decoded:
                assert decoded.size == (1024, 1024)

            status, running = request_json(current_port, "GET", f"/api/generations/{running_id}")
            assert status == 200
            assert running["status"] == "unknown"
            assert running["stage"] == "waiting_remote"
            assert running["remote_task_id"] == f"remote-{running_project['id'][:8]}"
            assert running["error"]["code"] == "PROCESS_INTERRUPTED"
            assert running["completed_at"] is not None
            assert running["result_asset_id"] is None
            row = read_generation_row(full_settings, running_id)
            assert "https://cdn.invalid/private-t10.png" in row["result_locator_json"]

            status, queued = request_json(current_port, "GET", f"/api/generations/{queued_id}")
            assert status == 200
            assert queued["status"] == "unknown"
            assert queued["stage"] == "waiting_slot"
            assert queued["remote_task_id"] is None
            assert queued["error"]["code"] == "PROCESS_INTERRUPTED"

            organizing_detail = wait_project_process(
                current_port, organizing_project["id"], "draft"
            )
            assert organizing_detail["project"]["brief_error"]["code"] == "ORGANIZATION_INTERRUPTED"
            assert organizing_detail["project"]["revision"] == 1
            assert organizing_detail["project"]["prompt_revision"] is None
            assert organizing_detail["project"]["confirmed_revision"] is None
            assert [message["role"] for message in organizing_detail["messages"]] == ["user"]
            assert read_call_log(call_log) == calls_before_restart
        finally:
            stop_server(current)

    current_port = free_port()
    current = start_restart_server(full_settings, current_port, marker, call_log)
    try:
        wait_for_health(current, current_port)
        assert read_call_log(call_log) == calls_before_restart
        marker.unlink()

        status, old_request = request_json(
            current_port,
            "POST",
            f"/api/projects/{running_project['id']}/generations",
            running_payload,
        )
        assert status == 200, old_request
        assert old_request["generation"]["id"] == running_id

        status, detail_before = request_json(
            current_port, "GET", f"/api/projects/{running_project['id']}"
        )
        assert status == 200
        generation_count = len(detail_before["generations"])
        status, no_ack = request_json(
            current_port,
            "POST",
            f"/api/projects/{running_project['id']}/generations",
            {"request_id": str(uuid4()), "confirmed_revision": running_project["revision"]},
        )
        assert status == 409, no_ack
        assert no_ack["error"]["code"] == "UNKNOWN_ACK_REQUIRED"
        status, detail_after_cancel = request_json(
            current_port, "GET", f"/api/projects/{running_project['id']}"
        )
        assert status == 200
        assert len(detail_after_cancel["generations"]) == generation_count

        status, new_created = request_json(
            current_port,
            "POST",
            f"/api/projects/{running_project['id']}/generations",
            {
                "request_id": str(uuid4()),
                "confirmed_revision": running_project["revision"],
                "acknowledge_unknown": True,
            },
        )
        assert status == 202, new_created
        new_id = new_created["generation"]["id"]
        new_generation = wait_generation_process(current_port, new_id)
        assert new_generation["version_no"] == 2
        status, old_after = request_json(current_port, "GET", f"/api/generations/{running_id}")
        assert status == 200
        assert old_after["status"] == "unknown"
        assert old_after["error"]["code"] == "PROCESS_INTERRUPTED"
        calls_after = read_call_log(call_log)
        assert sum(line.startswith("submit:") for line in calls_after) == 3
        assert sum(line.startswith("poll:") for line in calls_after) == 1
    finally:
        stop_server(current)

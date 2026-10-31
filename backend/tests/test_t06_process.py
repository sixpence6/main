from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest
from PIL import Image

from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR, migrate
from tests.support.process_auth import prepare_process, process_auth_env, process_session
from tests.test_t01_process import free_port, stop_server, wait_for_health

VISUAL_DELAY_SECONDS = 2.0


def start_generation_server(settings, port: int, marker: Path) -> subprocess.Popen[str]:
    prepare_process(settings)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_"))
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
        LLM_TIMEOUT_SECONDS="60",
        VISUAL_ADAPTER="stub-visual",
        VISUAL_BASE_URL="https://visual.invalid",
        VISUAL_API_KEY="sk-test-process",
        VISUAL_MODEL="stub-model",
        VISUAL_IMAGE_SIZE="1024x1024",
        VISUAL_TIMEOUT_SECONDS="30",
        VISUAL_DELAY_SECONDS=str(VISUAL_DELAY_SECONDS),
        VISUAL_CALL_MARKER=str(marker),
    )
    env.update(process_auth_env(settings, port))
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.support.generation_app:app",
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


def request_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    port = int(url.split(":")[2].split("/")[0])
    path = url.split(f"127.0.0.1:{port}", 1)[1]
    return process_session(port).request(method, path, payload)


def create_confirmed_project(port: int) -> dict:
    status, project = request_json("POST", f"http://127.0.0.1:{port}/api/projects", {})
    assert status == 201, project
    status, exchange = request_json(
        "POST",
        f"http://127.0.0.1:{port}/api/projects/{project['id']}/messages",
        {"content": "做一个白色马克杯的电商主图", "expected_revision": 0},
    )
    assert status == 200, exchange
    reviewed = exchange["project"]
    assert reviewed["brief_status"] == "review"
    status, confirmed = request_json(
        "POST",
        f"http://127.0.0.1:{port}/api/projects/{project['id']}/confirm",
        {"revision": reviewed["revision"]},
    )
    assert status == 200, confirmed
    return confirmed


def submit_generation(port: int, project: dict, request_id: str) -> tuple[int, dict, float]:
    started = time.monotonic()
    status, body = request_json(
        "POST",
        f"http://127.0.0.1:{port}/api/projects/{project['id']}/generations",
        {"request_id": request_id, "confirmed_revision": project["confirmed_revision"]},
    )
    return status, body, time.monotonic() - started


def wait_for_generation(port: int, generation_id: str, deadline_seconds: float = 30.0) -> dict:
    deadline = time.monotonic() + deadline_seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        _status, latest = request_json(
            "GET", f"http://127.0.0.1:{port}/api/generations/{generation_id}"
        )
        if latest["status"] in {"succeeded", "failed", "unknown"}:
            return latest
        time.sleep(0.1)
    raise AssertionError(f"generation did not finish: {latest}")


def test_real_process_returns_202_before_background_and_download_is_decodable(settings):
    migrate(settings)
    marker = Path(settings.data_dir).parent / "visual-calls.log"
    port = free_port()
    server = start_generation_server(settings, port, marker)
    try:
        wait_for_health(server, port)
        project_a = create_confirmed_project(port)
        project_b = create_confirmed_project(port)

        request_a = str(uuid.uuid4())
        status, body, elapsed = submit_generation(port, project_a, request_a)
        assert status == 202, body
        assert elapsed < VISUAL_DELAY_SECONDS
        generation_a = body["generation"]
        assert generation_a["status"] == "queued"
        assert generation_a["stage"] == "waiting_slot"

        status, duplicate, _duplicate_elapsed = submit_generation(port, project_a, request_a)
        assert status == 200, duplicate
        assert duplicate["generation"]["id"] == generation_a["id"]

        status, body_b, _elapsed_b = submit_generation(port, project_b, str(uuid.uuid4()))
        assert status == 202, body_b
        generation_b = body_b["generation"]
        assert generation_b["status"] == "queued"

        final_a = wait_for_generation(port, generation_a["id"])
        final_b = wait_for_generation(port, generation_b["id"])
        assert final_a["status"] == "succeeded", final_a
        assert final_b["status"] == "succeeded", final_b
        assert final_a["stage"] == final_b["stage"] == "done"

        events = [line.split()[0] for line in marker.read_text(encoding="utf-8").splitlines()]
        assert events == ["start", "end", "start", "end"], events

        asset_a = final_a["result_asset"]
        assert asset_a["availability"] == "available"
        # T01：真实进程同样不能绕过未审查门禁；字节仍保留在本地存储中。
        denied_status, denied_body = process_session(port).request(
            "GET", f"/api/assets/{asset_a['id']}?download=true"
        )
        assert denied_status == 409
        assert denied_body["error"]["code"] == "ASSET_NOT_REVIEWED"
        image = Image.open(settings.generated_dir / f"{asset_a['id']}.png")
        image.load()
        assert image.size == (1024, 1024)
    finally:
        stop_server(server)

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR, migrate
from tests.support.process_auth import prepare_process, process_auth_env, process_session
from tests.test_t01_process import free_port, stop_server, wait_for_health


def start_blocking_server(settings, port: int, marker: Path) -> subprocess.Popen[str]:
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
        LLM_TIMEOUT_SECONDS="600",
        ORGANIZE_CALL_MARKER=str(marker),
    )
    env.update(process_auth_env(settings, port))
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.support.blocking_app:app",
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


def start_normal_server(settings, port: int) -> subprocess.Popen[str]:
    prepare_process(settings)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_"))
    }
    env.update(
        DATA_DIR=str(settings.data_dir),
        WEB_DIST_DIR=str(settings.web_dist_dir),
        PYTHONPATH=str(MAIN_BACKEND_DIR),
        PYTHONUNBUFFERED="1",
    )
    env.update(process_auth_env(settings, port))
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
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


def post_message(project_id: str, port: int) -> None:
    try:
        process_session(port).request(
            "POST",
            f"/api/projects/{project_id}/messages",
            {"content": "进程中断前的消息", "expected_revision": 0},
        )
    except Exception:
        return


def get_json(port: int, path: str) -> dict:
    status, body = process_session(port).request("GET", path)
    assert status == 200, body
    return body


def test_organizing_state_is_repaired_after_real_process_kill(settings):
    migrate(settings)
    marker = Path(settings.data_dir).parent / "organize.marker"
    port = free_port()
    blocking = start_blocking_server(settings, port, marker)
    try:
        wait_for_health(blocking, port)
        status, project = process_session(port).request("POST", "/api/projects", {})
        assert status == 201, project
        thread = threading.Thread(target=post_message, args=(project["id"], port), daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        detail = None
        while time.monotonic() < deadline:
            detail = get_json(port, f"/api/projects/{project['id']}")
            if detail["project"]["brief_status"] == "organizing":
                break
            time.sleep(0.1)
        assert detail is not None and detail["project"]["brief_status"] == "organizing"
        assert marker.read_text(encoding="utf-8") == "called"
    finally:
        stop_server(blocking)

    for _attempt in range(2):
        current_port = free_port()
        server = start_normal_server(settings, current_port)
        try:
            wait_for_health(server, current_port)
            detail = get_json(current_port, f"/api/projects/{project['id']}")
            assert detail["project"]["brief_status"] == "draft"
            assert detail["project"]["brief_error"]["code"] == "ORGANIZATION_INTERRUPTED"
            assert detail["project"]["revision"] == 1
            assert detail["project"]["prompt_revision"] is None
            assert detail["project"]["confirmed_revision"] is None
            assert [message["role"] for message in detail["messages"]] == ["user"]
            assert detail["messages"][0]["content"] == "进程中断前的消息"
        finally:
            stop_server(server)
        assert marker.read_text(encoding="utf-8") == "called"

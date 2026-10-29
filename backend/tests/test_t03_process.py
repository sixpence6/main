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
from tests.test_t01_process import free_port, stop_server, wait_for_health


def start_blocking_server(settings, port: int, marker: Path) -> subprocess.Popen[str]:
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
    payload = json.dumps({"content": "进程中断前的消息", "expected_revision": 0}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/projects/{project_id}/messages",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=30)
    except Exception:
        return


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_organizing_state_is_repaired_after_real_process_kill(settings):
    migrate(settings)
    marker = Path(settings.data_dir).parent / "organize.marker"
    port = free_port()
    blocking = start_blocking_server(settings, port, marker)
    try:
        wait_for_health(blocking, port)
        payload = json.dumps({}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/projects",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            project = json.loads(response.read().decode("utf-8"))
        thread = threading.Thread(target=post_message, args=(project["id"], port), daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        detail = None
        while time.monotonic() < deadline:
            detail = get_json(f"http://127.0.0.1:{port}/api/projects/{project['id']}")
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
            detail = get_json(
                f"http://127.0.0.1:{current_port}/api/projects/{project['id']}"
            )
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

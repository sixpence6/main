from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tests.conftest import BACKEND_DIR, MAIN_BACKEND_DIR, migrate


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start_server(settings, port: int) -> subprocess.Popen[str]:
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
    process = subprocess.Popen(
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
    return process


def wait_for_health(
    process: subprocess.Popen[str],
    port: int,
    deadline_seconds: float = 45.0,
) -> dict:
    deadline = time.monotonic() + deadline_seconds
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            raise AssertionError(f"server exited early with {process.returncode}:\n{output}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.2)
    output = process.stdout.read() if process.stdout else ""
    raise AssertionError(f"server did not become healthy in time:\n{output}")


def stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


@pytest.fixture
def dist(settings) -> Path:
    directory = settings.resolved_web_dist_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.html").write_text("<html>workspace</html>", encoding="utf-8")
    return directory


def test_single_worker_process_boots_and_restarts_on_same_data_dir(settings, dist):
    migrate(settings)
    port = free_port()
    first = start_server(settings, port)
    try:
        assert wait_for_health(first, port) == {"status": "ok"}
        status_url = f"http://127.0.0.1:{port}/api/config/status"
        with urllib.request.urlopen(status_url, timeout=5) as response:
            assert response.status == 200
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert b"workspace" in response.read()
    finally:
        stop_server(first)

    second_port = free_port()
    second = start_server(settings, second_port)
    try:
        assert wait_for_health(second, second_port) == {"status": "ok"}
    finally:
        stop_server(second)

    assert (settings.resolved_data_dir() / "app.db").exists()

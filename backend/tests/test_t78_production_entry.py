from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

from app.config import MAIN_DIR, REPO_ROOT, Settings
from app.production import ENTRYPOINT, runtime_info
from app.startup import BACKEND_DIR, head_revision
from tests.conftest import migrate

ROOT = Path(REPO_ROOT)
MAIN_BACKEND = Path(MAIN_DIR) / "backend"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_production_entry_layout_uses_one_source_and_aligned_artifacts():
    assert ENTRYPOINT == "app.main:app"
    assert (MAIN_BACKEND / "app" / "production.py").is_file()
    assert not (ROOT / "backend" / "app").exists()

    root_project = tomllib.loads(_read(ROOT / "backend" / "pyproject.toml"))
    main_project = tomllib.loads(_read(MAIN_BACKEND / "pyproject.toml"))
    assert root_project["project"]["dependencies"] == main_project["project"]["dependencies"]
    assert (ROOT / "backend" / "uv.lock").read_bytes() == (
        MAIN_BACKEND / "uv.lock"
    ).read_bytes()

    assert (ROOT / "package.json").read_bytes() == (ROOT / "main" / "package.json").read_bytes()
    assert (ROOT / "package-lock.json").read_bytes() == (
        ROOT / "main" / "package-lock.json"
    ).read_bytes()

    migration_dir = MAIN_BACKEND / "migrations" / "versions"
    root_migration_dir = ROOT / "backend" / "migrations" / "versions"
    migration_names = sorted(path.name for path in migration_dir.glob("*.py"))
    root_names = sorted(path.name for path in root_migration_dir.glob("*.py"))
    assert root_names == migration_names
    for name in migration_names:
        assert (root_migration_dir / name).read_bytes() == (
            migration_dir / name
        ).read_bytes()
    assert head_revision() == "0010"

    root_backend_docker = _read(ROOT / "docker" / "backend.Dockerfile")
    assert "COPY main/backend/pyproject.toml main/backend/uv.lock ./" in root_backend_docker
    assert "COPY main/backend/migrations ./migrations" in root_backend_docker
    assert "COPY main/backend/app ./app" in root_backend_docker
    assert "python -m app.production" in root_backend_docker

    main_backend_docker = _read(ROOT / "main" / "docker" / "backend.Dockerfile")
    assert "COPY backend/pyproject.toml backend/uv.lock ./" in main_backend_docker
    assert "COPY backend/migrations ./migrations" in main_backend_docker
    assert "COPY backend/app ./app" in main_backend_docker
    assert "python -m app.production" in main_backend_docker

    root_frontend_docker = _read(ROOT / "docker" / "Dockerfile")
    assert "COPY main/package.json main/package-lock.json ./" in root_frontend_docker
    assert "COPY main/src ./src" in root_frontend_docker
    assert "COPY main/docker/nginx.conf.template" in root_frontend_docker

    wrapper = _read(ROOT / "backend" / "run.py")
    assert "from app.production import main" in wrapper
    assert '_MAIN_BACKEND' in wrapper
    assert '"main" / "backend"' in wrapper


def test_runtime_info_reports_effective_paths_and_migration_head(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        web_dist_dir=tmp_path / "web",
        allowed_hosts=["127.0.0.1"],
        allowed_origins=["https://127.0.0.1"],
        allow_loopback_http=True,
        auth_cookie_secure=True,
        app_public_url="https://127.0.0.1",
    )
    info = runtime_info(settings)
    assert info.entrypoint == "app.main:app"
    assert info.import_root == str((MAIN_BACKEND / "app").resolve())
    assert info.backend_dir == str(MAIN_BACKEND.resolve())
    assert info.workspace_dir == str(BACKEND_DIR.resolve())
    assert info.data_dir == str((tmp_path / "data").resolve())
    assert info.database_path == str((tmp_path / "data" / "app.db").resolve())
    assert info.web_dist_dir == str((tmp_path / "web").resolve())
    assert info.migration_head == "0010"


def test_production_check_command_is_side_effect_free(tmp_path: Path):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_"))
    }
    env.update(
        APP_DATA_DIR=str(tmp_path / "data"),
        WEB_DIST_DIR=str(tmp_path / "web"),
        PYTHONPATH=str(MAIN_BACKEND),
        PYTHONUNBUFFERED="1",
    )
    completed = subprocess.run(
        [sys.executable, "-m", "app.production", "--check", "--json"],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["entrypoint"] == "app.main:app"
    assert payload["migration_head"] == "0010"
    assert payload["data_dir"] == str((tmp_path / "data").resolve())
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "web").exists()


def test_production_entry_boots_fresh_migrated_sqlite_and_serves_health(
    settings: Settings,
):
    migrate(settings)
    web = settings.resolved_web_dist_dir()
    web.mkdir(parents=True, exist_ok=True)
    (web / "index.html").write_text("<html>production entry</html>", encoding="utf-8")
    port = _free_port()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_"))
    }
    env.update(
        APP_DATA_DIR=str(settings.data_dir),
        WEB_DIST_DIR=str(settings.web_dist_dir),
        ALLOW_LOOPBACK_HTTP="true",
        AUTH_COOKIE_SECURE="false",
        APP_PUBLIC_URL=f"http://127.0.0.1:{port}",
        PYTHONPATH=str(MAIN_BACKEND),
        PYTHONUNBUFFERED="1",
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "app.production",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 45
        health = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"production entry exited early: {output}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=2
                ) as response:
                    health = json.loads(response.read().decode("utf-8"))
                break
            except OSError:
                time.sleep(0.2)
        assert health == {"status": "ok"}
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert b"production entry" in response.read()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/config/status", timeout=5
        ) as response:
            assert response.status == 200
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)


def test_workspace_wrapper_and_legacy_uvicorn_entry_remain_compatible(tmp_path: Path):
    check_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_"))
    }
    check_env.update(
        APP_DATA_DIR=str(tmp_path / "wrapper-data"),
        WEB_DIST_DIR=str(tmp_path / "wrapper-web"),
        PYTHONUNBUFFERED="1",
    )
    completed = subprocess.run(
        [sys.executable, str(ROOT / "backend" / "run.py"), "--check", "--json"],
        cwd=str(ROOT),
        env=check_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["entrypoint"] == "app.main:app"
    assert payload["import_root"] == str((MAIN_BACKEND / "app").resolve())

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "legacy-data",
        web_dist_dir=tmp_path / "legacy-web",
        allowed_hosts=["127.0.0.1"],
        allowed_origins=["https://127.0.0.1"],
        allow_loopback_http=True,
        auth_cookie_secure=True,
        app_public_url="https://127.0.0.1",
    )
    migrate(settings)
    web = settings.resolved_web_dist_dir()
    web.mkdir(parents=True, exist_ok=True)
    (web / "index.html").write_text("<html>legacy entry</html>", encoding="utf-8")
    port = _free_port()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LLM_", "VISUAL_"))
    }
    env.update(
        APP_DATA_DIR=str(settings.data_dir),
        WEB_DIST_DIR=str(settings.web_dist_dir),
        ALLOW_LOOPBACK_HTTP="true",
        AUTH_COOKIE_SECURE="false",
        APP_PUBLIC_URL=f"http://127.0.0.1:{port}",
        PYTHONPATH=str(MAIN_BACKEND),
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
        cwd=str(ROOT / "backend"),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 45
        health = None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise AssertionError(f"legacy entry exited early: {output}")
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=2
                ) as response:
                    health = json.loads(response.read().decode("utf-8"))
                break
            except OSError:
                time.sleep(0.2)
        assert health == {"status": "ok"}
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
            assert b"legacy entry" in response.read()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)

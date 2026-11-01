from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "release_package.py"
MAIN_DIST = ROOT / "main" / "dist"


def _load_tool():
    spec = importlib.util.spec_from_file_location("release_package", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_release_manifest_is_the_only_package_contract_and_rejects_forbidden_files():
    tool = _load_tool()
    manifest = tool.load_manifest()
    assert manifest.package == "ecommerce-image-workspace"
    assert manifest.version == "0.1.0"
    assert manifest.source_root == ROOT / "main"
    assert "backend/app/production.py" in manifest.required
    assert "tests/test_t79_release_package.py" not in manifest.include

    assert tool.forbidden_reason("backend/.env", manifest)
    assert tool.forbidden_reason(".env", manifest)
    assert tool.forbidden_reason("tests/test_app.py", manifest)
    assert tool.forbidden_reason("node_modules/pkg/index.js", manifest)
    assert tool.forbidden_reason("backend/.venv/bin/python", manifest)
    assert tool.forbidden_reason("backend/data/app.db", manifest)
    assert tool.forbidden_reason("backend/runtime.log", manifest)
    assert tool.forbidden_reason("backend/.env.example", manifest) is None
    assert tool.forbidden_reason("backend/app/main.py", manifest) is None


def test_release_manifest_check_is_side_effect_free_and_successful():
    completed = subprocess.run(
        [sys.executable, str(TOOL), "--check", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["selected_tracked_files"] > 100
    assert payload["issues"] == []


@pytest.mark.skipif(
    not (MAIN_DIST / "index.html").is_file(),
    reason="frontend dist must exist for release artifact build evidence",
)
def test_release_artifact_excludes_secrets_tests_data_and_has_verifiable_hashes(tmp_path: Path):
    output = tmp_path / "artifacts"
    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--build",
            "--no-build",
            "--output-root",
            str(output),
            "--json",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    package_dir = Path(result["directory"])
    assert package_dir.is_dir()
    assert Path(result["zip"]).is_file()
    assert package_dir.parent == output

    names = {
        path.relative_to(package_dir).as_posix()
        for path in package_dir.rglob("*")
        if path.is_file()
    }
    assert "README.md" in names
    assert "THIRD_PARTY_NOTICES.md" in names
    assert "RELEASE-MANIFEST.json" in names
    assert "SHA256SUMS" in names
    assert "backend/data/.gitkeep" in names
    assert "dist/index.html" in names
    assert not any(name == ".env" or name.endswith("/.env") for name in names)
    assert not any(
        part in {"tests", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
        for name in names
        for part in Path(name).parts
    )
    assert not any(name.endswith((".db", ".db-wal", ".db-shm", ".pyc", ".log")) for name in names)

    manifest = json.loads((package_dir / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["package"] == "ecommerce-image-workspace"
    assert manifest["migration_head"] == "0010"
    assert manifest["source_commit"] == subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    for entry in manifest["files"]:
        path = package_dir / entry["path"]
        assert path.is_file()
        assert _sha256(path) == entry["sha256"], entry["path"]
        assert path.stat().st_size == entry["size"]

    checksums = (package_dir / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert checksums
    for line in checksums:
        digest, name = line.split("  ", 1)
        assert digest == _sha256(package_dir / name)

    with zipfile.ZipFile(result["zip"]) as archive:
        archive_names = archive.namelist()
        assert any(name.endswith("/README.md") for name in archive_names)
        assert not any("/tests/" in name or name.endswith("/.env") for name in archive_names)

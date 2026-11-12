"""Behavioral deployment checks; synthetic dist does not prove production builds."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FAKE_ENV = {
    "PUBLIC_HOSTNAME": "192.0.2.10",
    "PUBLIC_ORIGIN": "https://192.0.2.10:8443",
    "PUBLIC_BIND_ADDRESS": "192.0.2.10",
    "PUBLIC_HTTPS_PORT": "8443",
    "TLS_CERT_DIR": "./fake-certs",
}


def _tool():
    spec = importlib.util.spec_from_file_location(
        "review_deployment_release_tool", ROOT / "tools/release_package.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _compose(directory: Path, lan: bool) -> dict:
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker CLI required for authoritative Compose parsing")
    command = [docker, "compose", "-f", str(directory / "docker-compose.yml")]
    if lan:
        command += ["-f", str(directory / "docker-compose.lan.yml")]
    # Do not resolve developer env files or use a local .env for interpolation.
    command += ["--env-file", os.devnull, "config", "--no-env-resolution", "--format", "json"]
    result = subprocess.run(
        command, cwd=directory, env={**os.environ, **FAKE_ENV},
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _copy_inputs(dockerfile: Path, args: dict | None = None) -> list[Path]:
    """Resolve local COPY inputs with ARG defaults and Compose overrides."""
    values = {}
    sources = []
    text = re.sub(r"\\\r?\n", " ", dockerfile.read_text(encoding="utf-8"))
    for line in text.splitlines():
        tokens = shlex.split(line, comments=True)
        if not tokens:
            continue
        if tokens[0].upper() == "ARG" and "=" in tokens[1]:
            name, value = tokens[1].split("=", 1)
            values[name] = value
        if tokens[0].upper() != "COPY" or any(t.startswith("--from=") for t in tokens):
            continue
        for token in [t for t in tokens[1:] if not t.startswith("--")][:-1]:
            variables = {**values, **(args or {})}
            expanded = re.sub(
                r"\$\{(\w+)\}|\$(\w+)",
                lambda match, variables=variables: variables[match[1] or match[2]], token,
            )
            sources.append(Path(expanded))
    assert sources, f"no local COPY inputs found in {dockerfile}"
    return sources


def _assert_layout(directory: Path, context: Path, lan: bool) -> None:
    services = _compose(directory, lan)["services"]
    for name in ("frontend", "backend"):
        build = services[name]["build"]
        build_context = Path(build["context"]).resolve()
        if name == "frontend":
            assert build_context == context.resolve()
        dockerfile = build_context / build["dockerfile"]
        assert dockerfile.is_file(), dockerfile
        inputs = _copy_inputs(dockerfile, build.get("args"))
        for source in inputs:
            resolved = (build_context / source).resolve()
            assert resolved.is_relative_to(build_context), source
            assert resolved.exists(), f"{name} COPY input missing in build context: {resolved}"
        if name == "frontend":
            templates = [p for p in inputs if p.name.endswith(".conf.template")]
            assert len(templates) == 1, templates
            template = (build_context / templates[0]).read_text(encoding="utf-8")
            if lan:
                assert "ssl_certificate" in template
                assert "443" in template
            else:
                assert "listen 80" in template
                assert "ssl_certificate " not in template


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    scratch = tmp_path_factory.mktemp("review-deployment")
    dist = scratch / "synthetic-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<script src="/assets/probe.js"></script>', encoding="utf-8")
    (dist / "assets/probe.js").write_text(
        "console.log('synthetic release probe')", encoding="utf-8"
    )
    tool = _tool()
    manifest = replace(tool.load_manifest(), dist_dir=dist)
    result = tool.build_package(manifest, scratch / "artifacts", do_build=False, require_dist=True)
    archive_path = Path(result["zip"])
    extracted = scratch / "extracted"
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        assert all(not Path(n).is_absolute() and ".." not in Path(n).parts for n in names)
        archive.extractall(extracted)
    roots = list(extracted.iterdir())
    assert len(roots) == 1 and roots[0].is_dir()
    return Path(result["directory"]), roots[0], archive_path


@pytest.mark.parametrize("lan", [False, True], ids=["http", "lan"])
def test_repository_compose_copy_inputs_exist_in_build_context(lan):
    _assert_layout(ROOT / "main", ROOT, lan)


@pytest.mark.parametrize("layout", [0, 1], ids=["export-directory", "independent-zip"])
@pytest.mark.parametrize("lan", [False, True], ids=["http", "lan"])
def test_standalone_compose_copy_inputs_exist_in_build_context(exported, layout, lan):
    directory = exported[layout]
    _assert_layout(directory, directory, lan)


def test_repository_has_one_hoisted_lock():
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert "main" in lock["packages"]
    assert not any(k.startswith("main/node_modules/") for k in lock["packages"])
    assert not list((ROOT / "main").rglob("package-lock.json")), "nested product lock"


@pytest.mark.parametrize("layout", [0, 1], ids=["export-directory", "independent-zip"])
def test_standalone_lock_is_self_contained_without_workspace_or_parent(exported, layout):
    directory = exported[layout]
    package = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((directory / "package-lock.json").read_text(encoding="utf-8"))
    assert "workspaces" not in package
    assert lock["packages"][""]["name"] == package["name"]
    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        assert lock["packages"][""].get(field, {}) == package.get(field, {})
        for name in package.get(field, {}):
            assert f"node_modules/{name}" in lock["packages"], name
    assert "main" not in lock["packages"]
    assert not any(value.get("link") for value in lock["packages"].values())
    assert all(key == "" or key.startswith("node_modules/") for key in lock["packages"])
    assert list(directory.rglob("package-lock.json")) == [directory / "package-lock.json"]


def test_independent_zip_bytes_match_export_and_verify_without_source_tree(exported):
    original, extracted, _ = exported
    original_files = {
        p.relative_to(original).as_posix() for p in original.rglob("*") if p.is_file()
    }
    extracted_files = {
        p.relative_to(extracted).as_posix() for p in extracted.rglob("*") if p.is_file()
    }
    assert original_files == extracted_files
    assert {"dist/index.html", "dist/assets/probe.js"} <= extracted_files
    for name in extracted_files:
        assert (extracted / name).read_bytes() == (original / name).read_bytes(), name
    manifest = json.loads((extracted / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
    listed = {item["path"] for item in manifest["files"]}
    assert listed == extracted_files - {"RELEASE-MANIFEST.json", "SHA256SUMS"}
    for item in manifest["files"]:
        data = (extracted / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"], item["path"]
        assert len(data) == item["size"]
    checksums = {}
    for line in (extracted / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        checksums[name] = digest
    assert set(checksums) == listed
    for name, digest in checksums.items():
        assert hashlib.sha256((extracted / name).read_bytes()).hexdigest() == digest
    assert not any(p.is_symlink() for p in extracted.rglob("*"))


def test_workspace_build_context_has_its_own_ignore_policy():
    assert (ROOT / ".dockerignore").is_file()


def test_root_ignore_actually_filters_fake_secrets_and_keeps_build_inputs(tmp_path):
    ignore = ROOT / ".dockerignore"
    assert ignore.is_file(), "repository context needs its own .dockerignore"
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker CLI unavailable: real build-context sentinel probe not run")
    try:
        probe = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("Docker daemon did not reply within 15s; sentinel build not run")
    if probe.returncode:
        detail = probe.stderr.strip()[:300]
        pytest.skip(f"Docker daemon unavailable; sentinel build not run: {detail}")
    context = tmp_path / "fake-context"
    context.mkdir()
    shutil.copyfile(ignore, context / ".dockerignore")
    allowed = {"package.json", "package-lock.json", "main/package.json", "main/src/main.ts",
               "main/tsconfig.json", "main/vite.config.ts", "main/index.html"}
    for lan in (False, True):
        build = _compose(ROOT / "main", lan)["services"]["frontend"]["build"]
        for source in _copy_inputs(ROOT / build["dockerfile"], build.get("args")):
            actual = ROOT / source
            allowed.add((source / "fake-input.txt" if actual.is_dir() else source).as_posix())
    forbidden = {
        ".env", ".env.production", "backend/.env", "backend/data/fake.db",
        "main/backend/.env", "main/backend/.env.production", "main/backend/data/fake.db",
        "main/backend/data/fake.db-wal", "main/backend/data/uploads/fake.png",
        "main/backend/.venv/fake.txt", "main/backend/__pycache__/fake.pyc",
        "main/backups/fake.zip", "main/certs/server.key", "main/certs/server.crt",
        "main/.scratch/fake.txt", "main/node_modules/fake/index.js",
        "node_modules/fake/index.js", "nested/private.pem", "nested/.env.local",
        "nested/fake.sqlite3", "nested/fake.log",
        "main/src/.env", "main/src/.env.production", "main/src/data/fake.png",
        "main/src/backups/fake.zip", "main/src/fake.key", "main/docker/private.pem",
    }
    for name in allowed | forbidden:
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("FAKE TEST SENTINEL ONLY", encoding="utf-8")
    dockerfile = tmp_path / "sentinel.Dockerfile"
    dockerfile.write_text("FROM scratch\nCOPY . /\n", encoding="utf-8")
    output = tmp_path / "captured-context"
    result = subprocess.run(
        [docker, "buildx", "build", "--network=none", "--no-cache", "--progress=plain",
         "--file", str(dockerfile), "--output", f"type=local,dest={output}", str(context)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    captured = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    assert not (captured & forbidden), f"fake secrets leaked: {captured & forbidden}"
    assert allowed <= captured, f"required build inputs excluded: {allowed - captured}"




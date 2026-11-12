#!/usr/bin/env python3
"""Build and verify the self-contained release package from an explicit manifest.

The manifest is the contract: only paths listed under "include" can enter the
artifact.  The tool never copies arbitrary files from the working tree.  All
paths are relative to the declared source root (main/).

Examples:
    python tools/release_package.py --check
    python tools/release_package.py --build
    python tools/release_package.py --build --output-root release-artifacts
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import subprocess
import sys
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "release" / "package-manifest.json"
DEFAULT_OUTPUT_ROOT = ROOT / "release-artifacts"


@dataclass(frozen=True)
class Manifest:
    package: str
    version: str
    source_root: Path
    include: tuple[str, ...]
    required: tuple[str, ...]
    forbidden_components: frozenset[str]
    forbidden_files: frozenset[str]
    forbidden_suffixes: tuple[str, ...]
    generated: tuple[str, ...]
    build_command: str | None
    dist_dir: Path


def load_manifest() -> Manifest:
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if raw.get("schema") != 1:
        raise SystemExit("manifest schema must be 1")
    if raw.get("source_root") != "main":
        raise SystemExit("manifest source_root must be main")
    source_root = ROOT / raw["source_root"]
    package_json = json.loads((source_root / raw["version_source"]).read_text(encoding="utf-8"))
    build = raw.get("build") or {}
    return Manifest(
        package=raw["package"],
        version=package_json["version"],
        source_root=source_root,
        include=tuple(raw["include"]),
        required=tuple(raw["required"]),
        forbidden_components=frozenset(raw["forbidden_components"]),
        forbidden_files=frozenset(raw["forbidden_files"]),
        forbidden_suffixes=tuple(raw["forbidden_suffixes"]),
        generated=tuple(raw["generated"]),
        build_command=build.get("command"),
        dist_dir=ROOT / build["dist_dir"] if build.get("dist_dir") else source_root / "dist",
    )


def tracked_files(source_root: Path) -> list[str]:
    """Return tracked and non-ignored new source files for the working-tree snapshot."""
    rel_root = source_root.relative_to(ROOT).as_posix()
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others",
         "--exclude-standard", "-z", "--", rel_root],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit("git ls-files failed: " + proc.stderr.decode("utf-8", "replace"))
    result: list[str] = []
    prefix = rel_root + "/"
    for item in proc.stdout.decode("utf-8").split("\0"):
        if not item:
            continue
        if not item.startswith(prefix):
            continue
        rel = item[len(prefix):]
        if rel:
            result.append(rel)
    return sorted(set(result))


def dist_files(dist_dir: Path) -> list[str]:
    if not dist_dir.is_dir():
        return []
    files: list[str] = []
    for p in sorted(dist_dir.rglob("*")):
        if p.is_symlink():
            raise SystemExit(f"refusing symlink in dist: {p}")
        if p.is_file():
            files.append("dist/" + p.relative_to(dist_dir).as_posix())
    return files


def is_included(rel: str, manifest: Manifest) -> bool:
    return any(fnmatch.fnmatchcase(rel, pattern) for pattern in manifest.include)


def forbidden_reason(rel: str, manifest: Manifest) -> str | None:
    parts = set(Path(rel).parts)
    bad = parts & manifest.forbidden_components
    if bad:
        return "forbidden component: " + ", ".join(sorted(bad))
    if Path(rel).name.startswith(".env") and Path(rel).name != ".env.example":
        return "forbidden environment file"
    if rel in manifest.forbidden_files:
        return "forbidden secret/config file"
    for suffix in manifest.forbidden_suffixes:
        if rel.endswith(suffix):
            return "forbidden suffix: " + suffix
    return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str | None:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def validate(manifest: Manifest, require_dist: bool = False) -> tuple[list[str], list[str], list[str]]:
    issues: list[str] = []
    selected: list[str] = []
    tracked = tracked_files(manifest.source_root)
    if not tracked:
        issues.append("no tracked files found under source root")

    for rel in manifest.required:
        if not (manifest.source_root / rel).is_file():
            issues.append("missing required file: " + rel)

    if not (ROOT / "package-lock.json").is_file():
        issues.append("workspace package-lock.json is missing")

    matched_patterns = {pattern: False for pattern in manifest.include}
    for rel in tracked:
        for pattern in manifest.include:
            if fnmatch.fnmatchcase(rel, pattern):
                matched_patterns[pattern] = True
        if not is_included(rel, manifest):
            continue
        reason = forbidden_reason(rel, manifest)
        if reason:
            issues.append(f"selected file violates package policy: {rel} ({reason})")
            continue
        source = manifest.source_root / rel
        if (source.is_symlink() or not source.is_file()
                or not source.resolve().is_relative_to(manifest.source_root.resolve())):
            issues.append("selected source is missing or a symlink: " + rel)
            continue
        selected.append(rel)

    for pattern, matched in matched_patterns.items():
        if not matched and not pattern.startswith("dist/"):
            issues.append("include pattern matched no tracked file: " + pattern)

    dist = dist_files(manifest.dist_dir)
    if not dist:
        message = "frontend dist is missing; run the build before packaging"
        if require_dist:
            issues.append(message)
        else:
            print("warning: " + message, file=sys.stderr)

    for rel in dist:
        if not is_included(rel, manifest):
            issues.append("dist file is outside manifest: " + rel)
        reason = forbidden_reason(rel, manifest)
        if reason:
            issues.append(f"dist file violates package policy: {rel} ({reason})")

    # A release artifact must never include the live backend data directory.
    selected_real = [rel for rel in selected if rel.startswith("backend/data/")]
    if selected_real:
        issues.append("live backend/data files are selected: " + ", ".join(selected_real))

    return selected, dist, issues


def run_build(manifest: Manifest) -> None:
    if not manifest.build_command:
        raise SystemExit("manifest has no build command")
    print("running: " + manifest.build_command)
    proc = subprocess.run(
        manifest.build_command,
        cwd=str(ROOT),
        shell=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"frontend build failed with exit code {proc.returncode}")


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def migration_head(source_root: Path) -> str:
    versions = source_root / "backend" / "migrations" / "versions"
    heads = sorted(
        path.name[:4]
        for path in versions.glob("[0-9][0-9][0-9][0-9]_*.py")
        if path.name[:4].isdigit()
    )
    if not heads:
        raise SystemExit(f"no migration versions found under {versions}")
    return heads[-1]


def materialize_standalone_layout(package_dir: Path) -> list[str]:
    """Adapt the single-workspace lock and Docker inputs without changing dev layout."""
    package = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    if "main" not in packages or any(k.startswith("main/node_modules/") for k in packages):
        raise SystemExit("release requires a hoisted single-workspace dependency lock")
    root_entry = {k: v for k, v in package.items() if k in {
        "name", "version", "dependencies", "devDependencies", "optionalDependencies", "engines"
    }}
    locked_main = packages["main"]
    for field in ("dependencies", "devDependencies", "optionalDependencies"):
        if package.get(field, {}) != locked_main.get(field, {}):
            raise SystemExit("workspace lock is stale for " + field)
    lock["name"] = package["name"]
    lock["version"] = package["version"]
    lock["packages"] = {"": root_entry, **{
        k: v for k, v in packages.items() if k.startswith("node_modules/") and not v.get("link")
    }}
    (package_dir / "package-lock.json").write_text(
        json.dumps(lock, indent=2) + "\n", encoding="utf-8"
    )
    dockerfile = package_dir / "docker" / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")
    text = text.replace("COPY main/package.json ./main/package.json\n", "")
    text = text.replace("npm ci --workspace main", "npm ci")
    text = text.replace("WORKDIR /app/main", "WORKDIR /app")
    text = text.replace("main/", "")
    dockerfile.write_text(text, encoding="utf-8")
    compose = package_dir / "docker-compose.yml"
    text = compose.read_text(encoding="utf-8").replace("context: ..", "context: .")
    text = text.replace("dockerfile: main/docker/Dockerfile", "dockerfile: docker/Dockerfile")
    compose.write_text(text, encoding="utf-8")
    # The LAN overlay shares the frontend context with the base compose file.
    # Its template is rooted at main/ in development and at docker/ in the ZIP.
    overlay = package_dir / "docker-compose.lan.yml"
    if overlay.is_file():
        text = overlay.read_text(encoding="utf-8").replace(
            "NGINX_TEMPLATE: main/docker/nginx-tls.conf.template",
            "NGINX_TEMPLATE: docker/nginx-tls.conf.template",
        )
        overlay.write_text(text, encoding="utf-8")
    (package_dir / ".gitignore").write_text(
        ".env\n.env.*\n!.env.example\n**/.env\n**/.env.*\n!**/.env.example\n"
        "node_modules/\n.venv/\n__pycache__/\n*.pyc\n*.log\n*.db*\n*.sqlite*\n"
        "backend/data/*\n!backend/data/.gitkeep\nbackups/\ncerts/\n*.key\n*.pem\n*.p12\n*.pfx\n",
        encoding="utf-8",
    )
    return ["package-lock.json", ".gitignore"]


def write_checksums(package_dir: Path) -> None:
    lines: list[str] = []
    for path in sorted(package_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(package_dir).as_posix()
        if rel in {"SHA256SUMS", "RELEASE-MANIFEST.json"}:
            continue
        lines.append(f"{sha256_file(path)}  {rel}")
    (package_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_release_manifest(package_dir: Path, manifest: Manifest, files: list[str]) -> None:
    entries = []
    for rel in files:
        path = package_dir / rel
        entries.append({"path": rel, "sha256": sha256_file(path), "size": path.stat().st_size})
    payload = {
        "schema": 1,
        "package": manifest.package,
        "version": manifest.version,
        "source_root": "main",
        "source_commit": git_head(),
        "source_snapshot": "working-tree (includes modified and new production files)",
        "source_dirty": bool(subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--", "main", "package-lock.json"],
            capture_output=True, check=True,
        ).stdout.strip()),
        "created_at": datetime.now(UTC).isoformat(),
        "migration_head": migration_head(manifest.source_root),
        "files": entries,
    }
    (package_dir / "RELEASE-MANIFEST.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build_package(manifest: Manifest, output_root: Path, do_build: bool, require_dist: bool) -> dict:
    if do_build:
        run_build(manifest)
    selected, dist, issues = validate(manifest, require_dist=require_dist or do_build)
    if issues:
        for issue in issues:
            print("error: " + issue, file=sys.stderr)
        raise SystemExit("release package validation failed")
    if not dist:
        raise SystemExit("frontend dist is required to build a release artifact")

    output_root.mkdir(parents=True, exist_ok=True)
    package_name = f"{manifest.package}-{manifest.version}"
    staging = output_root / f".{package_name}.tmp-{uuid.uuid4().hex}"
    final_dir = output_root / package_name
    zip_path = output_root / f"{package_name}.zip"
    staging.mkdir(parents=True)

    files: list[str] = []
    for rel in selected:
        copy_file(manifest.source_root / rel, staging / rel)
        files.append(rel)

    for rel in dist:
        source = manifest.dist_dir / rel[len("dist/"):]
        copy_file(source, staging / rel)
        files.append(rel)

    (staging / "backend" / "data").mkdir(parents=True, exist_ok=True)
    (staging / "backend" / "data" / ".gitkeep").write_text("", encoding="utf-8")
    files.append("backend/data/.gitkeep")

    # Files never allowed even if accidentally selected later.
    unexpected = [rel for rel in files if forbidden_reason(rel, manifest)]
    if unexpected:
        raise SystemExit("refusing to publish forbidden files: " + ", ".join(unexpected))

    files.extend(materialize_standalone_layout(staging))
    write_checksums(staging)
    write_release_manifest(staging, manifest, files)

    if (final_dir.is_symlink() or final_dir.resolve().parent != output_root.resolve()
            or staging.resolve().parent != output_root.resolve()):
        raise SystemExit("release output must stay inside the resolved output root")
    if final_dir.exists():
        shutil.rmtree(final_dir)
    staging.rename(final_dir)

    tmp_zip = output_root / f".{package_name}.zip.tmp-{uuid.uuid4().hex}"
    with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(final_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(final_dir).as_posix()
                archive.write(path, arcname=f"{package_name}/{rel}")
    tmp_zip.replace(zip_path)

    return {
        "package": manifest.package,
        "version": manifest.version,
        "source_commit": git_head(),
        "directory": str(final_dir),
        "zip": str(zip_path),
        "file_count": len(files) + 2,
        "zip_bytes": zip_path.stat().st_size,
        "dist_files": len(dist),
        "production_entry": "backend/app/production.py",
    }


def check_command(manifest: Manifest, require_dist: bool, as_json: bool) -> int:
    selected, dist, issues = validate(manifest, require_dist=require_dist)
    result = {
        "ok": not issues,
        "package": manifest.package,
        "version": manifest.version,
        "source_root": "main",
        "selected_tracked_files": len(selected),
        "dist_present": bool(dist),
        "dist_files": len(dist),
        "required_files": len(manifest.required),
        "issues": issues,
    }
    if as_json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"package: {manifest.package} {manifest.version}")
        print(f"tracked files selected: {len(selected)}")
        print(f"dist present: {bool(dist)} ({len(dist)} files)")
        for issue in issues:
            print("error: " + issue)
        print("ok" if not issues else "failed")
    return 0 if not issues else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validate the manifest and source tree")
    group.add_argument("--build", action="store_true", help="build the release directory and zip")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-build", action="store_true", help="reuse an existing main/dist")
    parser.add_argument("--require-dist", action="store_true", help="fail check when main/dist is absent")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    manifest = load_manifest()
    if args.check:
        return check_command(manifest, args.require_dist, args.json)

    result = build_package(
        manifest,
        args.output_root.resolve(),
        do_build=not args.no_build,
        require_dist=args.require_dist,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"release package: {result['directory']}")
        print(f"zip: {result['zip']}")
        print(f"files: {result['file_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

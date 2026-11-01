#!/usr/bin/env python3
"""Scan release artifacts, backups, logs and bundles for secret material.

The scanner is deliberately read-only and prints only file locations, never the
matched value.  Markers supplied on the command line are the synthetic secrets
used by acceptance checks; the default patterns catch common credential shapes
that must never ship in a package.
"""
from __future__ import annotations

import argparse
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-key", re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}")),
    ("aws-access-key", re.compile(rb"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")),
    ("github-token", re.compile(rb"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}")),
)
SKIP_DIRS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".scratch"}
)
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class SecretHit:
    path: str
    kind: str
    marker: int | None = None


def _marker_hits(data: bytes, markers: tuple[bytes, ...]) -> list[int]:
    return [index for index, marker in enumerate(markers) if marker and marker in data]


def _scan_bytes(data: bytes, markers: tuple[bytes, ...]) -> list[tuple[str, int | None]]:
    hits: list[tuple[str, int | None]] = []
    for index in _marker_hits(data, markers):
        hits.append(("marker", index))
    for label, pattern in DEFAULT_PATTERNS:
        if pattern.search(data):
            hits.append((label, None))
    return hits


def _sorted_hits(hits: list[SecretHit]) -> list[SecretHit]:
    return sorted(
        set(hits),
        key=lambda hit: (hit.path, hit.kind, hit.marker if hit.marker is not None else -1),
    )


def _scan_name(name: str, markers: tuple[bytes, ...]) -> list[tuple[str, int | None]]:
    lowered = name.lower()
    hits: list[tuple[str, int | None]] = []
    for index, marker in enumerate(markers):
        if marker and marker.decode("utf-8", "ignore").lower() in lowered:
            hits.append(("filename-marker", index))
    for label, _ in DEFAULT_PATTERNS:
        if label == "private-key" and "private key" in lowered:
            hits.append(("filename-pattern", None))
    return hits


def scan_directory(root: Path, markers: tuple[str, ...] = ()) -> list[SecretHit]:
    root = root.resolve()
    hits: list[SecretHit] = []
    if not root.exists():
        return [SecretHit(str(root), "missing-root")]
    if root.is_file():
        return scan_file(root, markers)
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        hits.extend(scan_file(path, markers, display=str(relative)))
    return hits


def scan_file(
    path: Path, markers: tuple[str, ...] = (), *, display: str | None = None
) -> list[SecretHit]:
    encoded = tuple(marker.encode("utf-8") for marker in markers if marker)
    label = display or str(path)
    hits = [SecretHit(label, kind, index) for kind, index in _scan_name(path.name, encoded)]
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                hits.extend(
                    SecretHit(label, kind, index) for kind, index in _scan_bytes(chunk, encoded)
                )
    except OSError:
        return [SecretHit(label, "unreadable")]
    return _sorted_hits(hits)


def scan_zip(path: Path, markers: tuple[str, ...] = ()) -> list[SecretHit]:
    encoded = tuple(marker.encode("utf-8") for marker in markers if marker)
    hits: list[SecretHit] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                hits.extend(
                    SecretHit(name, kind, index)
                    for kind, index in _scan_name(Path(name).name, encoded)
                )
                if name.endswith("/"):
                    continue
                data = archive.read(name)
                hits.extend(
                    SecretHit(name, kind, index) for kind, index in _scan_bytes(data, encoded)
                )
    except (OSError, zipfile.BadZipFile):
        return [SecretHit(str(path), "unreadable")]
    return _sorted_hits(hits)


def scan(path: Path, markers: tuple[str, ...] = ()) -> list[SecretHit]:
    if path.suffix.lower() == ".zip" and path.is_file():
        return scan_zip(path, markers)
    return scan_directory(path, markers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", default=[])
    parser.add_argument("--zip", type=Path, action="append", default=[])
    parser.add_argument("--marker", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.root and not args.zip:
        parser.error("at least one --root or --zip is required")
    markers = tuple(marker for marker in args.marker if marker)
    hits: list[SecretHit] = []
    for root in args.root:
        hits.extend(scan_directory(root, markers))
    for archive in args.zip:
        hits.extend(scan_zip(archive, markers))
    result = {
        "ok": not hits,
        "roots": [str(root) for root in args.root],
        "zips": [str(archive) for archive in args.zip],
        "markers": len(markers),
        "hits": [hit.__dict__ for hit in hits],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"secret scan: {len(hits)} hit(s), {len(markers)} marker(s)")
        for hit in hits:
            marker = f" marker#{hit.marker}" if hit.marker is not None else ""
            print(f"  {hit.path}: {hit.kind}{marker}")
        print("ok" if not hits else "failed")
    return 0 if not hits else 1


if __name__ == "__main__":
    raise SystemExit(main())

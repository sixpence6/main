from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from app.config import MAIN_DIR, REPO_ROOT, Settings
from app.startup import BACKEND_DIR, head_revision

ENTRYPOINT = "app.main:app"


@dataclass(frozen=True)
class RuntimeInfo:
    """Effective production paths, safe to print (no credentials)."""

    entrypoint: str
    import_root: str
    repository_root: str
    backend_dir: str
    workspace_dir: str
    main_dir: str
    data_dir: str
    database_path: str
    web_dist_dir: str
    migration_head: str
    python_executable: str
    working_directory: str


def runtime_info(settings: Settings | None = None) -> RuntimeInfo:
    resolved = settings or Settings()
    production_backend = MAIN_DIR / "backend"
    return RuntimeInfo(
        entrypoint=ENTRYPOINT,
        import_root=str((production_backend / "app").resolve()),
        repository_root=str(REPO_ROOT.resolve()),
        backend_dir=str(production_backend.resolve()),
        workspace_dir=str(BACKEND_DIR.resolve()),
        main_dir=str(MAIN_DIR.resolve()),
        data_dir=str(resolved.resolved_data_dir().resolve()),
        database_path=str(resolved.database_path.resolve()),
        web_dist_dir=str(resolved.resolved_web_dist_dir().resolve()),
        migration_head=head_revision(),
        python_executable=sys.executable,
        working_directory=str(Path.cwd().resolve()),
    )


def _print_runtime(info: RuntimeInfo, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(info), ensure_ascii=False, sort_keys=True))
        return
    for key, value in asdict(info).items():
        print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Single production entry point for the ecommerce image workspace."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print effective paths and the migration head, then exit",
    )
    parser.add_argument("--json", action="store_true", help="use JSON for --check output")
    parser.add_argument("--host", help="override the configured listen address")
    parser.add_argument("--port", type=int, help="override the configured listen port")
    args = parser.parse_args(argv)

    settings = Settings()
    info = runtime_info(settings)
    if args.check:
        _print_runtime(info, args.json)
        return 0

    import uvicorn

    uvicorn.run(
        ENTRYPOINT,
        host=args.host or settings.host,
        port=args.port or settings.port,
        workers=1,
        proxy_headers=False,
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

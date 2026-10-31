from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from app.maintenance.backup import (
    create_backup,
    inspect_sources_report,
    restore_verify,
    verify_backup,
)
from app.maintenance.errors import BackupError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.maintenance.cli",
        description="Local-only maintenance backup commands (no web download).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser(
        "inspect-sources", help="report candidate data roots and digests without modifying them"
    )
    inspect_parser.add_argument(
        "--candidate-root", action="append", dest="candidate_roots", default=None
    )
    inspect_parser.add_argument("--json", action="store_true")

    backup_parser = subparsers.add_parser(
        "backup", help="create one verified backup from an explicitly selected source"
    )
    backup_parser.add_argument("--source", required=True)
    backup_parser.add_argument("--output", required=True)
    backup_parser.add_argument(
        "--service-stopped",
        action="store_true",
        help="required acknowledgement that the application process is stopped",
    )
    backup_parser.add_argument("--timeout", type=float, default=30.0)
    backup_parser.add_argument("--confirm-source-digest", default=None)
    backup_parser.add_argument(
        "--candidate-root", action="append", dest="candidate_roots", default=None
    )
    backup_parser.add_argument("--json", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify", help="verify a backup manifest, hashes and SQLite integrity"
    )
    verify_parser.add_argument("--backup", required=True)
    verify_parser.add_argument("--json", action="store_true")

    restore_parser = subparsers.add_parser(
        "restore-verify", help="rehearse a restore into an isolated empty directory"
    )
    restore_parser.add_argument("--backup", required=True)
    restore_parser.add_argument("--output", required=True)
    restore_parser.add_argument("--confirm-restore", required=True)
    restore_parser.add_argument("--json", action="store_true")
    return parser


def _print_payload(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect-sources":
            payload = inspect_sources_report(args.candidate_roots)
        elif args.command == "backup":
            if not args.service_stopped:
                raise BackupError(
                    "SERVICE_MUST_BE_STOPPED",
                    "CLI backup is offline-only; stop the service and pass --service-stopped",
                )
            result = asyncio.run(
                create_backup(
                    source=args.source,
                    output=args.output,
                    timeout=args.timeout,
                    confirm_source_digest=args.confirm_source_digest,
                    candidate_roots=args.candidate_roots,
                )
            )
            payload = result.as_dict()
        elif args.command == "verify":
            payload = verify_backup(args.backup).as_dict()
        elif args.command == "restore-verify":
            payload = restore_verify(
                backup=args.backup,
                output=args.output,
                confirmation=args.confirm_restore,
            ).as_dict()
        else:
            raise BackupError("COMMAND_REQUIRED", "a subcommand is required")
    except BackupError as exc:
        _print_payload({"ok": False, "error": exc.as_dict()})
        return 1
    _print_payload(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

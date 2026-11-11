from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence

from app.maintenance.automation import read_status
from app.maintenance.backup import (
    create_backup,
    inspect_sources_report,
    restore_verify,
    verify_backup,
)
from app.maintenance.claim import CLAIM_CONFIRMATION, claim_legacy, inspect_legacy_projects
from app.maintenance.errors import BackupError
from app.maintenance.restore import (
    CLEAR_CONFIRMATION,
    RESTORE_CONFIRMATION,
    clear_restore_gap,
    restore_switch,
)


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

    status_parser = subparsers.add_parser(
        "backup-status", help="read the local scheduled-backup status file"
    )
    status_parser.add_argument("--output", required=True)
    status_parser.add_argument("--json", action="store_true")

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

    restore_switch_parser = subparsers.add_parser(
        "restore", help="switch one verified snapshot into the explicit data root"
    )
    restore_switch_parser.add_argument("--source", required=True)
    restore_switch_parser.add_argument("--backup", required=True)
    restore_switch_parser.add_argument("--output", required=True)
    restore_switch_parser.add_argument(
        "--confirm-restore",
        required=True,
        help=f"explicit confirmation; must be {RESTORE_CONFIRMATION}",
    )
    restore_switch_parser.add_argument(
        "--service-stopped",
        action="store_true",
        help="required acknowledgement that the application process is stopped",
    )
    restore_switch_parser.add_argument(
        "--allow-real-data-root",
        action="store_true",
        help="allow the protected default data root after an explicit maintainer source choice",
    )
    restore_switch_parser.add_argument("--gap-seconds", type=int, default=86400)
    restore_switch_parser.add_argument("--json", action="store_true")

    clear_gap_parser = subparsers.add_parser(
        "clear-restore-gap", help="audit and clear an open post-restore ledger gap"
    )
    clear_gap_parser.add_argument("--source", required=True)
    clear_gap_parser.add_argument(
        "--confirm-clear",
        required=True,
        help=f"explicit confirmation; must be {CLEAR_CONFIRMATION}",
    )
    clear_gap_parser.add_argument("--reason", required=True)
    clear_gap_parser.add_argument(
        "--service-stopped",
        action="store_true",
        help="required acknowledgement that the application process is stopped",
    )
    clear_gap_parser.add_argument(
        "--allow-real-data-root",
        action="store_true",
        help="allow the protected default data root after an explicit maintainer source choice",
    )
    clear_gap_parser.add_argument("--json", action="store_true")

    inspect_legacy_parser = subparsers.add_parser(
        "inspect-legacy", help="list unowned projects that contain legacy evidence"
    )
    inspect_legacy_parser.add_argument("--source", required=True)
    inspect_legacy_parser.add_argument("--confirm-source-digest", default=None)
    inspect_legacy_parser.add_argument(
        "--candidate-root", action="append", dest="candidate_roots", default=None
    )
    inspect_legacy_parser.add_argument("--json", action="store_true")

    claim_parser = subparsers.add_parser(
        "claim-legacy", help="claim explicitly selected legacy projects for the initial admin"
    )
    claim_parser.add_argument("--source", required=True)
    claim_parser.add_argument("--backup", required=True)
    claim_parser.add_argument("--admin-email", required=True)
    claim_parser.add_argument("--project", action="append", dest="projects", required=True)
    claim_parser.add_argument("--confirm-source-digest", default=None)
    claim_parser.add_argument(
        "--confirm-claim",
        required=True,
        help=f"explicit confirmation; must be {CLAIM_CONFIRMATION}",
    )
    claim_parser.add_argument(
        "--service-stopped",
        action="store_true",
        help="required acknowledgement that the application process is stopped",
    )
    claim_parser.add_argument(
        "--candidate-root", action="append", dest="candidate_roots", default=None
    )
    claim_parser.add_argument("--json", action="store_true")
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
        elif args.command == "backup-status":
            status = read_status(args.output)
            payload = {"ok": status is not None, "status": status}
        elif args.command == "verify":
            payload = verify_backup(args.backup).as_dict()
        elif args.command == "restore-verify":
            payload = restore_verify(
                backup=args.backup,
                output=args.output,
                confirmation=args.confirm_restore,
            ).as_dict()
        elif args.command == "restore":
            if not args.service_stopped:
                raise BackupError(
                    "SERVICE_MUST_BE_STOPPED",
                    "restore is offline-only; stop the service and pass --service-stopped",
                )
            payload = restore_switch(
                source=args.source,
                backup=args.backup,
                output=args.output,
                confirmation=args.confirm_restore,
                service_stopped=True,
                allow_real_data_root=args.allow_real_data_root,
                gap_seconds=args.gap_seconds,
            ).as_dict()
        elif args.command == "clear-restore-gap":
            if not args.service_stopped:
                raise BackupError(
                    "SERVICE_MUST_BE_STOPPED",
                    "gap clearing is offline-only; stop the service and pass --service-stopped",
                )
            payload = clear_restore_gap(
                source=args.source,
                confirmation=args.confirm_clear,
                reason=args.reason,
                service_stopped=True,
                allow_real_data_root=args.allow_real_data_root,
            )
        elif args.command == "inspect-legacy":
            payload = asyncio.run(
                inspect_legacy_projects(
                    source=args.source,
                    candidate_roots=args.candidate_roots,
                    confirm_source_digest=args.confirm_source_digest,
                )
            )
        elif args.command == "claim-legacy":
            if not args.service_stopped:
                raise BackupError(
                    "SERVICE_MUST_BE_STOPPED",
                    "CLI claim is offline-only; stop the service and pass --service-stopped",
                )
            result = asyncio.run(
                claim_legacy(
                    source=args.source,
                    backup=args.backup,
                    admin_email=args.admin_email,
                    project_ids=args.projects,
                    confirmation=args.confirm_claim,
                    service_stopped=True,
                    candidate_roots=args.candidate_roots,
                    confirm_source_digest=args.confirm_source_digest,
                )
            )
            payload = result.as_dict()
        else:
            raise BackupError("COMMAND_REQUIRED", "a subcommand is required")
    except BackupError as exc:
        _print_payload({"ok": False, "error": exc.as_dict()})
        return 1
    _print_payload(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

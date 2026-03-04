from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from .bases import write_bases
from .config import config_summary, default_config, load_config, save_config, validate_config
from .daemon import daemon_status, start_daemon, stop_daemon
from .doctor import run_doctor
from .importer import run_import
from .layout import ensure_layout
from .queue import sync_queue
from .watch import run_watch


def _parse_vault(path: str) -> Path:
    return Path(path).expanduser().resolve()


def cmd_init(args: argparse.Namespace) -> int:
    vault_root = _parse_vault(args.vault)
    config = default_config(vault_root)

    ensure_layout(config)
    save_config(config)
    base_files = write_bases(config)

    print(f"initialized: {config.ledger_root}")
    print(config_summary(config))
    for base_file in base_files:
        rel = base_file.resolve().relative_to(vault_root.resolve()).as_posix()
        print(f"base: {rel}")

    auto_start = args.auto_start and not args.no_auto_start
    if auto_start and platform.system().lower() == "darwin":
        try:
            msg = start_daemon(config)
            print(msg)
        except Exception as exc:
            print(f"warn: failed to auto-start daemon: {exc}")
            return 1
    elif auto_start:
        print("warn: auto-start is macOS-only; run `ledger-obsidian watch --vault ...` manually")

    return 0


def cmd_import(args: argparse.Namespace) -> int:
    config = load_config(_parse_vault(args.vault))
    validate_config(config)

    result = run_import(
        config,
        dry_run=bool(args.dry_run),
        max_files=args.max_files,
        max_notes=args.max_notes,
    )

    print(
        json.dumps(
            {
                "selected_files": result.selected_files,
                "notes_created": result.notes_created,
                "queue_created": result.queue_created,
                "skipped_low_confidence": result.skipped_low_confidence,
                "skipped_deduped": result.skipped_deduped,
                "dry_run": result.dry_run,
            },
            indent=2,
        )
    )
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    config = load_config(_parse_vault(args.vault))
    validate_config(config)
    return run_watch(config, debounce_seconds=args.debounce_seconds)


def cmd_daemon(args: argparse.Namespace) -> int:
    if not getattr(args, "vault", None):
        print("error: --vault is required")
        return 2
    config = load_config(_parse_vault(args.vault))
    validate_config(config)

    if args.daemon_command == "start":
        print(start_daemon(config))
        return 0
    if args.daemon_command == "stop":
        print(stop_daemon(config))
        return 0
    if args.daemon_command == "status":
        running, detail = daemon_status(config)
        print(detail)
        return 0 if running else 1

    print("unknown daemon command")
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config(_parse_vault(args.vault))
    code, lines = run_doctor(config)
    for line in lines:
        print(line)
    return code


def cmd_queue_sync(args: argparse.Namespace) -> int:
    if not getattr(args, "vault", None):
        print("error: --vault is required")
        return 2
    config = load_config(_parse_vault(args.vault))
    validate_config(config)
    result = sync_queue(config)
    print(json.dumps(result, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger-obsidian", description="Drop-in Cognitive Ledger tooling for Obsidian vaults")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize cognitive-ledger structure in a vault")
    init_parser.add_argument("--vault", required=True, help="Path to the Obsidian vault")
    init_parser.add_argument("--auto-start", action="store_true", default=True, help="Start background daemon on macOS")
    init_parser.add_argument("--no-auto-start", action="store_true", help="Do not start daemon after init")

    import_parser = subparsers.add_parser("import", help="Run one import cycle")
    import_parser.add_argument("--vault", required=True, help="Path to the Obsidian vault")
    import_parser.add_argument("--dry-run", action="store_true", help="Do not write notes or state")
    import_parser.add_argument("--max-files", type=int, default=None)
    import_parser.add_argument("--max-notes", type=int, default=None)

    watch_parser = subparsers.add_parser("watch", help="Run continuous watch/import loop")
    watch_parser.add_argument("--vault", required=True, help="Path to the Obsidian vault")
    watch_parser.add_argument("--debounce-seconds", type=float, default=None)

    daemon_parser = subparsers.add_parser("daemon", help="Manage launchd watcher daemon")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command")
    daemon_start = daemon_subparsers.add_parser("start", help="Start daemon")
    daemon_start.add_argument("--vault", required=True, help="Path to the Obsidian vault")
    daemon_stop = daemon_subparsers.add_parser("stop", help="Stop daemon")
    daemon_stop.add_argument("--vault", required=True, help="Path to the Obsidian vault")
    daemon_status = daemon_subparsers.add_parser("status", help="Show daemon status")
    daemon_status.add_argument("--vault", required=True, help="Path to the Obsidian vault")

    doctor_parser = subparsers.add_parser("doctor", help="Run environment checks")
    doctor_parser.add_argument("--vault", required=True, help="Path to the Obsidian vault")

    queue_parser = subparsers.add_parser("queue", help="Queue operations")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command")
    queue_sync = queue_subparsers.add_parser("sync", help="Promote approved candidate notes")
    queue_sync.add_argument("--vault", required=True, help="Path to the Obsidian vault")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return cmd_init(args)
    if args.command == "import":
        return cmd_import(args)
    if args.command == "watch":
        return cmd_watch(args)
    if args.command == "daemon":
        return cmd_daemon(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "queue" and args.queue_command == "sync":
        return cmd_queue_sync(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_root(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _add_root_argument(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--vault", dest="root", help="Path to the Obsidian vault")
    group.add_argument(
        "--root",
        dest="root",
        help="Path to the note-base root (generic markdown tree or Obsidian vault)",
    )


def cmd_init(args: argparse.Namespace) -> int:
    from ledger.importers.backends.obsidian import ObsidianBackend
    vault_root = _parse_root(args.root)
    auto_start = args.auto_start and not args.no_auto_start
    return ObsidianBackend(vault_root).init(auto_start=auto_start)


def cmd_import(args: argparse.Namespace) -> int:
    from ledger.importers.backends.obsidian import ObsidianBackend
    from ledger.importers.types import ImportOptions
    root = _parse_root(args.root)
    options = ImportOptions(root=root, dry_run=bool(args.dry_run))
    return ObsidianBackend(root).run_import(
        options, max_files=args.max_files, max_notes=args.max_notes
    )


def cmd_bootstrap(args: argparse.Namespace) -> int:
    from ledger.importers.backends.obsidian import ObsidianBackend
    from ledger.importers.types import ImportOptions
    root = _parse_root(args.root)
    options = ImportOptions(root=root, dry_run=bool(args.dry_run))
    return ObsidianBackend(root).bootstrap(
        options, max_files=args.max_files, max_notes=args.max_notes
    )


def cmd_watch(args: argparse.Namespace) -> int:
    from ledger.importers.backends.obsidian import ObsidianBackend
    root = _parse_root(args.root)
    return ObsidianBackend(root).watch(debounce_seconds=args.debounce_seconds)


def cmd_daemon(args: argparse.Namespace) -> int:
    if not getattr(args, "root", None):
        print("error: --vault or --root is required")
        return 2
    from ledger.importers.backends.obsidian import ObsidianBackend
    root = _parse_root(args.root)
    backend = ObsidianBackend(root)
    if args.daemon_command == "start":
        return backend.daemon_start()
    if args.daemon_command == "stop":
        return backend.daemon_stop()
    if args.daemon_command == "status":
        return backend.daemon_status()
    print("unknown daemon command")
    return 2


def cmd_doctor(args: argparse.Namespace) -> int:
    from ledger.importers.backends.obsidian import ObsidianBackend
    return ObsidianBackend(_parse_root(args.root)).run_doctor()


def cmd_queue_sync(args: argparse.Namespace) -> int:
    if not getattr(args, "root", None):
        print("error: --vault or --root is required")
        return 2
    from ledger.importers.backends.obsidian import ObsidianBackend
    return ObsidianBackend(_parse_root(args.root)).queue_sync()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ledger-obsidian",
        description="Drop-in Cognitive Ledger tooling for Obsidian vaults and generic markdown note bases",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize cognitive-ledger structure in a vault")
    _add_root_argument(init_parser)
    init_parser.add_argument("--auto-start", action="store_true", default=True, help="Start background daemon on macOS")
    init_parser.add_argument("--no-auto-start", action="store_true", help="Do not start daemon after init")

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Initialize cognitive-ledger structure and run one import cycle",
    )
    _add_root_argument(bootstrap_parser)
    bootstrap_parser.add_argument("--dry-run", action="store_true", help="Do not write notes or state")
    bootstrap_parser.add_argument("--max-files", type=int, default=None)
    bootstrap_parser.add_argument("--max-notes", type=int, default=None)

    import_parser = subparsers.add_parser("import", help="Run one import cycle")
    _add_root_argument(import_parser)
    import_parser.add_argument("--dry-run", action="store_true", help="Do not write notes or state")
    import_parser.add_argument("--max-files", type=int, default=None)
    import_parser.add_argument("--max-notes", type=int, default=None)

    watch_parser = subparsers.add_parser("watch", help="Run continuous watch/import loop")
    _add_root_argument(watch_parser)
    watch_parser.add_argument("--debounce-seconds", type=float, default=None)

    daemon_parser = subparsers.add_parser("daemon", help="Manage launchd watcher daemon")
    daemon_subparsers = daemon_parser.add_subparsers(dest="daemon_command")
    daemon_start = daemon_subparsers.add_parser("start", help="Start daemon")
    _add_root_argument(daemon_start)
    daemon_stop = daemon_subparsers.add_parser("stop", help="Stop daemon")
    _add_root_argument(daemon_stop)
    daemon_status = daemon_subparsers.add_parser("status", help="Show daemon status")
    _add_root_argument(daemon_status)

    doctor_parser = subparsers.add_parser("doctor", help="Run environment checks")
    _add_root_argument(doctor_parser)

    queue_parser = subparsers.add_parser("queue", help="Queue operations")
    queue_subparsers = queue_parser.add_subparsers(dest="queue_command")
    queue_sync = queue_subparsers.add_parser("sync", help="Promote approved candidate notes")
    _add_root_argument(queue_sync)

    related_parser = subparsers.add_parser(
        "related",
        help="Find ledger notes related to an Obsidian note or free text",
    )
    related_group = related_parser.add_mutually_exclusive_group(required=True)
    related_group.add_argument("--path", dest="note_path", help="Path to an Obsidian/markdown note")
    related_group.add_argument("--query", dest="query_text", help="Free-text query")
    related_parser.add_argument("--limit", type=int, default=5, help="Max results (default: 5)")
    related_parser.add_argument("--json", action="store_true", dest="json_output")

    return parser


def cmd_related(args: argparse.Namespace) -> int:
    from ledger.importers.backends.obsidian import ObsidianBackend

    if args.note_path:
        note_path = Path(args.note_path).expanduser().resolve()
        if not note_path.is_file():
            print(f"error: file not found: {note_path}", file=sys.stderr)
            return 2
        text = note_path.read_text(encoding="utf-8")
    else:
        text = args.query_text

    return ObsidianBackend(Path(".")).related(
        text=text, top_k=args.limit, json_output=args.json_output
    )


def _emit_obsidian_doctor(as_json: bool) -> int:
    """Top-level --doctor flag per hugr CONVENTIONS.md.

    Reuses ledger.doctor.run_doctor() for the standard schema (config,
    redaction sentinel, etc.) and overrides tool to 'ledger-obsidian'.
    The existing `ledger-obsidian doctor` subcommand stays as-is for
    back-compat and still runs the obsidian-vault-specific checks.
    """
    import json as _json
    import sys as _sys

    from ledger.doctor import run_doctor as _ledger_run_doctor

    payload = _ledger_run_doctor()
    data = payload.to_dict()
    data["tool"] = "ledger-obsidian"
    if as_json:
        _sys.stdout.write(_json.dumps(data, ensure_ascii=False) + "\n")
        _sys.stdout.flush()
    else:
        print(f"ledger-obsidian doctor (v{data['version']})")
        if payload.config_path:
            print(f"  config: {payload.config_path}")
        if payload.data_path:
            print(f"  root:   {payload.data_path}")
        if not payload.findings:
            print("  status: ok")
        else:
            print(f"  findings: {len(payload.findings)}")
            for f in payload.findings:
                marker = {"error": "x", "warning": "!", "info": "."}.get(f.severity, ".")
                print(f"    {marker} [{f.severity}] {f.id}: {f.message}")
                if f.hint:
                    print(f"        hint: {f.hint}")
    return payload.exit_code()


def main(argv: list[str] | None = None) -> int:
    import sys as _sys

    raw = list(_sys.argv[1:] if argv is None else argv)
    # Top-level --doctor per hugr CONVENTIONS.md (alongside the
    # existing `doctor` subcommand, which stays for back-compat).
    if "--doctor" in raw:
        as_json = "--json" in raw
        return _emit_obsidian_doctor(as_json)

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return cmd_init(args)
    if args.command == "bootstrap":
        return cmd_bootstrap(args)
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
    if args.command == "related":
        return cmd_related(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI dispatch for ``ledger import {folder|obsidian} {subcommand}``.

Argparse lives here; backends are free of it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_root(path: str) -> Path:
    return Path(path).expanduser().resolve()


def _add_root_required(parser: argparse.ArgumentParser) -> None:
    """Mutually exclusive --vault / --root, required."""
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--vault", dest="root", type=_parse_root, help="Path to the Obsidian vault")
    group.add_argument("--root", dest="root", type=_parse_root, help="Path to the note-base root")


def _add_root_optional(parser: argparse.ArgumentParser) -> None:
    """Mutually exclusive --vault / --root, optional (for parent-level default action)."""
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--vault", dest="root", type=_parse_root, help="Path to the Obsidian vault")
    group.add_argument("--root", dest="root", type=_parse_root, help="Path to the note-base root")


# ---------------------------------------------------------------------------
# Folder backend
# ---------------------------------------------------------------------------

def _register_folder(backend_subs: argparse._SubParsersAction) -> argparse.ArgumentParser:
    folder_parser = backend_subs.add_parser(
        "folder",
        help="Generic markdown folder import backend",
    )
    # optional root for the default (no-subcommand) case
    folder_parser.add_argument("--root", dest="root", type=_parse_root, help="Root folder path")
    folder_parser.add_argument("--dry-run", action="store_true", help="Do not write notes or state")

    folder_subs = folder_parser.add_subparsers(dest="import_subcommand")

    import_p = folder_subs.add_parser("import", help="Run one import pass")
    import_p.add_argument("--root", dest="root", type=_parse_root, required=True, help="Root folder path")
    import_p.add_argument("--dry-run", action="store_true")

    doctor_p = folder_subs.add_parser("doctor", help="Check folder health")
    doctor_p.add_argument("--root", dest="root", type=_parse_root, required=True, help="Root folder path")

    return folder_parser


def _dispatch_folder(args: argparse.Namespace) -> int:
    from ledger.importers.backends.folder import FolderBackend
    from ledger.importers.types import ImportOptions

    subcommand = getattr(args, "import_subcommand", None)
    root: Path | None = getattr(args, "root", None)

    if subcommand == "doctor":
        result = FolderBackend(root).doctor()
        for e in result.errors:
            print(f"error: {e}")
        for w in result.warnings:
            print(f"warning: {w}")
        if result.ok:
            print(f"folder: ok ({root})")
        return 0 if result.ok else 2

    # default: import
    if root is None:
        print("error: --root is required", file=sys.stderr)
        return 2
    dry_run = getattr(args, "dry_run", False)
    result = FolderBackend(root).import_once(ImportOptions(root=root, dry_run=dry_run))
    print(f"backend: {result.backend}")
    print(f"scanned: {result.scanned}")
    print(f"imported: {result.imported}")
    print(f"skipped: {result.skipped}")
    for e in result.errors:
        print(f"error: {e}")
    return 0 if result.ok else 2


# ---------------------------------------------------------------------------
# Obsidian backend
# ---------------------------------------------------------------------------

def _register_obsidian(backend_subs: argparse._SubParsersAction) -> argparse.ArgumentParser:
    obsidian_parser = backend_subs.add_parser(
        "obsidian",
        help="Obsidian vault import backend",
    )
    # optional root at the obsidian level for the default (no-subcommand) case
    _add_root_optional(obsidian_parser)
    obsidian_parser.add_argument("--dry-run", action="store_true")
    obsidian_parser.add_argument("--max-files", type=int, default=None)
    obsidian_parser.add_argument("--max-notes", type=int, default=None)

    obsidian_subs = obsidian_parser.add_subparsers(dest="import_subcommand")

    init_p = obsidian_subs.add_parser("init", help="Initialize cognitive-ledger structure in a vault")
    _add_root_required(init_p)
    init_p.add_argument("--auto-start", action="store_true", default=True)
    init_p.add_argument("--no-auto-start", action="store_true")

    import_p = obsidian_subs.add_parser("import", help="Run one import cycle")
    _add_root_required(import_p)
    import_p.add_argument("--dry-run", action="store_true")
    import_p.add_argument("--max-files", type=int, default=None)
    import_p.add_argument("--max-notes", type=int, default=None)

    bootstrap_p = obsidian_subs.add_parser("bootstrap", help="Initialize and run one import cycle")
    _add_root_required(bootstrap_p)
    bootstrap_p.add_argument("--dry-run", action="store_true")
    bootstrap_p.add_argument("--max-files", type=int, default=None)
    bootstrap_p.add_argument("--max-notes", type=int, default=None)

    watch_p = obsidian_subs.add_parser("watch", help="Run continuous watch/import loop")
    _add_root_required(watch_p)
    watch_p.add_argument("--debounce-seconds", type=float, default=None)

    daemon_p = obsidian_subs.add_parser("daemon", help="Manage launchd watcher daemon")
    daemon_subs = daemon_p.add_subparsers(dest="daemon_subcommand")
    for verb in ("start", "stop", "status"):
        sub = daemon_subs.add_parser(verb, help=f"{verb.capitalize()} daemon")
        _add_root_required(sub)

    doctor_p = obsidian_subs.add_parser("doctor", help="Run environment checks")
    _add_root_required(doctor_p)

    queue_p = obsidian_subs.add_parser("queue", help="Queue operations")
    queue_subs = queue_p.add_subparsers(dest="queue_subcommand")
    queue_sync = queue_subs.add_parser("sync", help="Promote approved candidate notes")
    _add_root_required(queue_sync)

    related_p = obsidian_subs.add_parser(
        "related",
        help="Find ledger notes related to an Obsidian note or free text",
    )
    related_src = related_p.add_mutually_exclusive_group(required=True)
    related_src.add_argument("--path", dest="note_path", help="Path to an Obsidian/markdown note")
    related_src.add_argument("--query", dest="query_text", help="Free-text query")
    related_p.add_argument("--limit", type=int, default=5)
    related_p.add_argument("--json", action="store_true", dest="json_output")

    return obsidian_parser


def _dispatch_obsidian(args: argparse.Namespace) -> int:
    from ledger.importers.backends.obsidian import ObsidianBackend
    from ledger.importers.types import ImportOptions

    subcommand = getattr(args, "import_subcommand", None)
    root: Path | None = getattr(args, "root", None)

    if subcommand == "init":
        auto_start = getattr(args, "auto_start", True) and not getattr(args, "no_auto_start", False)
        return ObsidianBackend(root).init(auto_start=auto_start)

    if subcommand in ("import", None):
        if root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        options = ImportOptions(root=root, dry_run=getattr(args, "dry_run", False))
        return ObsidianBackend(root).run_import(
            options,
            max_files=getattr(args, "max_files", None),
            max_notes=getattr(args, "max_notes", None),
        )

    if subcommand == "bootstrap":
        options = ImportOptions(root=root, dry_run=getattr(args, "dry_run", False))
        return ObsidianBackend(root).bootstrap(
            options,
            max_files=getattr(args, "max_files", None),
            max_notes=getattr(args, "max_notes", None),
        )

    if subcommand == "watch":
        return ObsidianBackend(root).watch(debounce_seconds=getattr(args, "debounce_seconds", None))

    if subcommand == "daemon":
        if root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        daemon_sub = getattr(args, "daemon_subcommand", None)
        backend = ObsidianBackend(root)
        if daemon_sub == "start":
            return backend.daemon_start()
        if daemon_sub == "stop":
            return backend.daemon_stop()
        if daemon_sub == "status":
            return backend.daemon_status()
        print("error: daemon requires start|stop|status", file=sys.stderr)
        return 2

    if subcommand == "doctor":
        result = ObsidianBackend(root).run_doctor()
        for line in result.checks.get("lines", []):
            print(line)
        for e in result.errors:
            print(f"error: {e}")
        return 0 if result.ok else 2

    if subcommand == "queue":
        if root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        queue_sub = getattr(args, "queue_subcommand", None)
        if queue_sub == "sync":
            result = ObsidianBackend(root).queue_sync()
            if result is None:
                print("error: --vault or --root is required", file=sys.stderr)
                return 2
            import json as _json
            print(_json.dumps({"promoted": result.promoted, "rejected": result.rejected, "pending": result.pending}, indent=2))
            return 0
        print("error: queue requires sync", file=sys.stderr)
        return 2

    if subcommand == "related":
        note_path = getattr(args, "note_path", None)
        query_text = getattr(args, "query_text", None)
        if note_path:
            p = Path(note_path).expanduser().resolve()
            if not p.is_file():
                print(f"error: file not found: {p}", file=sys.stderr)
                return 2
            text = p.read_text(encoding="utf-8")
        else:
            text = query_text
        backend = ObsidianBackend(root or Path("."))
        return backend.related(
            text=text,
            top_k=getattr(args, "limit", 5),
            json_output=getattr(args, "json_output", False),
        )

    print("error: unknown subcommand", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# Public API — wired into ledger/cli.py
# ---------------------------------------------------------------------------

def build_import_subparser(parent_subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register ``ledger import`` with the main CLI parser."""
    import_parser = parent_subparsers.add_parser(
        "import",
        help="Import from external sources (folder, obsidian, ...)",
    )
    backend_subs = import_parser.add_subparsers(dest="import_backend")
    _register_folder(backend_subs)
    _register_obsidian(backend_subs)
    return import_parser


def handle_import_command(args: argparse.Namespace, import_parser: argparse.ArgumentParser) -> int:
    backend = getattr(args, "import_backend", None)

    if backend == "folder":
        return _dispatch_folder(args)

    if backend == "obsidian":
        return _dispatch_obsidian(args)

    import_parser.print_help()
    return 0

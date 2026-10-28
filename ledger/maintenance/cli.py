"""Electric Sheep command-line dispatch."""
from __future__ import annotations

import argparse
from .sync import cmd_sync
from .status import cmd_status
from .lint import cmd_lint
from .index import cmd_index
from .curation import _cmd_duplicates, cmd_links, cmd_provenance, cmd_sleep


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Electric Sheep maintenance helper")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Emit JSON / action envelope on stdout (machine mode).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show sleep and sync status")
    subparsers.add_parser("lint", help="Validate note and timeline conventions")
    subparsers.add_parser("index", help="Regenerate derived indices")
    subparsers.add_parser("sleep", help="Show consolidation checklist")

    duplicates_parser = subparsers.add_parser(
        "duplicates",
        help="Scan for duplicate or near-duplicate notes",
    )
    duplicates_parser.add_argument(
        "--threshold",
        type=float,
        default=0.65,
        dest="threshold",
        help="Jaccard similarity threshold for content duplicates (default: 0.65)",
    )
    duplicates_parser.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Emit JSON output",
    )

    sync_parser = subparsers.add_parser("sync", help="Compare notes against sync baseline")
    mode_group = sync_parser.add_mutually_exclusive_group()
    mode_group.add_argument("--check", action="store_true", help="Check drift (default)")
    mode_group.add_argument("--apply", action="store_true", help="Write current notes snapshot as baseline")
    sync_parser.add_argument(
        "--accept-drift",
        action="store_true",
        help="Explicitly accept unresolved drift when replacing an existing baseline",
    )

    links_parser = subparsers.add_parser(
        "links",
        help="Propose [[links]] between related notes (dry run unless --apply)",
    )
    links_parser.add_argument("--apply", action="store_true", help="Write proposals under ## Links")
    links_parser.add_argument("--all", action="store_true", dest="all_notes",
                              help="Consider every note, not only those changed since the last sleep")

    contradictions_parser = subparsers.add_parser(
        "contradictions",
        help="NLI-based contradiction scan (check | apply)",
    )
    contra_mode = contradictions_parser.add_mutually_exclusive_group()
    contra_mode.add_argument(
        "--check",
        action="store_true",
        help="Dry run: report what would be done without writing (default)",
    )
    contra_mode.add_argument(
        "--apply",
        action="store_true",
        help="Write conflict inbox notes (and supersessions, if contradiction_auto_supersede is on)",
    )

    provenance_parser = subparsers.add_parser(
        "provenance",
        help="Stamp provenance:corrected on notes with correction signals (check | apply)",
    )
    prov_mode = provenance_parser.add_mutually_exclusive_group()
    prov_mode.add_argument("--check", action="store_true",
                           help="Dry run: report what would be stamped (default)")
    prov_mode.add_argument("--apply", action="store_true",
                           help="Write provenance:corrected to the affected notes")

    return parser


def main(argv: list[str] | None = None) -> int:
    import sys as _sys

    raw = list(_sys.argv[1:] if argv is None else argv)
    # Top-level --doctor per the CLI contract. Handle before
    # argparse because the maintenance parser requires a subcommand.
    if "--doctor" in raw:
        from ledger.doctor import emit_doctor
        # sheep uses ledger's doctor; identity-of-tool override
        # happens via the JSON shape (tool field).
        as_json = "--json" in raw
        return _emit_sheep_doctor(as_json)

    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))

    if args.command == "status":
        return cmd_status(as_json=as_json)
    if args.command == "lint":
        return cmd_lint(as_json=as_json)
    if args.command == "index":
        return cmd_index(as_json=as_json)
    if args.command == "sleep":
        return cmd_sleep(as_json=as_json)
    if args.command == "sync":
        if args.accept_drift and not args.apply:
            parser.error("--accept-drift requires --apply")
        return _wrap_sync(
            apply=bool(args.apply),
            accept_drift=bool(args.accept_drift),
            as_json=as_json,
        )
    if args.command == "links":
        return cmd_links(apply=bool(args.apply), all_notes=bool(args.all_notes), as_json=as_json)
    if args.command == "contradictions":
        from ledger.contradiction import cmd_sleep_contradictions
        return cmd_sleep_contradictions(apply=bool(getattr(args, "apply", False)))
    if args.command == "provenance":
        return cmd_provenance(apply=bool(getattr(args, "apply", False)), as_json=as_json)
    if args.command == "duplicates":
        return _cmd_duplicates(
            threshold=float(getattr(args, "threshold", 0.65)),
            as_json=as_json,
        )

    parser.print_help()
    return 1


def _wrap_sync(apply: bool, accept_drift: bool, as_json: bool) -> int:
    """cmd_sync is action when --apply, data when --check."""
    if not as_json:
        return cmd_sync(apply=apply, accept_drift=accept_drift)
    import io as _io
    import time as _t
    from contextlib import redirect_stdout
    t0 = _t.monotonic()
    buf = _io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = cmd_sync(apply=apply, accept_drift=accept_drift)
    except Exception as exc:
        from ledger.conventions import (
            EXIT_USER_ERROR, action_envelope, emit_action,
        )
        emit_action(action_envelope(
            command="sheep sync", ok=False,
            tool="sheep",
            error={"code": "sync_failed", "message": str(exc)},
            duration_ms=(_t.monotonic() - t0) * 1000.0,
        ))
        return EXIT_USER_ERROR
    if apply:
        from ledger.conventions import action_envelope, emit_action
        emit_action(action_envelope(
            command="sheep sync", ok=(rc == 0),
            tool="sheep",
            stats={"applied": True, "human_lines": [
                line.rstrip() for line in buf.getvalue().splitlines() if line.strip()
            ]},
            duration_ms=(_t.monotonic() - t0) * 1000.0,
        ))
        return int(rc or 0)
    # Data class -> raw doc, no top-level `ok`.
    import json as _json
    import sys as _sys
    payload = {
        "tool": "sheep",
        "command": "sheep sync",
        "applied": False,
        "exit_code": int(rc or 0),
        "human_lines": [line.rstrip() for line in buf.getvalue().splitlines() if line.strip()],
    }
    _sys.stdout.write(_json.dumps(payload, ensure_ascii=False) + "\n")
    _sys.stdout.flush()
    return int(rc or 0)


def _emit_sheep_doctor(as_json: bool) -> int:
    """sheep's doctor reuses ledger's checks and re-tags the tool name."""
    import json as _json
    import sys as _sys

    from ledger.doctor import run_doctor

    payload = run_doctor()
    data = payload.to_dict()
    data["tool"] = "sheep"
    if as_json:
        _sys.stdout.write(_json.dumps(data, ensure_ascii=False) + "\n")
        _sys.stdout.flush()
    else:
        print(f"sheep doctor (v{data['version']})")
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

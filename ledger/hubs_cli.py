"""`ledger people`, `ledger projects` and `ledger unreviewed` (see ledger.hubs)."""

from __future__ import annotations

import argparse
import json
from typing import Any

from ledger import hubs

_ADD_FIELDS = {
    "people": (("--entity", "entity", None), ("--sphere", "spheres", "append"), ("--org", "orgs", "append"),
               ("--alias", "aliases", "append")),
    "projects": (("--status", "status", None), ("--profile", "profile", None), ("--repo", "repos", "append"),
                 ("--ado", "ado", "append"), ("--person", "people", "append")),
}


def register(subparsers: argparse._SubParsersAction) -> None:
    for kind, singular in (("people", "person"), ("projects", "project")):
        parser = subparsers.add_parser(kind, help=f"{singular} hubs: one note per {singular} with an append-only log")
        sub = parser.add_subparsers(dest="hub_command", required=True)
        p = sub.add_parser("list", help=f"list {kind}")
        p.add_argument("--json", action="store_true")
        p = sub.add_parser("show", help=f"show a {singular} hub and the notes that point at it")
        p.add_argument("name", help="slug, title, entity or alias")
        p.add_argument("--json", action="store_true")
        p = sub.add_parser("add", help=f"create a {singular} hub")
        p.add_argument("slug")
        p.add_argument("--title", required=True, help="H1; for people the full name as in yaams")
        for flag, dest, action in _ADD_FIELDS[kind]:
            p.add_argument(flag, dest=dest, action=action)
        for heading in hubs._SECTIONS[kind][:-1]:
            p.add_argument(f"--{heading.lower().replace(' ', '-')}", dest=f"section_{heading}", default="")
        p.add_argument("--scope", default="work")
        p.add_argument("--lang", default="en")
        p.add_argument("--agent", action="store_true", help="agent-written: marked reviewed: false")
        p.add_argument("--json", action="store_true")
        p = sub.add_parser("log", help="append a dated line to ## Log")
        p.add_argument("name")
        p.add_argument("text")
        p.add_argument("--date", help="YYYY-MM-DD (default today, UTC)")
        p.add_argument("--agent", action="store_true", help="agent-written: line marked (agent)")
        p.add_argument("--json", action="store_true")
        p = sub.add_parser("set", help="replace a section (e.g. State); ## Log is append-only")
        p.add_argument("name")
        p.add_argument("--section", required=True)
        p.add_argument("text")
        p.add_argument("--agent", action="store_true", help="agent-written: note marked reviewed: false")
        p.add_argument("--json", action="store_true")
        p = sub.add_parser("resolve", help="slug for a name (hubs first, then yaams aliases)")
        p.add_argument("name")
    parser = subparsers.add_parser("unreviewed", help="list or accept agent-written content")
    parser.add_argument("--accept", nargs="+", metavar="PATH", help="mark these notes reviewed")
    parser.add_argument("--json", action="store_true")


def _emit(args: argparse.Namespace, payload: Any, text: str) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2) if getattr(args, "json", False) else text)


def _slug(kind: str, name: str) -> str:
    if hubs.hub_path(kind, name).exists():
        return name
    slug = hubs.resolve(kind, name)
    if slug is None:
        raise SystemExit(f"error: no {kind} hub matches {name!r} (try `ledger {kind} list`)")
    return slug


def handle(args: argparse.Namespace) -> int:
    kind, cmd = args.command, args.hub_command
    if cmd == "list":
        rows = hubs.list_hubs(kind)
        lines = [
            f"- {r['slug']} | {r['title']}"
            + (f" | {r['status']}" if r["status"] else "")
            + (f" | last {r['last']}" if r["last"] else "")
            + (f" | {r['unreviewed']} unreviewed" if r["unreviewed"] else "")
            for r in rows
        ]
        _emit(args, rows, f"{kind} ({len(rows)}):\n" + "\n".join(lines) if rows else f"{kind}: none")
    elif cmd == "show":
        doc = hubs.show(kind, _slug(kind, args.name))
        connected = "\n".join(f"- {p}" for p in doc["connected"]) or "- (none)"
        _emit(args, doc, f"{doc['text'].rstrip()}\n\n## Connected (generated)\n{connected}")
    elif cmd == "add":
        fields = {dest: getattr(args, dest) for _flag, dest, _a in _ADD_FIELDS[kind]}
        sections = {h: getattr(args, f"section_{h}") for h in hubs._SECTIONS[kind][:-1]}
        path = hubs.create(kind, args.slug, args.title, sections=sections, fields=fields,
                           agent=args.agent, scope=args.scope, lang=args.lang)
        _emit(args, {"ok": True, "path": str(path)}, f"created {path}")
    elif cmd == "log":
        path = hubs.log(kind, _slug(kind, args.name), args.text, agent=args.agent, date=args.date)
        _emit(args, {"ok": True, "path": str(path)}, f"logged to {path}")
    elif cmd == "set":
        path = hubs.set_section(kind, _slug(kind, args.name), args.section, args.text, agent=args.agent)
        _emit(args, {"ok": True, "path": str(path)}, f"updated {args.section} in {path}")
    elif cmd == "resolve":
        slug = hubs.resolve(kind, args.name)
        print(slug or "")
        return 0 if slug else 1
    return 0


def handle_unreviewed(args: argparse.Namespace) -> int:
    if args.accept:
        cleared = {p: hubs.accept(p) for p in args.accept}
        _emit(args, cleared, "\n".join(f"- {p}: {n} mark(s) cleared" for p, n in cleared.items()))
        return 0
    rows = hubs.unreviewed()
    _emit(args, rows, "\n".join(f"- {r['path']} ({r['unreviewed']})" for r in rows) or "nothing unreviewed")
    return 0

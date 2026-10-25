"""CLI registration for lesson improvement workflows (loaded on demand)."""
from __future__ import annotations

import json
import sys

from ledger.config import get_config
from ledger.conventions import emit_action, action_envelope


def register(subparsers):
    parser = subparsers.add_parser("improvements", help="Act on recurring signal patterns")
    commands = parser.add_subparsers(dest="improvement_command", required=True)
    for name in ("list", "show", "sync", "resolve", "dismiss", "defer", "reopen"):
        p = commands.add_parser(name)
        p.add_argument("--json", action="store_true")
        if name not in {"list", "sync"}:
            p.add_argument("id")
        if name == "list":
            p.add_argument("--state", choices=["all", "open", "resolved", "dismissed", "deferred"], default="open")
        if name == "resolve":
            p.add_argument("--artifact", default="")
        if name in {"resolve", "dismiss"}:
            p.add_argument("--reason", default="")
        if name == "defer":
            p.add_argument("--until", required=True)


def handle(args):
    from ledger import improvements
    try:
        root = get_config().ledger_notes_dir
        command = args.improvement_command
        if command in {"list", "show"}:
            items = improvements.queue(root)
            if command == "show":
                result = next((i for i in items if i["id"] == args.id), None)
                if result is None:
                    raise KeyError(args.id)
            else:
                result = [i for i in items if args.state == "all" or i["state"] == args.state]
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            elif command == "show":
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for item in result:
                    print(f"{item['id']} [{item['state']}] {item['kind']}: {item['subject']}")
            return 0
        if command == "sync":
            result = {"observed": improvements.sync(root)}
        else:
            result = improvements.transition(root, args.id, command,
                artifact=getattr(args, "artifact", ""), reason=getattr(args, "reason", ""),
                until=getattr(args, "until", ""))
        emit_action(action_envelope(command=f"improvements {command}", ok=True, stats=result))
        return 0
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyError:
        print("error: improvement not found", file=sys.stderr)
        return 4

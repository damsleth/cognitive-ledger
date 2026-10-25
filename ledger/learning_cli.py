"""CLI registration for lesson improvement workflows (loaded on demand)."""
from __future__ import annotations

import json
import sys
import shlex
import subprocess
from pathlib import Path
import yaml

from ledger.config import get_config
from ledger.conventions import emit_action, action_envelope


def register(subparsers):
    audit = subparsers.add_parser("audit", help="Audit explicitly selected instruction sources")
    audits = audit.add_subparsers(dest="audit_command", required=True)
    p = audits.add_parser("instructions")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--max-chars", type=int, default=60000)
    p.add_argument("--max-files", type=int, default=100)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--command", dest="reviewer_command", help="Explicit reviewer command; receives JSON evidence on stdin")
    mode.add_argument("--findings", type=Path, help="Validate a saved review against the current bundle")
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--enqueue", action="store_true", help="Explicitly append validated findings to improvements")
    p.add_argument("--json", action="store_true")
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


def handle_audit(args):
    from ledger.instruction_audit import build_bundle, audit_prompt, validate_findings, queue_items
    from ledger.synthesize.llm import SubprocessAdapter
    from ledger.improvements import sync
    try:
        root = get_config().ledger_notes_dir
        if args.enqueue and not args.findings:
            raise ValueError("Enqueue requires --findings from a reviewed report; model output is report-only")
        bundle = build_bundle(args.manifest, root, max_chars=args.max_chars, max_files=args.max_files)
        findings = []
        reviewed = False
        reviewer = getattr(args, "reviewer_command", None)
        if reviewer:
            if args.timeout <= 0:
                raise ValueError("Timeout must be positive")
            response = SubprocessAdapter(shlex.split(reviewer), timeout=args.timeout).complete(audit_prompt(bundle))
            findings = validate_findings(bundle, json.loads(response.text))
            reviewed = True
        elif args.findings:
            findings = validate_findings(bundle, json.loads(args.findings.read_text()))
            reviewed = True
        result = {"bundle": bundle, "semantic_reviewed": reviewed,
                  "findings": bundle["deterministic_findings"] + findings}
        if args.enqueue:
            result["enqueued"] = sync(root, queue_items(result["findings"]))
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            print(f"Sources: {len(bundle['documents'])}; omitted: {len(bundle['omitted'])}; "
                  f"findings: {len(result['findings'])}; semantic review: {reviewed}")
            for finding in result["findings"]:
                print(f"[{finding['kind']}] {finding['subject']}: {finding['suggested_action']}")
            if not reviewed:
                print("Use --json for the evidence bundle; --command or --findings for semantic review.")
        return 0
    except (ValueError, OSError, RuntimeError, yaml.YAMLError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def handle_behaviour(args):
    from ledger.behaviour import run_suite
    try:
        result = run_suite(Path(args.cases), get_config().ledger_notes_dir,
                           command=shlex.split(args.behaviour_command) if args.behaviour_command else None,
                           model=args.behaviour_model,
                           instructions=args.instructions.read_text() if args.instructions else "",
                           runs=args.runs, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        else:
            for arm, stats in result["totals"].items():
                print(f"{arm}: {stats['passed']}/{stats['total']} passed")
            print(f"Agent pass-rate delta: {result['agent_pass_rate_delta']}")
            for row in result["results"]:
                if not row["passed"]:
                    print(f"FAIL {row['case']} {row['arm']}: {', '.join(row['failures'])}")
        candidate = result["totals"]["candidate"]
        return 0 if candidate["passed"] == candidate["total"] else 5
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

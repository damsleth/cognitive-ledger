"""`ledger inbox`: list, triage, reject and clean up inbox captures."""

from __future__ import annotations

import sys


def handle_inbox_command(args):
    import json
    from ledger.inbox import list_inbox, triage_suggestions

    sub = getattr(args, "inbox_command", None)

    if sub == "list":
        items = list_inbox()
        if not items:
            print("Inbox is empty.")
            return
        print(f"Inbox ({len(items)} items):")
        for item in items:
            print(f"  {item['filename']} - {item['title']}")

    elif sub == "triage":
        if not getattr(args, "plain", False):
            from ledger.inbox_triage import run_interactive_triage
            raise SystemExit(run_interactive_triage())
        suggestions = triage_suggestions()
        if not suggestions:
            print("Inbox is empty.")
            return
        print(f"Triage suggestions ({len(suggestions)} items):")
        for s in suggestions:
            print(f"  {s['filename']}")
            print(f"    -> {s['suggested_type']} ({s['reason']})")

    elif sub == "cleanup":
        from ledger.inbox import cleanup_inbox
        result = cleanup_inbox(stale_days=args.days, apply=args.apply)
        orphaned = result["orphaned_locks"]
        stale = result["stale_items"]
        unheld = result.get("unheld_locks") or []
        label = "Removed" if args.apply else "Would remove"
        if not orphaned and not stale and not unheld:
            print("Nothing to clean up.")
            return
        if orphaned:
            print(f"{label} {len(orphaned)} orphaned lock file(s):")
            for f in orphaned:
                print(f"  {f}")
        if unheld:
            print(f"{label} {len(unheld)} unheld lock file(s) across the notes tree:")
            for f in unheld:
                print(f"  {f}")
        if stale:
            print(f"{label} {len(stale)} stale auto-generated item(s):")
            for f in stale:
                print(f"  {f}")
        if not args.apply:
            print("\nRun with --apply to remove these files.")

    elif sub == "reject":
        from ledger.inbox import reject_inbox_item

        try:
            result = reject_inbox_item(
                args.path,
                reason=args.reason,
                remove=not getattr(args, "keep", False),
            )
        except (FileNotFoundError, ValueError) as exc:
            if getattr(args, "json", False):
                payload = {
                    "tool": "ledger",
                    "command": "inbox reject",
                    "ok": False,
                    "error": {"code": "invalid_input", "message": str(exc)},
                }
                print(json.dumps(payload, ensure_ascii=False))
            else:
                print(f"ledger inbox reject: {exc}", file=sys.stderr)
            raise SystemExit(1)

        if getattr(args, "json", False):
            payload = {
                "tool": "ledger",
                "command": "inbox reject",
                "ok": True,
                **result,
            }
            print(json.dumps(payload, ensure_ascii=False))
        else:
            verb = "rejected" if result["removed"] else "logged rejection for"
            print(f"{verb} {result['filename']} (reason: {result['reason']})")
            print(f"  logged to {result['rejected_to']}")

    elif sub == "rejected":
        import sys
        from datetime import datetime, timezone
        from ledger.inbox import list_rejections, clear_rejections

        # Parse --since
        since_days: int | None = None
        since_raw = getattr(args, "since", None)
        if since_raw is not None:
            val = since_raw.rstrip("d")
            try:
                since_days = int(val)
            except ValueError:
                print(
                    f"ledger inbox rejected: invalid --since: {since_raw}",
                    file=sys.stderr,
                )
                raise SystemExit(1)

        do_clear = getattr(args, "clear", False)
        before_raw = getattr(args, "before", None)
        do_json = getattr(args, "json", False)
        do_yes = getattr(args, "yes", False)
        do_verbose = getattr(args, "verbose", False)

        if before_raw is not None and not do_clear:
            print(
                "ledger inbox rejected: --before requires --clear",
                file=sys.stderr,
            )
            raise SystemExit(1)

        # Parse --before
        before_dt = None
        if before_raw is not None:
            try:
                before_dt = datetime.strptime(before_raw, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                print(
                    f"ledger inbox rejected: invalid --before date: {before_raw}",
                    file=sys.stderr,
                )
                raise SystemExit(1)

        if do_clear:
            # Confirmation prompt unless --yes or --json.
            if not do_yes and not do_json:
                prompt = "Proceed? [y/N] "
                try:
                    answer = input(prompt).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = ""
                if answer not in ("y", "yes"):
                    print("Aborted.")
                    return

            count = clear_rejections(before=before_dt)
            if do_json:
                print(json.dumps({
                    "tool": "ledger",
                    "command": "inbox rejected",
                    "ok": True,
                    "count": count,
                    "records": [],
                }, ensure_ascii=False))
            else:
                print(f"Removed {count} rejection record(s).")
        else:
            records = list_rejections(since_days=since_days)
            if do_json:
                print(json.dumps({
                    "tool": "ledger",
                    "command": "inbox rejected",
                    "ok": True,
                    "count": len(records),
                    "records": records,
                }, ensure_ascii=False))
            else:
                if not records:
                    print("No rejection records found.")
                    return
                print(f"Rejected candidates ({len(records)}):")
                for rec in records:
                    # Format timestamp as YYYY-MM-DD HH:MM
                    ts_raw = rec.get("rejected_at", "")
                    try:
                        ts_dt = datetime.strptime(ts_raw, "%Y-%m-%dT%H:%M:%SZ")
                        ts_display = ts_dt.strftime("%Y-%m-%d %H:%M")
                    except ValueError:
                        ts_display = ts_raw
                    print(f"  {ts_display} {rec.get('filename', '')}")
                    print(f"    id: {rec.get('yaams_candidate_id', '')}  entity: {rec.get('yaams_entity', '')}  reason: {rec.get('reason', '')}")
                    if do_verbose:
                        ids = rec.get("yaams_source_item_ids", [])
                        if ids:
                            print(f"    source_ids: {', '.join(str(i) for i in ids)}")

    elif sub == "conflicts":
        import json as _json
        from ledger.inbox import load_candidates_for_triage
        from ledger.parsing.frontmatter import parse_frontmatter_text

        do_json = getattr(args, "json", False)
        candidates = load_candidates_for_triage()
        conflicts = [c for c in candidates if c.conflict_classification == "contradict"]

        def _extract_statement_section(body: str) -> str | None:
            lines = body.splitlines()
            in_section = False
            section_lines: list[str] = []
            for line in lines:
                if line.strip() == "## Statement":
                    in_section = True
                    continue
                if in_section:
                    if line.startswith("## "):
                        break
                    section_lines.append(line)
            if not section_lines:
                return None
            return "\n".join(section_lines).strip() or None

        if do_json:
            records = []
            for c in conflicts:
                statement = _extract_statement_section(c.body) or c.body.strip()
                records.append({
                    "filename": c.filename,
                    "merge_with": c.merge_with,
                    "conflict_classification": c.conflict_classification,
                    "conflict_confidence": c.conflict_confidence,
                    "conflict_reason": c.conflict_reason,
                    "statement": statement,
                })
            print(_json.dumps({
                "tool": "ledger",
                "command": "inbox conflicts",
                "ok": True,
                "conflicts": records,
            }, ensure_ascii=False))
        else:
            if not conflicts:
                print("No conflict candidates.")
                return
            print(f"Conflict candidates ({len(conflicts)}):")
            for c in conflicts:
                statement = _extract_statement_section(c.body) or c.body.strip()
                print(f"\n  {c.filename}")
                print(f"    classification: {c.conflict_classification}")
                if c.conflict_confidence is not None:
                    print(f"    confidence:     {c.conflict_confidence:.2f}")
                if c.conflict_reason:
                    print(f"    reason:         {c.conflict_reason}")
                print(f"    conflicts with: {c.merge_with or '(unknown)'}")
                print(f"    statement:      {statement[:200]}")

    else:
        print("Usage: ledger inbox {list|triage|cleanup|reject|rejected|conflicts}")
        raise SystemExit(1)

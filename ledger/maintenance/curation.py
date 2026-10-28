"""Consolidation checklist, links, provenance and duplicates."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from ledger.layout import INBOX_SUBDIR, INDICES_SUBDIR, NOTE_LAYOUTS
from ledger.config import get_config
from .inventory import _config_paths
from .sync import _is_tracked_path, _timeline_jsonl_entries


def _paths_changed_since_last_sleep() -> set[str] | None:
    """Logical note paths touched since the last `sleep` event; None if never slept."""
    entries = _timeline_jsonl_entries(get_config().timeline_jsonl_path)
    sleeps = [i for i, entry in enumerate(entries) if entry[2] == "sleep"]
    if not sleeps:
        return None
    return {path for _l, _ts, _a, path, _d in entries[sleeps[-1] + 1:] if _is_tracked_path(path)}


def cmd_links(*, apply: bool = False, all_notes: bool = False, as_json: bool = False) -> int:
    """Propose [[links]] between related notes (plan 11); write them with --apply."""
    from ledger import link_proposals as lp

    only = None if all_notes else _paths_changed_since_last_sleep()
    proposals = lp.propose_links(only=only)
    written: list[str] = []
    if apply and proposals:
        from ledger.embeddings import append_timeline_entry

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for proposal in proposals:
            if lp.apply_link(proposal, now):
                written.append(proposal.source)
        for source in sorted(set(written)):
            targets = [p.target for p in proposals if p.source == source]
            # Action "linked", not "updated": maintenance, not work on the
            # note, so the briefing does not read it as fresh activity.
            append_timeline_entry("linked", source, "sleep links: +" + ", ".join(Path(t).stem for t in targets))
    if as_json:
        print(json.dumps({
            "scope": "all" if only is None else "changed_since_last_sleep",
            "proposals": [p.__dict__ for p in proposals],
            "applied": bool(apply),
            "notes_written": sorted(set(written)),
        }, ensure_ascii=False))
        return 0
    scope = "all notes" if only is None else f"{len(only)} note(s) changed since last sleep"
    print(f"Link proposals ({scope}, floor {lp.FLOOR}): {len(proposals)}")
    for p in proposals:
        print(f"  {p.score:.3f}  {Path(p.source).stem}  ->  [[{Path(p.target).stem}]]")
    if apply:
        print(f"Wrote {len(written)} link(s) into {len(set(written))} note(s).")
    elif proposals:
        print("Dry run. Re-run with --apply to write them under ## Links.")
    return 0


def cmd_sleep(as_json: bool = False) -> int:
    _notes_dir, _indices_dir, _timeline = _config_paths()

    _SLEEP_ITEMS = [
        {
            "step": 1,
            "title": "Triage inbox",
            "command": f"fd . notes/{INBOX_SUBDIR}",
            "guidance": "Promote, archive, or delete each item",
        },
        {
            "step": 2,
            "title": "Review open loops",
            "command": f"fd {NOTE_LAYOUTS['loops'].prefix} notes/{NOTE_LAYOUTS['loops'].subdir}",
            "guidance": "Close resolved, snooze stale, ensure next actions exist",
        },
        {
            "step": 3,
            "title": "Check for duplicates",
            "command": f"sheep index && head -20 notes/{INDICES_SUBDIR}/sizes.md",
            "guidance": "Merge similar notes, archive duplicates",
        },
        {
            "step": 4,
            "title": "Regenerate indices",
            "command": "sheep index",
            "guidance": "",
        },
        {
            "step": 5,
            "title": "Lint",
            "command": "sheep lint",
            "guidance": "Fix any errors",
        },
        {
            "step": "5b",
            "title": "Contradiction scan",
            "command": "ledger sleep contradictions --check",
            "guidance": "Requires contradiction_enabled=true in config. Use --apply to execute.",
        },
        {
            "step": "5c",
            "title": "Provenance stamp",
            "command": "ledger sleep provenance --check",
            "guidance": "Stamp provenance:corrected on corrected notes. Use --apply to write.",
        },
        {
            "step": "5d",
            "title": "Propose links",
            "command": "ledger sleep links",
            "guidance": "Review [[link]] proposals between related notes. Use --apply to write them.",
        },
        {
            "step": 6,
            "title": "Mark sleep complete",
            "command": (
                f"{sys.executable} -c "
                "\"from ledger.embeddings import append_timeline_entry; "
                "append_timeline_entry('sleep', '-', 'consolidation complete')\""
            ),
            "guidance": "timeline.jsonl is the source of truth",
        },
        {
            "step": "6b",
            "title": "Re-baseline sync state",
            "command": "sheep sync --apply",
            "guidance": (
                "Without this, `sheep status` keeps recommending sleep on sync_drift "
                "right after a completed sleep. Add --accept-drift if it refuses."
            ),
        },
        {
            "step": 7,
            "title": "Commit",
            "command": 'git add -A && git commit -m "sleep: weekly consolidation"',
            "guidance": "",
        },
    ]

    if as_json:
        payload = {"items": _SLEEP_ITEMS}
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print("=== Electric Sheep - Sleep Checklist ===")
    print("")
    print("Follow these steps to consolidate the ledger.")
    print("Use judgment; this is a guide, not a script.")
    print("")
    print("1. Triage inbox")
    print(f"   fd . notes/{INBOX_SUBDIR}")
    print("   -> Promote, archive, or delete each item")
    print("")
    print("2. Review open loops")
    print(f"   fd {NOTE_LAYOUTS['loops'].prefix} notes/{NOTE_LAYOUTS['loops'].subdir}")
    print("   -> Close resolved, snooze stale, ensure next actions exist")
    print("")
    print("3. Check for duplicates")
    print(f"   sheep index && head -20 notes/{INDICES_SUBDIR}/sizes.md")
    print("   -> Merge similar notes, archive duplicates")
    print("")
    print("4. Regenerate indices")
    print("   sheep index")
    print("")
    print("5. Lint")
    print("   sheep lint")
    print("   -> Fix any errors")
    print("")
    print("5b. Contradiction scan (requires contradiction_enabled=true in config)")
    print("    ledger sleep contradictions --check   # dry run, see what would be done")
    print("    ledger sleep contradictions --apply   # write conflict notes (+ supersessions if opted in)")
    print("")
    print("5c. Provenance stamp (mark corrected notes so confidence weighting can use it)")
    print("    ledger sleep provenance --check   # dry run, see what would be stamped")
    print("    ledger sleep provenance --apply   # write provenance:corrected")
    print("")
    print("6. Mark sleep complete")
    print("   # timeline.jsonl is the source of truth; appending to timeline.md is")
    print("   # silently wiped on the next `sheep index`. Use the canonical helper:")
    print(
        f"   {sys.executable} -c "
        "\"from ledger.embeddings import append_timeline_entry; "
        "append_timeline_entry('sleep', '-', 'consolidation complete')\""
    )
    print("")
    print("6b. Re-baseline sync state (else `sheep status` nags on sync_drift forever)")
    print("    sheep sync --check                  # what drifted since last baseline")
    print("    sheep sync --apply                  # write current snapshot as baseline")
    print("    sheep sync --apply --accept-drift   # required if --apply refuses;")
    print("                                        # logs an audited accept_drift event")
    print("")
    print("7. Commit")
    print('   git add -A && git commit -m "sleep: weekly consolidation"')
    print("")
    return 0


def cmd_provenance(apply: bool = False, as_json: bool = False) -> int:
    """Stamp ``provenance: corrected`` on notes with real correction signals (plan 42).

    A note that has been corrected (an affirmed correction signal) should carry
    the ``corrected`` provenance class so provenance-weighted confidence values
    it at 0.90 rather than its derived default. Only notes whose ``provenance``
    is currently absent are stamped — an explicit value the user set is left
    alone. Dry-run by default; pass ``apply=True`` to write.
    """
    import json as _json

    from ledger.config import get_config
    from ledger.layout import resolve_path
    from ledger.parsing.frontmatter import parse_frontmatter_text, serialize_frontmatter
    from ledger.io import safe_write_text
    from ledger.signals import summarize_signals

    cfg = get_config()
    summary = summarize_signals(signals_path=cfg.signals_path)
    notes = summary.get("notes", {})

    changed: list[str] = []
    skipped_explicit: list[str] = []
    for rel_path, stats in notes.items():
        # Real (non-synthetic) corrections only.
        real_corrections = float(stats.get("corrections", 0.0)) - float(
            stats.get("synthetic_corrections", 0.0)
        )
        if real_corrections <= 0:
            continue
        abs_path = Path(
            resolve_path(rel_path, ledger_root=cfg.ledger_root, ledger_notes_dir=cfg.ledger_notes_dir)
        )
        if not abs_path.is_file():
            continue
        text = abs_path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter_text(text)
        current = str(fm.get("provenance", "") or "").strip().lower()
        if current == "corrected":
            continue
        if current:
            skipped_explicit.append(rel_path)
            continue
        if apply:
            fm["provenance"] = "corrected"
            safe_write_text(abs_path, serialize_frontmatter(fm) + "\n" + body.lstrip("\n"))
        changed.append(rel_path)

    if as_json:
        print(_json.dumps(
            {"apply": apply, "changed": changed, "skipped_explicit": skipped_explicit,
             "count": len(changed)}, ensure_ascii=False))
        return 0

    verb = "Stamped" if apply else "Would stamp"
    print(f"provenance: {verb} {len(changed)} note(s) as 'corrected'.")
    for rel_path in changed:
        print(f"  {rel_path}")
    if skipped_explicit:
        print(f"  (left {len(skipped_explicit)} note(s) with explicit provenance unchanged)")
    if not apply and changed:
        print("\nRun with --apply to write these changes.")
    return 0


def _cmd_duplicates(threshold: float = 0.65, as_json: bool = False) -> int:
    """Scan for duplicate or near-duplicate notes."""
    import json as _json
    from ledger.config import get_config
    from ledger.duplicates import scan_duplicates

    cfg = get_config()
    notes_dir = getattr(cfg, "ledger_notes_dir", None)
    if not notes_dir:
        if as_json:
            print(_json.dumps({"error": "ledger_notes_dir not configured"}, ensure_ascii=False))
        else:
            print("error: ledger_notes_dir not configured", flush=True)
        return 1

    findings = scan_duplicates(notes_dir, jaccard_threshold=threshold)

    if as_json:
        out = {
            "notes_dir": str(notes_dir),
            "threshold": threshold,
            "count": len(findings),
            "findings": [f.to_dict() for f in findings],
        }
        print(_json.dumps(out, ensure_ascii=False))
        return 0

    print(f"duplicates scan: {len(findings)} finding(s) (threshold={threshold:.2f})")
    if not findings:
        print("  No duplicates found.")
        return 0

    for f in findings:
        print(f"  [{f.reason}] score={f.score:.3f}")
        print(f"    a: {f.path_a}")
        print(f"    b: {f.path_b}")
        if f.details:
            for k, v in f.details.items():
                print(f"    {k}: {v}")
    return 0

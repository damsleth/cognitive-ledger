"""Briefing system for Cognitive Ledger.

Generates daily and weekly briefings with open loop nudging,
recent changes, maintenance status, and suggested actions.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.notes import get_notes, BaseNote
from ledger.parsing.frontmatter import parse_frontmatter_text, parse_timestamp
from ledger import timeline as timeline_lib


NUDGE_LOG_FILENAME = "nudge_log.json"


def _nudge_log_path() -> Path:
    config = get_config()
    return config.notes_dir / "08_indices" / NUDGE_LOG_FILENAME


def _load_nudge_log() -> dict[str, Any]:
    path = _nudge_log_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_nudge_log(log: dict[str, Any]) -> None:
    from ledger.io.safe_write import safe_write_text
    safe_write_text(_nudge_log_path(), json.dumps(log, indent=2, ensure_ascii=False) + "\n")


def _days_since(ts_str: str, now: datetime) -> int:
    ts = parse_timestamp(ts_str)
    if ts is None:
        return 999
    return max(0, (now.date() - ts.date()).days)


def _loop_staleness(loop: BaseNote, now: datetime) -> int:
    """Days since the loop was last updated."""
    return _days_since(loop.updated, now)


def _should_nudge(loop_path: str, nudge_log: dict, now: datetime) -> bool:
    """Check if we should nudge this loop (not nudged in last 3 days)."""
    entry = nudge_log.get(loop_path)
    if entry is None:
        return True
    last_nudge = parse_timestamp(entry.get("last_nudge", ""))
    if last_nudge is None:
        return True
    return (now - last_nudge).days >= 3


def _record_nudge(loop_path: str, nudge_log: dict, now: datetime) -> None:
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = nudge_log.get(loop_path, {"last_nudge": "", "count": 0})
    entry["last_nudge"] = ts
    entry["count"] = entry.get("count", 0) + 1
    nudge_log[loop_path] = entry


def daily_briefing() -> str:
    """Generate a daily briefing as structured markdown.

    Returns:
        Markdown string with open loops, nudges, recent changes,
        maintenance status, and suggested actions.
    """
    config = get_config()
    now = datetime.now(timezone.utc)
    nudge_log = _load_nudge_log()
    lines: list[str] = ["# Daily Briefing", ""]

    # Open loops sorted by staleness
    open_loops = get_notes("loops", loop_status="open")
    blocked_loops = get_notes("loops", loop_status="blocked")
    all_active = open_loops + blocked_loops

    if all_active:
        lines.append(f"## Open Loops ({len(all_active)})")
        lines.append("")

        # Sort by staleness (most stale first)
        all_active.sort(key=lambda l: _loop_staleness(l, now), reverse=True)

        nudge_candidates: list[BaseNote] = []
        for loop in all_active:
            staleness = _loop_staleness(loop, now)
            status_tag = f" [{loop.status}]" if loop.status == "blocked" else ""
            stale_tag = ""
            if staleness > 21:
                stale_tag = " **[stale >21d]**"
            elif staleness > 14:
                stale_tag = " **[stale >14d]**"
            elif staleness > 7:
                stale_tag = " *[>7d]*"

            lines.append(f"- {loop.title}{status_tag}{stale_tag} ({staleness}d ago)")

            if staleness > 7 and _should_nudge(str(loop.path), nudge_log, now):
                nudge_candidates.append(loop)

        lines.append("")

        # Nudges
        if nudge_candidates:
            lines.append("## Nudges")
            lines.append("")
            for loop in nudge_candidates[:5]:
                staleness = _loop_staleness(loop, now)
                if staleness > 21:
                    lines.append(f"- **{loop.title}** - open {staleness}d. Close, delegate, or snooze?")
                elif loop.status == "blocked" and staleness > 14:
                    lines.append(f"- **{loop.title}** - blocked {staleness}d. Still blocked? What's needed?")
                else:
                    lines.append(f"- **{loop.title}** - no update in {staleness}d. Quick update?")
                _record_nudge(str(loop.path), nudge_log, now)
            lines.append("")

            _save_nudge_log(nudge_log)
    else:
        lines.append("## Open Loops")
        lines.append("")
        lines.append("No open loops. Nice work!")
        lines.append("")

    # Recent changes (last 24h)
    since = now - timedelta(hours=24)
    recent = timeline_lib.timeline_since(config.timeline_jsonl_path, since)
    if recent:
        lines.append(f"## Recent Changes ({len(recent)} in last 24h)")
        lines.append("")
        for event in recent[-10:]:
            lines.append(f"- {event.get('action', '?')} `{event.get('path', '?')}` - {event.get('desc', '')}")
        lines.append("")

    # Things3 integration (optional)
    if shutil.which("things-cli"):
        lines.append("## Things3")
        lines.append("")
        lines.append("Run `things-cli today` for today's tasks.")
        lines.append("")

    # Maintenance check
    lines.append("## Maintenance")
    lines.append("")
    lines.append("Run `./scripts/sheep status` for maintenance status.")
    lines.append("")

    # Suggested actions
    stale_count = sum(1 for l in all_active if _loop_staleness(l, now) > 14)
    inbox_count = len(list((config.notes_dir / "00_inbox").glob("*.md"))) if (config.notes_dir / "00_inbox").is_dir() else 0

    suggestions: list[str] = []
    if stale_count:
        suggestions.append(f"{stale_count} loop(s) stale >14d - close, snooze, or update?")
    if inbox_count:
        suggestions.append(f"{inbox_count} inbox item(s) - run `ledger inbox triage`")

    if suggestions:
        lines.append("## Suggested Actions")
        lines.append("")
        for s in suggestions:
            lines.append(f"- {s}")
        lines.append("")

    # Motivational close
    closed_recent = len([e for e in recent if e.get("action") == "closed"]) if recent else 0
    if closed_recent:
        lines.append(f"You've closed {closed_recent} loop(s) recently. Keep it up!")
        lines.append("")

    return "\n".join(lines)


def weekly_review() -> str:
    """Generate a weekly review as extended markdown.

    Returns:
        Markdown string with week-over-week stats, stale notes,
        knowledge gaps, and achievements.
    """
    config = get_config()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)

    lines: list[str] = ["# Weekly Review", ""]

    # Week's timeline events
    events = timeline_lib.timeline_since(config.timeline_jsonl_path, since)

    # Stats
    created = sum(1 for e in events if e.get("action") == "created")
    updated = sum(1 for e in events if e.get("action") == "updated")
    closed = sum(1 for e in events if e.get("action") == "closed")
    archived = sum(1 for e in events if e.get("action") == "archived")

    lines.append("## This Week")
    lines.append("")
    lines.append(f"- Notes created: {created}")
    lines.append(f"- Notes updated: {updated}")
    lines.append(f"- Loops closed: {closed}")
    lines.append(f"- Notes archived: {archived}")
    lines.append(f"- Total events: {len(events)}")
    lines.append("")

    # Open loops status
    open_loops = get_notes("loops", loop_status="open")
    blocked_loops = get_notes("loops", loop_status="blocked")

    lines.append(f"## Loop Status ({len(open_loops)} open, {len(blocked_loops)} blocked)")
    lines.append("")

    # Stale loops
    stale = [l for l in open_loops if _loop_staleness(l, now) > 14]
    if stale:
        lines.append(f"### Stale Loops ({len(stale)})")
        lines.append("")
        for loop in stale:
            lines.append(f"- {loop.title} ({_loop_staleness(loop, now)}d since update)")
        lines.append("")

    # Knowledge gaps
    gaps: list[str] = []
    for loop in open_loops:
        if not getattr(loop, "next_action", ""):
            gaps.append(f"- {loop.title} - missing next action")
    if gaps:
        lines.append("### Knowledge Gaps")
        lines.append("")
        lines.extend(gaps)
        lines.append("")

    # Achievements
    if closed:
        lines.append("## Achievements")
        lines.append("")
        lines.append(f"Closed {closed} loop(s) this week!")
        closed_events = [e for e in events if e.get("action") == "closed"]
        for e in closed_events[:10]:
            lines.append(f"- {e.get('desc', e.get('path', ''))}")
        lines.append("")

    return "\n".join(lines)

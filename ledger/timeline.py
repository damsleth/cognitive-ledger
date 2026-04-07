"""Timeline utilities.

Machine source of truth: notes/08_indices/timeline.jsonl
Human view: notes/08_indices/timeline.md (generated)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.io.safe_write import safe_append_line, safe_write_text

TIMELINE_MARKDOWN_HEADER = """# Timeline

Append-only log of meaningful note changes.

Format: `<ISO timestamp> | <action> | <path> | <description>`

---
"""

MARKDOWN_LINE_RE = re.compile(
    r"^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z) \| (created|updated|archived|deleted|closed|sleep) \| (.+?) \| (.*)$"
)


def _infer_type(path: str) -> str:
    normalized = str(path or "").strip()
    if normalized.startswith("notes/01_identity/"):
        return "identity"
    if normalized.startswith("notes/02_facts/"):
        return "fact"
    if normalized.startswith("notes/03_preferences/"):
        return "preference"
    if normalized.startswith("notes/04_goals/"):
        return "goal"
    if normalized.startswith("notes/05_open_loops/"):
        return "loop"
    if normalized.startswith("notes/06_concepts/"):
        return "concept"
    if normalized.startswith("notes/08_indices/"):
        return "index"
    return "other"


def parse_timeline_markdown(timeline_md_path: Path | str) -> list[dict[str, Any]]:
    """Parse markdown timeline entries."""
    path = Path(timeline_md_path)
    if not path.is_file():
        return []

    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = MARKDOWN_LINE_RE.match(line.strip())
        if not match:
            continue
        ts, action, note_path, desc = match.groups()
        events.append(
            {
                "ts": ts,
                "action": action,
                "path": note_path,
                "desc": desc,
                "type": _infer_type(note_path),
            }
        )
    return events


def load_timeline_jsonl(timeline_jsonl_path: Path | str) -> list[dict[str, Any]]:
    """Load timeline events from JSONL."""
    path = Path(timeline_jsonl_path)
    if not path.is_file():
        return []

    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if not obj.get("ts") or not obj.get("action"):
            continue
        events.append(obj)
    return events


def write_timeline_jsonl(timeline_jsonl_path: Path | str, events: list[dict[str, Any]]) -> None:
    """Write JSONL timeline from event list."""
    lines = [json.dumps(event, ensure_ascii=False) for event in events]
    payload = ("\n".join(lines) + "\n") if lines else ""
    safe_write_text(Path(timeline_jsonl_path), payload)


def ensure_timeline_jsonl(
    timeline_md_path: Path | str,
    timeline_jsonl_path: Path | str,
) -> list[dict[str, Any]]:
    """Ensure JSONL exists, migrating from markdown if needed."""
    jsonl_path = Path(timeline_jsonl_path)
    events = load_timeline_jsonl(jsonl_path)
    if events:
        return events

    md_events = parse_timeline_markdown(timeline_md_path)
    if md_events:
        write_timeline_jsonl(jsonl_path, md_events)
    return md_events


def append_timeline_jsonl(timeline_jsonl_path: Path | str, event: dict[str, Any]) -> None:
    """Append a single timeline JSON event."""
    line = json.dumps(event, ensure_ascii=False)
    safe_append_line(Path(timeline_jsonl_path), line)


def render_timeline_markdown(events: list[dict[str, Any]]) -> str:
    """Render markdown timeline view from events."""
    lines = [TIMELINE_MARKDOWN_HEADER.rstrip("\n")]
    if events:
        lines.append("")
    for event in events:
        lines.append(
            f"{event.get('ts', '')} | {event.get('action', '')} | {event.get('path', '')} | {event.get('desc', '')}"
        )
    return "\n".join(lines).rstrip() + "\n"


def regenerate_timeline_markdown(
    timeline_jsonl_path: Path | str,
    timeline_md_path: Path | str,
) -> None:
    """Regenerate timeline markdown from JSONL source."""
    events = load_timeline_jsonl(timeline_jsonl_path)
    events.sort(key=lambda item: str(item.get("ts", "")))
    safe_write_text(Path(timeline_md_path), render_timeline_markdown(events))


def timeline_since(timeline_jsonl_path: Path | str, since: datetime | str) -> list[dict[str, Any]]:
    """Get events at or after a timestamp."""
    if isinstance(since, str):
        since_dt = datetime.strptime(since, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    else:
        since_dt = since

    out = []
    for event in load_timeline_jsonl(timeline_jsonl_path):
        ts = str(event.get("ts", "")).strip()
        try:
            event_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if event_dt >= since_dt:
            out.append(event)
    return out


def timeline_for_note(timeline_jsonl_path: Path | str, note_path: str | Path) -> list[dict[str, Any]]:
    """Get events for a specific note path."""
    target = str(note_path).replace("\\", "/").strip()
    return [
        event
        for event in load_timeline_jsonl(timeline_jsonl_path)
        if str(event.get("path", "")).replace("\\", "/").strip() == target
    ]


__all__ = [
    "parse_timeline_markdown",
    "load_timeline_jsonl",
    "write_timeline_jsonl",
    "ensure_timeline_jsonl",
    "append_timeline_jsonl",
    "render_timeline_markdown",
    "regenerate_timeline_markdown",
    "timeline_since",
    "timeline_for_note",
]

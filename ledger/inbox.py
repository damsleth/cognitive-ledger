"""Inbox triage system for Cognitive Ledger.

Raw passive captures land in notes/00_inbox/. This module provides
listing, triage suggestions, and promotion to proper note types.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.io.safe_write import append_timeline_entry
from ledger.parsing.frontmatter import parse_frontmatter_text


# Heuristics for suggesting target type from content
_TYPE_SIGNALS: list[tuple[str, list[str]]] = [
    ("preferences", ["prefer", "always", "never", "style", "habit", "going forward"]),
    ("facts", ["decided", "confirmed", "learned", "discovered", "is a", "was a"]),
    ("goals", ["goal", "objective", "target", "achieve", "milestone"]),
    ("loops", ["todo", "revisit", "unresolved", "blocked", "next step", "investigate"]),
    ("concepts", ["concept", "framework", "model", "definition", "pattern"]),
]


def _inbox_dir(notes_dir: Path | None = None) -> Path:
    config = get_config()
    nd = notes_dir or config.notes_dir
    return nd / "00_inbox"


def list_inbox(notes_dir: Path | None = None) -> list[dict[str, Any]]:
    """List all items in the inbox.

    Returns:
        List of dicts with path, title, created, tags, confidence.
    """
    inbox = _inbox_dir(notes_dir)
    if not inbox.is_dir():
        return []

    items = []
    for path in sorted(inbox.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter_text(text)

        # Extract title from first H1
        title = path.stem.replace("_", " ")
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        items.append({
            "path": str(path),
            "filename": path.name,
            "title": title,
            "created": str(fm.get("created", "")),
            "tags": fm.get("tags", []),
            "confidence": fm.get("confidence", 0.6),
        })

    return items


def triage_suggestions(notes_dir: Path | None = None) -> list[dict[str, Any]]:
    """Suggest target types for inbox items based on content analysis.

    Returns:
        List of dicts with path, title, suggested_type, reason.
    """
    items = list_inbox(notes_dir)
    suggestions = []

    for item in items:
        path = Path(item["path"])
        text = path.read_text(encoding="utf-8").lower()

        suggested = "facts"  # default fallback
        reason = "no strong signal detected, defaulting to fact"

        for note_type, signals in _TYPE_SIGNALS:
            for signal in signals:
                if signal in text:
                    suggested = note_type
                    reason = f"content contains '{signal}'"
                    break
            if suggested != "facts" or reason != "no strong signal detected, defaulting to fact":
                break

        suggestions.append({
            "path": item["path"],
            "filename": item["filename"],
            "title": item["title"],
            "suggested_type": suggested,
            "reason": reason,
        })

    return suggestions


def promote(
    path: str | Path,
    target_type: str,
    notes_dir: Path | None = None,
) -> Path:
    """Promote an inbox item to a proper note type.

    Moves the file to the correct folder with the correct prefix,
    updates the timeline.

    Args:
        path: Path to the inbox note.
        target_type: Target note type (facts, preferences, goals, loops, concepts).
        notes_dir: Optional notes directory override.

    Returns:
        Path to the promoted note.
    """
    config = get_config()
    nd = notes_dir or config.notes_dir
    source = Path(path)

    if not source.is_file():
        raise FileNotFoundError(f"Inbox item not found: {source}")

    type_config = config.note_types.get(target_type)
    if type_config is None:
        raise ValueError(f"Unknown note type: {target_type}")

    # Generate target filename
    prefix = type_config["label"] + "__"
    slug = source.stem
    # Strip any existing prefix
    for nt in config.note_types.values():
        p = nt["label"] + "__"
        if slug.startswith(p):
            slug = slug[len(p):]
            break
    # Clean slug
    slug = re.sub(r"[^a-z0-9_]", "_", slug.lower()).strip("_")
    if not slug:
        slug = "untitled"

    target_dir = config.root_dir / type_config["dir"]
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{prefix}{slug}.md"

    # Avoid overwriting
    counter = 1
    while target.exists():
        target = target_dir / f"{prefix}{slug}_{counter}.md"
        counter += 1

    shutil.move(str(source), str(target))

    append_timeline_entry(
        config.timeline_path,
        "created",
        target,
        f"promoted from inbox to {target_type}",
        root_dir=config.root_dir,
    )

    return target

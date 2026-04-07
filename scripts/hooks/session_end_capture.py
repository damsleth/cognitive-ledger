#!/usr/bin/env python3
"""Session-end passive capture for Cognitive Ledger.

Diffs the current state against a session baseline to extract
durable artifacts from commit messages and file changes, writing
them as inbox notes for later triage.

Usage: python3 scripts/hooks/session_end_capture.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from ledger.config import get_config
from ledger.io.safe_write import safe_write_text, append_timeline_entry
from ledger.parsing.frontmatter import serialize_frontmatter

BASELINE_PATH = ROOT_DIR / "notes" / "08_indices" / ".session_baseline"

# Keywords that signal durable artifacts in commit messages
DECISION_SIGNALS = re.compile(
    r"(?:decide|chose|pick|select|switch|adopt|migrate|replace|introduce|add|create|implement)",
    re.IGNORECASE,
)
PATTERN_SIGNALS = re.compile(
    r"(?:pattern|convention|architecture|refactor|redesign|restructure)",
    re.IGNORECASE,
)
CONFIG_SIGNALS = re.compile(
    r"(?:config|setting|env|flag|toggle|enable|disable|option)",
    re.IGNORECASE,
)
FIX_SIGNALS = re.compile(
    r"(?:fix|bug|issue|resolve|patch|hotfix|workaround)",
    re.IGNORECASE,
)


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, timeout=10,
        cwd=str(ROOT_DIR),
    )
    return result.stdout.strip()


def _load_baseline() -> dict | None:
    if not BASELINE_PATH.is_file():
        return None
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _classify_commit(message: str) -> str | None:
    """Classify a commit message into a note-worthy category."""
    if DECISION_SIGNALS.search(message):
        return "decision"
    if PATTERN_SIGNALS.search(message):
        return "pattern"
    if CONFIG_SIGNALS.search(message):
        return "config"
    if FIX_SIGNALS.search(message):
        return "fix"
    return None


def _write_inbox_note(title: str, content: str, tags: list[str]) -> Path:
    """Write a note to the inbox."""
    config = get_config()
    inbox_dir = config.notes_dir / "00_inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    slug = re.sub(r"[^a-z0-9_]", "_", title.lower())[:60].strip("_")

    fm = {
        "created": ts,
        "updated": ts,
        "tags": ["inbox", "auto-capture"] + tags,
        "confidence": 0.6,
        "source": "assistant",
        "scope": "dev",
        "lang": "en",
    }

    body = f"\n# {title}\n\n## Content\n{content}\n\n## Source\nAuto-captured by session-end hook.\n"
    note_content = serialize_frontmatter(fm) + body

    path = inbox_dir / f"{slug}.md"
    counter = 1
    while path.exists():
        path = inbox_dir / f"{slug}_{counter}.md"
        counter += 1

    safe_write_text(path, note_content)

    append_timeline_entry(
        config.timeline_path,
        "created",
        path,
        f"auto-captured: {title}",
        root_dir=config.root_dir,
    )

    return path


def main() -> int:
    baseline = _load_baseline()
    notes_created = 0

    if baseline:
        baseline_sha = baseline.get("head_sha", "")

        # Get commits since baseline
        if baseline_sha:
            log = _run_git("log", f"{baseline_sha}..HEAD", "--oneline", "--no-decorate")
            for line in log.splitlines():
                if not line.strip():
                    continue
                parts = line.split(" ", 1)
                if len(parts) < 2:
                    continue
                message = parts[1]
                category = _classify_commit(message)
                if category:
                    _write_inbox_note(
                        title=f"Session: {message[:80]}",
                        content=f"Commit: {line}\nCategory: {category}",
                        tags=[category],
                    )
                    notes_created += 1

        # Check for work left incomplete (uncommitted changes in notes/)
        dirty = _run_git("diff", "--name-only")
        note_changes = [f for f in dirty.splitlines() if f.startswith("notes/") and not f.startswith("notes/08_indices/")]
        if note_changes:
            _write_inbox_note(
                title="Uncommitted note changes",
                content="The following notes have uncommitted changes:\n" + "\n".join(f"- {f}" for f in note_changes),
                tags=["incomplete"],
            )
            notes_created += 1
    else:
        # No baseline - check for uncommitted working-tree diffs only
        dirty = _run_git("diff", "--name-only")
        note_changes = [f for f in dirty.splitlines() if f.startswith("notes/") and not f.startswith("notes/08_indices/")]
        if note_changes:
            _write_inbox_note(
                title="Session notes (no baseline)",
                content="Notes modified this session (no baseline available):\n" + "\n".join(f"- {f}" for f in note_changes),
                tags=["incomplete"],
            )
            notes_created += 1

    # Clean up baseline
    if BASELINE_PATH.is_file():
        BASELINE_PATH.unlink()

    if notes_created:
        print(f"Captured {notes_created} item(s) to inbox.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

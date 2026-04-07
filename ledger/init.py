"""One-command initialization for Cognitive Ledger.

Creates the full notes directory structure, generates config,
optionally imports voice DNA and sets up source root.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.io.safe_write import safe_write_text


# Directories to create under notes/
NOTE_DIRS = [
    "00_inbox",
    "01_identity",
    "02_facts",
    "03_preferences",
    "04_goals",
    "05_open_loops",
    "06_concepts",
    "07_projects",
    "08_indices",
    "09_archive",
]

# Minimal template content (generated inline, no external file dependency)
GENERIC_TEMPLATE = """\
---
created: {ts}
updated: {ts}
tags: [example]
confidence: 0.9
source: user
scope: meta
lang: en
---

# Title

## Statement
One clear, atomic claim or idea.

## Context
Why this matters.

## Implications
- How this should influence future decisions.

## Links
- Related notes (relative links only).
"""

LOOP_TEMPLATE = """\
---
created: {ts}
updated: {ts}
tags: [example]
confidence: 0.8
source: user
status: open
scope: meta
lang: en
---

# Loop: Title

## Question or task
What needs to be resolved.

## Why it matters
Motivation for closing this loop.

## Next action
- [ ] Immediate next step.

## Links
- Related notes.
"""


def init_ledger(
    root: str | Path | None = None,
    voice_dna_path: str | Path | None = None,
    source_root: str | Path | None = None,
    notes_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Initialize a cognitive ledger structure.

    Creates directories, templates, initial config, and optionally
    imports voice DNA and sets up source scanning.

    Args:
        root: Ledger root directory (defaults to config root).
        voice_dna_path: Optional path to voice-dna JSON for import.
        source_root: Optional source notes root for config.
        notes_dir: Optional notes directory override.

    Returns:
        Dict with created, skipped, and errors lists.
    """
    config = get_config()
    root_path = Path(root) if root else config.root_dir
    nd = Path(notes_dir) if notes_dir else root_path / "notes"

    report: dict[str, Any] = {
        "created": [],
        "skipped": [],
        "errors": [],
    }

    # 1. Create directory structure
    for dirname in NOTE_DIRS:
        dir_path = nd / dirname
        if dir_path.is_dir():
            report["skipped"].append(f"dir: {dirname}")
        else:
            dir_path.mkdir(parents=True, exist_ok=True)
            report["created"].append(f"dir: {dirname}")
            # Add .gitkeep for empty dirs
            gitkeep = dir_path / ".gitkeep"
            if not gitkeep.exists():
                gitkeep.touch()

    # 2. Generate templates if not present
    templates_dir = root_path / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    generic_path = templates_dir / "generic_note_template.md"
    if not generic_path.is_file():
        safe_write_text(generic_path, GENERIC_TEMPLATE.format(ts=ts))
        report["created"].append("templates/generic_note_template.md")
    else:
        report["skipped"].append("templates/generic_note_template.md")

    loop_path = templates_dir / "open_loop_template.md"
    if not loop_path.is_file():
        safe_write_text(loop_path, LOOP_TEMPLATE.format(ts=ts))
        report["created"].append("templates/open_loop_template.md")
    else:
        report["skipped"].append("templates/open_loop_template.md")

    # 3. Generate initial config.yaml if not present
    config_path = root_path / "config.yaml"
    if not config_path.is_file():
        config_content = f"""\
# Cognitive Ledger Configuration
# See schema.yaml for full specification

# Paths (override with env vars: LEDGER_ROOT_DIR, LEDGER_NOTES_DIR, LEDGER_SOURCE_ROOT)
# root_dir: {root_path}
# source_root: {source_root or '~/notes'}

# Retrieval tuning (defaults are well-tested, change with care)
# score_weight_bm25: 0.30
# score_weight_lexical: 0.15
# score_weight_tag: 0.15
# score_weight_scope: 0.15
# score_weight_recency: 0.15
# score_weight_confidence: 0.10

# Knowledge compounding
# auto_file_synthesis: false
"""
        safe_write_text(config_path, config_content)
        report["created"].append("config.yaml")
    else:
        report["skipped"].append("config.yaml")

    # 4. Import voice DNA if provided
    if voice_dna_path:
        try:
            from ledger.voice import import_voice_dna
            path = import_voice_dna(voice_dna_path, notes_dir=nd)
            report["created"].append(f"voice-dna: {path.name}")
        except Exception as exc:
            report["errors"].append(f"voice-dna import failed: {exc}")

    # 5. Initialize timeline if not present
    indices_dir = nd / "08_indices"
    indices_dir.mkdir(parents=True, exist_ok=True)
    timeline_md = indices_dir / "timeline.md"
    timeline_jsonl = indices_dir / "timeline.jsonl"

    if not timeline_md.is_file():
        from ledger.timeline import TIMELINE_MARKDOWN_HEADER
        safe_write_text(timeline_md, TIMELINE_MARKDOWN_HEADER)
        report["created"].append("notes/08_indices/timeline.md")
    else:
        report["skipped"].append("notes/08_indices/timeline.md")

    if not timeline_jsonl.is_file():
        timeline_jsonl.touch()
        report["created"].append("notes/08_indices/timeline.jsonl")
    else:
        report["skipped"].append("notes/08_indices/timeline.jsonl")

    # 6. Run initial index generation
    try:
        from ledger.maintenance import cmd_index
        cmd_index()
        report["created"].append("indices (via sheep index)")
    except Exception as exc:
        report["errors"].append(f"index generation failed: {exc}")

    return report

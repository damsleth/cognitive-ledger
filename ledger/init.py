"""One-command initialization for Cognitive Ledger.

Creates the full notes directory structure, generates config,
optionally imports voice DNA and sets up source root.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.config import LedgerConfig, get_config, set_config
from ledger.layout import LEDGER_NOTE_SUBDIRS, indices_dir, timeline_jsonl_path, timeline_path
from ledger.io.safe_write import safe_write_text


NOTE_DIRS = list(LEDGER_NOTE_SUBDIRS)
LEDGER_NOTES_GITIGNORE_LINES = (
    ".DS_Store",
    "*.lock",
    "08_indices/.session_baseline",
    "08_indices/note_index.json",
)

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


def _display_path(path: str | Path | None) -> str | None:
    """Render a path for config.yaml, preferring ~/ for home-relative values."""
    if path is None:
        return None

    resolved = Path(path).expanduser().resolve()
    home = Path.home().resolve()

    try:
        relative = resolved.relative_to(home)
    except ValueError:
        return resolved.as_posix()
    return f"~/{relative.as_posix()}"


def _build_config_content(
    *,
    root_path: Path,
    ledger_notes_dir: Path | None,
    source_notes_dir: str | Path | None,
) -> str:
    ledger_notes_setting = _display_path(ledger_notes_dir)
    source_notes_setting = _display_path(source_notes_dir)

    lines = [
        "# Cognitive Ledger configuration",
        "# See schema.yaml for full specification",
        "",
        "# Set to true on first install. The session_start hook will inject setup guidance.",
        "first_run: true",
        "",
        "# Paths (override with env vars: LEDGER_ROOT, LEDGER_NOTES_DIR, LEDGER_SOURCE_NOTES_DIR)",
        f"# ledger_root: {_display_path(root_path)}",
    ]

    if ledger_notes_setting is not None:
        lines.append(f"ledger_notes_dir: {ledger_notes_setting}")
    else:
        lines.append("# ledger_notes_dir: ~/Code/ledger-notes")

    if source_notes_setting is not None:
        lines.append(f"source_notes_dir: {source_notes_setting}")
    else:
        lines.append("# source_notes_dir: ~/Code/notes")

    lines.extend([
        "",
        "# Retrieval tuning (defaults are well-tested, change with care)",
        "# score_weight_bm25: 0.30",
        "# score_weight_lexical: 0.15",
        "# score_weight_tag: 0.15",
        "# score_weight_scope: 0.15",
        "# score_weight_recency: 0.15",
        "# score_weight_confidence: 0.10",
        "",
        "# Knowledge compounding",
        "# auto_file_synthesis: false",
        "",
    ])
    return "\n".join(lines)


def _ensure_notes_gitignore(notes_dir: Path) -> bool:
    """Ensure generated ledger artifacts stay out of corpus git status."""
    gitignore_path = notes_dir / ".gitignore"
    existing_lines: list[str] = []
    if gitignore_path.is_file():
        existing_lines = gitignore_path.read_text(encoding="utf-8").splitlines()

    present = {line.strip() for line in existing_lines}
    missing = [line for line in LEDGER_NOTES_GITIGNORE_LINES if line not in present]
    if not missing:
        return False

    next_lines = list(existing_lines)
    if next_lines and next_lines[-1].strip():
        next_lines.append("")
    next_lines.extend(missing)
    safe_write_text(gitignore_path, "\n".join(next_lines).rstrip() + "\n")
    return True


def init_ledger(
    root: str | Path | None = None,
    voice_dna_path: str | Path | None = None,
    source_notes_dir: str | Path | None = None,
    ledger_notes_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Initialize a cognitive ledger structure.

    Creates directories, templates, initial config, and optionally
    imports voice DNA and sets up source scanning.

    Args:
        root: Ledger root directory (defaults to config root).
        voice_dna_path: Optional path to voice-dna JSON for import.
        source_notes_dir: Optional source notes root for config.
        ledger_notes_dir: Optional notes directory override.

    Returns:
        Dict with created, skipped, and errors lists.
    """
    config = get_config()
    root_path = Path(root) if root else config.ledger_root
    root_path = root_path.expanduser().resolve()
    nd = Path(ledger_notes_dir) if ledger_notes_dir else root_path / "notes"
    nd = nd.expanduser().resolve()

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

    if _ensure_notes_gitignore(nd):
        report["created"].append("notes/.gitignore")
    else:
        report["skipped"].append("notes/.gitignore")

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
        config_content = _build_config_content(
            root_path=root_path,
            ledger_notes_dir=Path(ledger_notes_dir).expanduser().resolve() if ledger_notes_dir else None,
            source_notes_dir=source_notes_dir,
        )
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
    notes_indices_dir = indices_dir(nd)
    notes_indices_dir.mkdir(parents=True, exist_ok=True)
    timeline_md = timeline_path(nd)
    timeline_jsonl = timeline_jsonl_path(nd)

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
        previous_config = get_config()
        init_config = LedgerConfig(
            ledger_root=root_path,
            ledger_notes_dir=nd,
            source_notes_dir=Path(source_notes_dir).expanduser().resolve() if source_notes_dir else None,
        )
        set_config(init_config)
        try:
            cmd_index()
        finally:
            set_config(previous_config)
        report["created"].append("indices (via sheep index)")
    except Exception as exc:
        report["errors"].append(f"index generation failed: {exc}")

    return report

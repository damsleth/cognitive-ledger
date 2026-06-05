from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.io import safe_append_line, safe_read_text, safe_write_text
from ledger.io.safe_write import append_timeline_entry
from ledger.layout import logical_path
from ledger.text import (  # noqa: F401 — re-exported for intra-backend callers
    count_words,
    frontmatter_to_text,
    infer_lang,
    normalize_statement,
    sha1_file,
    sha1_text,
    slugify,
    write_markdown,
)
from ledger.timeline import append_timeline_jsonl


TIMELINE_HEADER = """# Timeline

Append-only log of meaningful note changes.

Format: `<ISO timestamp> | <action> | <path> | <description>`

---
"""

RESERVED_YAML_SCALARS = {"yes", "no", "true", "false", "null", "~"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def infer_scope_from_relpath(path_rel: str) -> str:
    parts = [part.lower() for part in Path(path_rel).parts]
    if "01-home" in parts or "home" in parts:
        return "home"
    if "02-work" in parts or "work" in parts:
        return "work"
    if "04-dev" in parts or "dev" in parts:
        return "dev"
    if "03-community" in parts or "community" in parts or "volunteer" in parts or "redcross" in parts:
        return "personal"
    if "91-opt" in parts or "opt" in parts or "meta" in parts or "system" in parts:
        return "meta"
    if "90-journal" in parts or "journal" in parts:
        return "personal"
    if "92-archive" in parts or "archive" in parts:
        return "personal"
    return "personal"


def ensure_timeline(path: Path) -> None:
    if path.is_file():
        return
    safe_write_text(path, TIMELINE_HEADER)


def append_timeline(path: Path, action: str, rel_path: str, description: str, ts: str | None = None) -> None:
    notes_root = path.parent.parent
    ledger_root = notes_root.parent
    normalized_path = rel_path.strip().replace("\\", "/")
    vault_prefix = f"{ledger_root.name}/notes/"
    if normalized_path.startswith(vault_prefix):
        normalized_path = normalized_path[len(ledger_root.name) + 1 :]
    note_path = logical_path(
        normalized_path,
        ledger_root=ledger_root,
        ledger_notes_dir=notes_root,
    ).as_posix()

    if ts is not None:
        ensure_timeline(path)
        append_timeline_jsonl(
            path.with_name("timeline.jsonl"),
            {"ts": ts, "action": action, "path": note_path, "desc": description},
        )
        safe_append_line(path, f"{ts} | {action} | {note_path} | {description}")
        return

    append_timeline_entry(
        timeline_path=path,
        action=action,
        note_path=note_path,
        description=description,
        root_dir=ledger_root,
        ledger_notes_dir=notes_root,
    )


def append_log(path: Path, lines: list[str], ts: str | None = None) -> None:
    ts_value = ts or now_iso()
    block = "\n".join(["", "---", "", f"## {ts_value}", "", *lines, ""])
    safe_append_line(path, block)


def is_markdown(path: Path) -> bool:
    return path.suffix.lower() == ".md"


def is_same_or_subpath(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def should_skip_markdown(path: Path, vault_root: Path, exclude_dirs: tuple[str, ...]) -> bool:
    try:
        rel_parts = path.resolve().relative_to(vault_root.resolve()).parts
    except ValueError:
        return True
    lowered = {part.lower() for part in rel_parts}
    for excluded in exclude_dirs:
        if excluded.lower() in lowered:
            return True
    return False

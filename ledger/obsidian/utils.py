from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.io import safe_append_line, safe_read_text, safe_write_text


TIMELINE_HEADER = """# Timeline

Append-only log of meaningful note changes.

Format: `<ISO timestamp> | <action> | <path> | <description>`

---
"""

RESERVED_YAML_SCALARS = {"yes", "no", "true", "false", "null", "~"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def sha1_file(path: Path) -> str:
    hasher = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def slugify(value: str, max_len: int = 80) -> str:
    slug = re.sub(r"['`\"]+", "", value.strip().lower())
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    return (slug[:max_len] or "untitled").strip("_") or "untitled"


def normalize_statement(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def count_words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def infer_lang(content: str) -> str:
    has_no = bool(re.search(r"\b(og|ikke|jeg|du|vi|skal|med|uten|hvor|hva|hvis)\b", content, re.I))
    has_en = bool(re.search(r"\b(and|not|i|you|we|should|with|without|what|if)\b", content, re.I))
    if has_no and has_en:
        return "mixed"
    if has_no:
        return "no"
    if has_en:
        return "en"
    return "mixed"


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


def _serialize_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text:
        return '""'
    lower = text.lower()
    if lower in RESERVED_YAML_SCALARS:
        return json.dumps(text)
    if re.fullmatch(r"[A-Za-z0-9_./:-]+", text):
        return text
    return json.dumps(text)


def frontmatter_to_text(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            serialized = ", ".join(_serialize_scalar(v) for v in value)
            lines.append(f"{key}: [{serialized}]")
            continue
        lines.append(f"{key}: {_serialize_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def write_markdown(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    text = frontmatter_to_text(frontmatter) + "\n\n" + body.rstrip() + "\n"
    safe_write_text(path, text)


def ensure_timeline(path: Path) -> None:
    if path.is_file():
        return
    safe_write_text(path, TIMELINE_HEADER)


def append_timeline(path: Path, action: str, rel_path: str, description: str, ts: str | None = None) -> None:
    ensure_timeline(path)
    ts_value = ts or now_iso()
    safe_append_line(path, f"{ts_value} | {action} | {rel_path} | {description}")


def append_log(path: Path, lines: list[str], ts: str | None = None) -> None:
    ts_value = ts or now_iso()
    existing = safe_read_text(path) if path.is_file() else ""
    block = ["", "---", "", f"## {ts_value}", "", *lines, ""]
    safe_write_text(path, existing.rstrip("\n") + "\n" + "\n".join(block))


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

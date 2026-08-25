"""Shared text utilities for Cognitive Ledger.

Provider-agnostic helpers used by core modules (``claude_memory``,
``embeddings``) and import backends alike. Keep this module light —
stdlib plus leaf ledger modules only — so anything can import it
without creating cycles.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from ledger.io import safe_write_text
from ledger.parsing.frontmatter import serialize_frontmatter


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
    return len(text.split())


def infer_lang(content: str) -> str:
    has_no = bool(re.search(r"\b(og|ikke|jeg|du|vi|skal|med|uten|hvor|hva|hvis)\b", content, re.I))
    # ponytail: "i" is excluded on purpose -- it is the most common Norwegian
    # preposition ("in"), and including it made pure Norwegian text infer as
    # "mixed", which bypasses the strict lang:no contradiction threshold.
    has_en = bool(re.search(r"\b(and|not|you|we|should|with|without|what|if)\b", content, re.I))
    if has_no and has_en:
        return "mixed"
    if has_no:
        return "no"
    if has_en:
        return "en"
    return "mixed"


def frontmatter_to_text(fields: dict[str, Any]) -> str:
    """Serialize a frontmatter dict to YAML text.

    Delegates to the canonical serializer in ledger.parsing.frontmatter.
    """
    return serialize_frontmatter(fields)


def write_markdown(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    text = frontmatter_to_text(frontmatter) + "\n\n" + body.rstrip() + "\n"
    safe_write_text(path, text)

"""Duplicate note detection for the cognitive ledger.

Provides a lightweight, dependency-free scan based on title normalisation,
content-hash fingerprinting, and word-overlap Jaccard similarity.
No ML model required; suitable for pre-embed bootstrapping.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DuplicateFinding:
    """A candidate duplicate pair found by scan_duplicates()."""

    path_a: str
    """Logical path of the first note."""

    path_b: str
    """Logical path of the second note."""

    reason: str
    """Short description of why the pair was flagged (e.g. 'title_overlap')."""

    score: float = 0.0
    """Similarity score in [0.0, 1.0] — higher means more likely a duplicate."""

    details: dict[str, Any] = field(default_factory=dict)
    """Extra context (e.g. common word count, hash, title similarity)."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_a": self.path_a,
            "path_b": self.path_b,
            "reason": self.reason,
            "score": round(self.score, 4),
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _normalize_title(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _content_hash(text: str) -> str:
    """SHA-1 hex digest of normalised body content."""
    normalised = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha1(normalised.encode("utf-8")).hexdigest()


def _word_set(text: str) -> set[str]:
    """Return lower-cased word tokens from text, stripping YAML frontmatter."""
    # Strip YAML frontmatter (---\n...\n---).
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL)
    # Remove Markdown headers, links, code blocks.
    body = re.sub(r"```.*?```", " ", body, flags=re.DOTALL)
    body = re.sub(r"`[^`]+`", " ", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    body = re.sub(r"[#*_>|]", " ", body)
    words = re.findall(r"\b[a-zA-Z0-9æøåÆØÅ]{3,}\b", body.lower())
    return set(words)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union else 0.0


def _extract_title(text: str, path: Path) -> str:
    """Best-effort title: frontmatter title > first H1 > stem."""
    fm_match = re.search(r"^title:\s*(.+)$", text, re.MULTILINE)
    if fm_match:
        return _normalize_title(fm_match.group(1))
    h1_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if h1_match:
        return _normalize_title(h1_match.group(1))
    return _normalize_title(path.stem)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_duplicates(
    notes_dir: str | Path,
    *,
    jaccard_threshold: float = 0.65,
    title_threshold: float = 0.75,
    include_exact: bool = True,
    glob: str = "**/*.md",
    exclude_dirs: frozenset[str] = frozenset({"08_indices", "09_archive"}),
) -> list[DuplicateFinding]:
    """Scan *notes_dir* for duplicate notes.

    Three detection passes (from cheapest to most expensive):

    1. **Exact hash** — two notes have identical normalised body text.
    2. **Title overlap** — normalised titles share high word-Jaccard.
    3. **Content overlap** — word-bag Jaccard of body text exceeds threshold.

    Args:
        notes_dir: Root directory of ledger notes.
        jaccard_threshold: Minimum Jaccard score to flag as duplicate (content).
        title_threshold: Minimum Jaccard score to flag as title duplicate.
        include_exact: Include exact-content duplicates (always score 1.0).
        glob: Glob pattern for notes files.
        exclude_dirs: Directory names to skip entirely.

    Returns:
        List of DuplicateFinding, sorted by score descending.
    """
    notes_dir = Path(notes_dir).expanduser().resolve()
    if not notes_dir.is_dir():
        return []

    # Collect candidate notes, skipping excluded dirs.
    paths: list[Path] = []
    for p in sorted(notes_dir.glob(glob)):
        if not p.is_file():
            continue
        # Skip if any path component is in exclude_dirs.
        rel_parts = set(p.relative_to(notes_dir).parts[:-1])
        if rel_parts & exclude_dirs:
            continue
        paths.append(p)

    if len(paths) < 2:
        return []

    # Build index: path -> (content, title, hash, word_set).
    index: list[tuple[Path, str, str, str, set[str]]] = []
    for p in paths:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = _extract_title(text, p)
        h = _content_hash(text)
        words = _word_set(text)
        index.append((p, text, title, h, words))

    findings: list[DuplicateFinding] = []
    seen_pairs: set[frozenset[str]] = set()

    def _rel(p: Path) -> str:
        try:
            return "notes/" + str(p.relative_to(notes_dir).as_posix())
        except ValueError:
            return str(p)

    for i in range(len(index)):
        p_a, _, t_a, h_a, w_a = index[i]
        for j in range(i + 1, len(index)):
            p_b, _, t_b, h_b, w_b = index[j]
            pair_key = frozenset({str(p_a), str(p_b)})
            if pair_key in seen_pairs:
                continue

            # Pass 1: exact hash.
            if include_exact and h_a == h_b:
                findings.append(DuplicateFinding(
                    path_a=_rel(p_a),
                    path_b=_rel(p_b),
                    reason="exact_content",
                    score=1.0,
                    details={"hash": h_a},
                ))
                seen_pairs.add(pair_key)
                continue

            # Pass 2: title overlap.
            title_words_a = set(t_a.split())
            title_words_b = set(t_b.split())
            title_j = _jaccard(title_words_a, title_words_b)
            if len(title_words_a) >= 2 and title_j >= title_threshold:
                findings.append(DuplicateFinding(
                    path_a=_rel(p_a),
                    path_b=_rel(p_b),
                    reason="title_overlap",
                    score=round(title_j, 4),
                    details={
                        "title_a": t_a,
                        "title_b": t_b,
                        "title_jaccard": round(title_j, 4),
                    },
                ))
                seen_pairs.add(pair_key)
                continue

            # Pass 3: content word-bag Jaccard.
            if len(w_a) >= 20 and len(w_b) >= 20:
                content_j = _jaccard(w_a, w_b)
                if content_j >= jaccard_threshold:
                    findings.append(DuplicateFinding(
                        path_a=_rel(p_a),
                        path_b=_rel(p_b),
                        reason="content_overlap",
                        score=round(content_j, 4),
                        details={
                            "common_words": len(w_a & w_b),
                            "total_words": len(w_a | w_b),
                            "jaccard": round(content_j, 4),
                        },
                    ))
                    seen_pairs.add(pair_key)

    findings.sort(key=lambda f: f.score, reverse=True)
    return findings

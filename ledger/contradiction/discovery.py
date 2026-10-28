from __future__ import annotations
from ledger.config import get_config
from ledger.layout import NOTE_LAYOUTS
from ledger.parsing.frontmatter import parse_frontmatter_text
from ledger.parsing.privacy import strip_private_tags
from ledger.scoring import canonical_scope
from pathlib import Path
from typing import Any
from .policy import _SCAN_NOTE_TYPES, _is_superseded, _type_compatible
from .state import ContradictionState, _content_hash


def _note_type_from_path(abs_path: Path) -> str | None:
    """Return the ledger note type name for the given path, or None."""
    subdir = abs_path.parent.name
    for name, layout in NOTE_LAYOUTS.items():
        if layout.subdir == subdir:
            return name
    return None


def _get_semantic_neighbors(
    candidate_text: str,
    candidate_rel_path: str,
    candidate_type: str | None,
    k: int,
    ledger_notes_dir: Path,
    candidate_scope: str | None = None,
) -> list[dict[str, Any]]:
    """Return up to k semantic neighbors of the candidate note.

    Uses the existing semantic index (embeddings.semantic_score_map).
    Returns a list of item dicts with at least rel_path, abs_path, scope,
    type, cosine_similarity.

    Only notes from the SAME scope as the candidate are returned (or notes
    with scope "all", which is the cross-scope sentinel).  When
    candidate_scope is None or "all", no scope filtering is applied.

    Returns [] when the semantic index is unavailable (callers handle this).
    """
    try:
        from ledger.embeddings import semantic_score_map
        from ledger.config import get_config
    except ImportError:
        return []

    config = get_config()
    backend = getattr(config, "embed_backend", "local")
    result = semantic_score_map(
        candidate_text,
        target="ledger",
        backend=backend,
        model=None,
    )
    if not result.get("available", False):
        return []

    items = result.get("results", [])
    neighbors: list[dict[str, Any]] = []

    # Normalise candidate scope for comparison.
    cand_scope_norm = canonical_scope(candidate_scope) or None

    for item in items:
        rel_path = str(item.get("rel_path", ""))
        if not rel_path:
            continue
        # Skip self
        if rel_path == candidate_rel_path:
            continue
        # Type compatibility filter
        item_type = item.get("type")
        # item "type" in the embeddings index is the label (e.g. "fact"),
        # not the name (e.g. "facts") — normalise
        item_name = _label_to_name(item_type)
        if not _type_compatible(candidate_type, item_name):
            continue

        # Scope filter: only match notes in the same scope.
        # Scope "all" is a cross-scope sentinel — never filtered out.
        # When the candidate has no scope (or scope "all"), skip filtering.
        if cand_scope_norm and cand_scope_norm != "all":
            item_scope = canonical_scope(item.get("scope", ""))
            if item_scope and item_scope != "all" and item_scope != cand_scope_norm:
                continue

        neighbors.append(item)
        if len(neighbors) >= k:
            break

    return neighbors


_LABEL_TO_NAME: dict[str, str] = {
    layout.label: name for name, layout in NOTE_LAYOUTS.items()
}


def _label_to_name(label: str | None) -> str | None:
    if label is None:
        return None
    return _LABEL_TO_NAME.get(str(label).lower())


def _rel_path_to_abs(rel_path: str, ledger_notes_dir: Path) -> Path | None:
    """Convert a notes/... logical path to an absolute path."""
    if rel_path.startswith("notes/"):
        return ledger_notes_dir / rel_path[len("notes/"):]
    return ledger_notes_dir / rel_path


def collect_candidates(ledger_notes_dir: Path, state: ContradictionState):
    # Collect candidates: notes that are new or have changed since last scan
    candidates: list[tuple[str, Path, str, dict[str, Any], str]] = []
    # (rel_path, abs_path, content_hash, frontmatter_dict, body_text)
    # Live notes per `attribute` slot, changed or not: the partner of a
    # collision is usually an old, unchanged note.
    slots: dict[str, list[tuple[str, str]]] = {}

    for note_type in sorted(_SCAN_NOTE_TYPES):
        layout = NOTE_LAYOUTS.get(note_type)
        if layout is None:
            continue
        folder = ledger_notes_dir / layout.subdir
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name == ".gitkeep":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            h = _content_hash(text)
            rel = f"notes/{layout.subdir}/{path.name}"
            fm, body = parse_frontmatter_text(text)
            body = strip_private_tags(body)
            if _is_superseded(fm):
                # Already superseded notes are skipped as candidates
                continue
            slot = str(fm.get("attribute", "") or "").strip().lower()
            if slot:
                slots.setdefault(slot, []).append((rel, body))
            if state.scanned_hashes.get(rel) == h:
                # Unchanged since last scan — skip
                continue
            candidates.append((rel, path, h, fm, body))

    return candidates, slots

"""Index operations for retrieval."""

from __future__ import annotations

from typing import Any
from pathlib import Path
from ledger.retrieval_types import RetrievalCandidate
import json
from ledger.layout import logical_path
from ledger.layout import note_index_path
from ledger.parsing import parse_frontmatter_text
from ledger.io import safe_write_text
from ledger.scoring import scope_matches
import time
from ledger.ranking.adapters import CandidateLike, _candidate_sort_key, _candidate_value
from ledger.ranking.candidates import _build_archive_candidates, _candidate_from_json, _candidate_from_parts, _candidate_to_json, _note_types, _sha256_text
from ledger.ranking.settings import _cfg, now_utc


NOTE_INDEX_VERSION = 3



_CANDIDATE_CACHE: list[RetrievalCandidate] | None = None



_CANDIDATE_INDEX_CACHE: dict[str, Any] | None = None



def _note_index_path() -> Path:
    return note_index_path(_cfg().ledger_notes_dir)



def _list_note_paths() -> list[tuple[str, Path]]:
    """List all retrieval note paths as (note_type, path)."""
    paths: list[tuple[str, Path]] = []
    for note_type in _cfg().core_note_types:
        note_dir = _note_types()[note_type]["dir"]
        if not note_dir.is_dir():
            continue
        for path in sorted(note_dir.glob("*.md")):
            paths.append((note_type, path))
    return paths



def load_note_index(index_path: Path | str | None = None) -> dict[str, Any]:
    """Load persistent note index from disk."""
    path = Path(index_path) if index_path is not None else _note_index_path()
    empty = {"version": NOTE_INDEX_VERSION, "built": "", "entries": {}, "inverted": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty
    except json.JSONDecodeError:
        return empty

    if not isinstance(raw, dict):
        return empty
    if int(raw.get("version", 0)) != NOTE_INDEX_VERSION:
        return empty
    entries = raw.get("entries", {})
    if not isinstance(entries, dict):
        entries = {}
    inverted = raw.get("inverted", {})
    if not isinstance(inverted, dict):
        inverted = {}
    return {
        "version": NOTE_INDEX_VERSION,
        "built": str(raw.get("built", "")),
        "entries": entries,
        "inverted": inverted,
    }



def rebuild_note_index(index_path: Path | str | None = None) -> dict[str, Any]:
    """Incrementally rebuild persistent note metadata index."""
    started = time.perf_counter_ns()
    path = Path(index_path) if index_path is not None else _note_index_path()
    existing = load_note_index(path)
    existing_entries: dict[str, dict[str, Any]] = existing.get("entries", {})
    updated_entries: dict[str, dict[str, Any]] = {}

    for note_type, note_path in _list_note_paths():
        rel = logical_path(
            note_path,
            ledger_root=_cfg().ledger_root,
            ledger_notes_dir=_cfg().ledger_notes_dir,
        ).as_posix()
        mtime = note_path.stat().st_mtime
        cached = existing_entries.get(rel, {})
        cached_mtime = float(cached.get("mtime", -1.0)) if cached else -1.0

        if cached and cached_mtime == mtime and isinstance(cached.get("candidate"), dict):
            updated_entries[rel] = cached
            continue

        text = note_path.read_text(encoding="utf-8")
        content_hash = _sha256_text(text)
        if (
            cached
            and isinstance(cached.get("candidate"), dict)
            and str(cached.get("content_hash", "")) == content_hash
        ):
            reused = dict(cached)
            reused["mtime"] = mtime
            updated_entries[rel] = reused
            continue

        frontmatter, body = parse_frontmatter_text(text)
        candidate = _candidate_from_parts(note_path, note_type, frontmatter, body)
        updated_entries[rel] = {
            "mtime": mtime,
            "content_hash": content_hash,
            "note_type": note_type,
            "candidate": _candidate_to_json(candidate),
        }

    inverted: dict[str, list[str]] = {}
    for rel, entry in updated_entries.items():
        candidate_json = entry.get("candidate", {})
        if not isinstance(candidate_json, dict):
            continue
        tokens = (
            set(candidate_json.get("note_tokens", []))
            | set(candidate_json.get("tag_tokens", []))
            | set(candidate_json.get("attention_tokens", []))
        )
        for token in tokens:
            inverted.setdefault(str(token), []).append(rel)

    for token in list(inverted.keys()):
        inverted[token] = sorted(set(inverted[token]))

    unchanged = (
        updated_entries == existing_entries
        and inverted == existing.get("inverted", {})
        and path.is_file()
    )
    if unchanged:
        payload = dict(existing)
        payload["build_ms"] = (time.perf_counter_ns() - started) / 1_000_000.0
        return payload

    payload = {
        "version": NOTE_INDEX_VERSION,
        "built": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": updated_entries,
        "inverted": inverted,
        "build_ms": (time.perf_counter_ns() - started) / 1_000_000.0,
    }
    safe_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return payload



def build_candidates(use_cache: bool = False) -> list[RetrievalCandidate]:
    """Build retrieval candidates from all core note types."""
    global _CANDIDATE_CACHE
    if use_cache and _CANDIDATE_CACHE is not None:
        return _CANDIDATE_CACHE

    index = rebuild_note_index()
    entries = index.get("entries", {})
    candidates: list[RetrievalCandidate] = []
    for rel_path in sorted(entries.keys()):
        entry = entries.get(rel_path, {})
        candidate_json = entry.get("candidate", {})
        if isinstance(candidate_json, dict):
            candidates.append(_candidate_from_json(candidate_json))

    if use_cache:
        _CANDIDATE_CACHE = candidates
        return _CANDIDATE_CACHE
    return candidates



def clear_candidate_cache() -> None:
    """Clear process-local candidate and candidate-index caches."""
    global _CANDIDATE_CACHE, _CANDIDATE_INDEX_CACHE
    _CANDIDATE_CACHE = None
    _CANDIDATE_INDEX_CACHE = None



def build_candidates_with_archive() -> list[RetrievalCandidate]:
    """Return all core candidates PLUS archive notes for --as-of queries.

    This is the widened candidate pool used when a temporal filter is requested.
    The result is NOT cached (archive candidates are only needed for as-of queries).
    """
    return build_candidates(use_cache=False) + _build_archive_candidates()



def candidate_index_tokens(candidate: CandidateLike) -> set[str]:
    """Return all tokens that should index a candidate."""
    return (
        set(_candidate_value(candidate, "note_tokens", set()) or set())
        | set(_candidate_value(candidate, "tag_tokens", set()) or set())
        | set(_candidate_value(candidate, "attention_tokens", set()) or set())
    )



def build_candidate_index(use_cache: bool = False) -> dict[str, Any]:
    """Build token -> candidate-id inverted index for fast preselection."""
    global _CANDIDATE_INDEX_CACHE
    if use_cache and _CANDIDATE_INDEX_CACHE is not None:
        return _CANDIDATE_INDEX_CACHE

    note_index = rebuild_note_index()
    entries = note_index.get("entries", {})
    rel_paths = sorted(entries.keys())
    candidates: list[RetrievalCandidate] = []
    rel_to_idx: dict[str, int] = {}
    for rel in rel_paths:
        entry = entries.get(rel, {})
        candidate_json = entry.get("candidate", {})
        if not isinstance(candidate_json, dict):
            continue
        rel_to_idx[rel] = len(candidates)
        candidates.append(_candidate_from_json(candidate_json))

    token_index: dict[str, list[int]] = {}
    inverted = note_index.get("inverted", {})
    if isinstance(inverted, dict) and inverted:
        for token, rel_list in inverted.items():
            if not isinstance(rel_list, list):
                continue
            ids = [rel_to_idx[rel] for rel in rel_list if rel in rel_to_idx]
            if ids:
                token_index[str(token)] = ids
    else:
        for idx, candidate in enumerate(candidates):
            for token in candidate_index_tokens(candidate):
                token_index.setdefault(token, []).append(idx)

    index = {"candidates": candidates, "token_index": token_index}
    if use_cache:
        _CANDIDATE_INDEX_CACHE = index
        return _CANDIDATE_INDEX_CACHE
    return index



def retrieve_candidates_from_index(
    index: dict[str, Any],
    query_tokens: set[str],
    query_scope: str,
    minimum_pool: int | None = None,
) -> list[CandidateLike]:
    """Retrieve candidate subset from token index while preserving fallback pool size."""
    minimum_pool = minimum_pool if minimum_pool is not None else _cfg().shortlist_min_candidates
    candidates = index.get("candidates", [])
    if not candidates:
        return []
    if not query_tokens and query_scope == "all":
        return candidates

    matched_ids: set[int] = set()
    token_index: dict[str, list[int]] = index.get("token_index", {})
    for token in query_tokens:
        matched_ids.update(token_index.get(token, []))

    if query_scope != "all":
        for idx, candidate in enumerate(candidates):
            if scope_matches(_candidate_value(candidate, "scope", ""), query_scope):
                matched_ids.add(idx)

    if not matched_ids:
        return candidates

    ordered_ids = sorted(matched_ids, key=lambda idx: _candidate_sort_key(candidates[idx]), reverse=True)
    selected = [candidates[idx] for idx in ordered_ids]
    if len(selected) >= min(len(candidates), minimum_pool):
        return selected

    seen_ids = set(ordered_ids)
    remainder = [idx for idx in range(len(candidates)) if idx not in seen_ids]
    remainder.sort(key=lambda idx: _candidate_sort_key(candidates[idx]), reverse=True)
    for idx in remainder:
        selected.append(candidates[idx])
        if len(selected) >= min(len(candidates), minimum_pool):
            break

    return selected


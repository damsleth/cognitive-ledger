"""Core retrieval utilities for Cognitive Ledger.

This module provides the lexical retrieval pipeline:
- Candidate construction
- Candidate indexing
- Multi-stage shortlist/prefilter
- Final ranking and scoring

All lexical retrieval behavior should live here so CLI/TUI/tests can reuse it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import heapq
import json
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.io import safe_write_text
from ledger.parsing import (
    parse_frontmatter_text,
    parse_sections,
    extract_title,
    first_content_line,
    first_checkbox,
    tokenize,
    extract_link_tokens,
)

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - optional dependency fallback
    BM25Okapi = None


# ---------------------------------------------------------------------------
# Config-derived constants
# ---------------------------------------------------------------------------

_config = get_config()
ROOT_DIR = _config.root_dir
ALIASES_PATH = _config.aliases_path
NOTE_INDEX_PATH = _config.notes_dir / "08_indices" / "note_index.json"
NOTE_INDEX_VERSION = 2

NOTE_TYPES = {
    name: {"dir": ROOT_DIR / info["dir"], "label": info["label"]}
    for name, info in _config.note_types.items()
}
CORE_NOTE_TYPES = _config.core_note_types

SHORTLIST_MIN_CANDIDATES = _config.shortlist_min_candidates
SHORTLIST_MAX_CANDIDATES = _config.shortlist_max_candidates
SHORTLIST_LIMIT_MULTIPLIER = _config.shortlist_limit_multiplier
ATTENTION_SHORTLIST_MIN_CANDIDATES = _config.attention_shortlist_min
ATTENTION_SHORTLIST_MAX_CANDIDATES = _config.attention_shortlist_max
ATTENTION_SHORTLIST_LIMIT_MULTIPLIER = _config.attention_shortlist_limit_multiplier
DETAILED_REASONS_LIMIT = _config.detailed_reasons_limit
PROGRESSIVE_RATIONALE_TOP = _config.progressive_rationale_top


# Intent detection hints
HISTORY_HINTS = frozenset({"history", "closed", "past"})
PREFERENCE_HINTS = frozenset({"preference", "preferences", "style", "workflow", "habit", "habits"})
LOOP_HINTS = frozenset({"loop", "loops", "unresolved", "next", "pending", "todo", "task", "tasks", "do"})


_CANDIDATE_CACHE: list[dict[str, Any]] | None = None
_CANDIDATE_INDEX_CACHE: dict[str, Any] | None = None


def now_utc() -> dt.datetime:
    """Get current UTC datetime."""
    return dt.datetime.now(tz=dt.timezone.utc)


def parse_ts(value: str) -> dt.datetime | None:
    """Parse ISO 8601 timestamp to datetime."""
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def canonical_scope(scope: str | None) -> str:
    """Normalize scope value."""
    lowered = str(scope or "").strip().lower()
    if lowered == "life":
        return "personal"
    return lowered


def scope_matches(note_scope: str, query_scope: str) -> bool:
    """Check if note scope matches query scope."""
    if query_scope == "all":
        return True
    return canonical_scope(note_scope) == query_scope


def resolve_retrieval_mode(retrieval_mode: str | None) -> str:
    """Resolve retrieval mode from argument or environment."""
    import os

    config = get_config()
    if retrieval_mode is None:
        retrieval_mode = os.getenv("LEDGER_RETRIEVAL_MODE", "legacy")
    mode = str(retrieval_mode or "").strip().lower() or "legacy"
    if mode not in config.retrieval_modes:
        return "legacy"
    return mode


def resolve_embed_backend(embed_backend: str | None) -> str:
    """Resolve embedding backend from argument or environment."""
    import os

    config = get_config()
    if embed_backend is None:
        embed_backend = os.getenv("LEDGER_EMBED_BACKEND", "local")
    backend = str(embed_backend or "").strip().lower() or "local"
    if backend not in config.embed_backends:
        return "local"
    return backend


def confidence_value(frontmatter: dict[str, Any]) -> float:
    """Extract and clamp confidence value from frontmatter."""
    try:
        value = float(frontmatter.get("confidence", 0))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def compute_recency_component(updated_ts: dt.datetime | None, now_dt: dt.datetime) -> float:
    """Compute recency score (0.0-1.0) based on age.

    Decays linearly over 90 days.
    """
    if not updated_ts:
        return 0.0
    age_days = max(0.0, (now_dt - updated_ts).total_seconds() / 86400.0)
    return max(0.0, 1.0 - (age_days / 90.0))


def load_aliases(path: Path) -> dict[str, list[str]]:
    """Load query aliases from JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}

    aliases: dict[str, list[str]] = {}
    if isinstance(data, dict):
        for key, values in data.items():
            if not isinstance(values, list):
                continue
            k = str(key).strip().lower()
            if not k:
                continue
            aliases[k] = [str(v).strip() for v in values if str(v).strip()]
    return aliases


def expand_query_tokens(
    query_tokens: set[str],
    aliases: dict[str, list[str]],
) -> tuple[set[str], list[dict[str, Any]]]:
    """Expand query tokens using aliases.

    Returns:
        Tuple of (expanded_tokens, expansion_events)
    """
    expanded = set(query_tokens)
    expansion_events = []

    for token in sorted(query_tokens):
        for phrase in aliases.get(token, []):
            phrase_tokens = tokenize(phrase)
            newly_added = sorted(phrase_tokens - expanded)
            if newly_added:
                expansion_events.append(
                    {
                        "alias": token,
                        "phrase": phrase,
                        "tokens": newly_added,
                    }
                )
            expanded |= phrase_tokens

    return expanded, expansion_events


def read_note_for_retrieval(path: Path) -> tuple[dict[str, Any], str]:
    """Read a note file for retrieval.

    Returns:
        Tuple of (frontmatter_dict, body).
    """
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter_text(text)


def _sha256_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def build_attention_tokens(
    title: str,
    statement: str,
    snippet_source: str,
    tags: list[str],
    scope: str,
    status: str,
    next_action: str,
    body: str,
    slug: str,
) -> set[str]:
    """Build a richer token capsule used by compressed_attention retrieval mode."""
    capsule_parts = [
        title,
        statement,
        snippet_source,
        " ".join(tags),
        scope,
        status,
        next_action,
        slug,
    ]
    attention_tokens = tokenize(" ".join(part for part in capsule_parts if part))
    attention_tokens |= extract_link_tokens(body)
    return attention_tokens


def _candidate_from_parts(path: Path, note_type: str, frontmatter: dict[str, Any], body: str) -> dict[str, Any]:
    """Build retrieval candidate dictionary from parsed note content."""
    sections = parse_sections(body)
    title = extract_title(body) or path.stem.replace("_", " ")

    if note_type == "loops":
        statement = "\n".join(sections.get("question or task", [])).strip()
        if not statement:
            statement = first_content_line(body)
        next_action_text = "\n".join(sections.get("next action", [])).strip()
        if not next_action_text:
            next_action_text = body
        next_action = first_checkbox(next_action_text)
        status = str(frontmatter.get("status", "open")).strip().lower() or "open"
    else:
        statement = "\n".join(sections.get("statement", [])).strip() or first_content_line(body)
        next_action = ""
        status = ""

    tags_raw = frontmatter.get("tags", [])
    if isinstance(tags_raw, list):
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    elif isinstance(tags_raw, str) and tags_raw.strip():
        tags = [tags_raw.strip()]
    else:
        tags = []

    scope = canonical_scope(frontmatter.get("scope", ""))
    updated_str = str(frontmatter.get("updated", "")).strip()
    updated_ts = parse_ts(updated_str)
    confidence = confidence_value(frontmatter)
    source = str(frontmatter.get("source", "")).strip().lower()

    rel_path = path.resolve().relative_to(ROOT_DIR)
    slug = path.stem

    searchable_text = " ".join(
        [
            title,
            statement,
            body,
            " ".join(tags),
            slug,
        ]
    )
    note_tokens = tokenize(searchable_text)

    tag_tokens = set()
    for tag in tags:
        tag_tokens |= tokenize(tag.replace("-", " ").replace("_", " "))

    snippet_source = statement or first_content_line(body)
    if note_type == "loops" and next_action:
        snippet_source = f"{statement} Next: {next_action}".strip()
    attention_tokens = build_attention_tokens(
        title=title,
        statement=statement,
        snippet_source=snippet_source,
        tags=tags,
        scope=scope,
        status=status,
        next_action=next_action,
        body=body,
        slug=slug,
    )

    return {
        "path": str(path.resolve()),
        "rel_path": str(rel_path),
        "type": NOTE_TYPES[note_type]["label"],
        "title": title,
        "statement": statement,
        "body": body,
        "updated": updated_str,
        "updated_ts": updated_ts,
        "confidence": confidence,
        "source": source,
        "scope": scope,
        "status": status,
        "tags": tags,
        "note_tokens": note_tokens,
        "tag_tokens": tag_tokens,
        "attention_tokens": attention_tokens,
        "snippet": snippet_source,
        "has_next_action_checkbox": bool(next_action),
    }


def candidate_from_note(path: Path, note_type: str) -> dict[str, Any]:
    """Build retrieval candidate dictionary from a note path."""
    frontmatter, body = read_note_for_retrieval(path)
    return _candidate_from_parts(path, note_type, frontmatter, body)


def _candidate_to_json(candidate: dict[str, Any]) -> dict[str, Any]:
    """Serialize candidate to JSON-friendly payload."""
    data = dict(candidate)
    data.pop("updated_ts", None)
    data["note_tokens"] = sorted(candidate.get("note_tokens", set()))
    data["tag_tokens"] = sorted(candidate.get("tag_tokens", set()))
    data["attention_tokens"] = sorted(candidate.get("attention_tokens", set()))
    return data


def _candidate_from_json(candidate_json: dict[str, Any]) -> dict[str, Any]:
    """Deserialize candidate from JSON-friendly payload."""
    data = dict(candidate_json)
    data["note_tokens"] = set(candidate_json.get("note_tokens", []))
    data["tag_tokens"] = set(candidate_json.get("tag_tokens", []))
    data["attention_tokens"] = set(candidate_json.get("attention_tokens", []))
    data["updated_ts"] = parse_ts(str(candidate_json.get("updated", "")))
    return data


def _list_note_paths() -> list[tuple[str, Path]]:
    """List all retrieval note paths as (note_type, path)."""
    paths: list[tuple[str, Path]] = []
    for note_type in CORE_NOTE_TYPES:
        note_dir = NOTE_TYPES[note_type]["dir"]
        if not note_dir.is_dir():
            continue
        for path in sorted(note_dir.glob("*.md")):
            paths.append((note_type, path))
    return paths


def load_note_index(index_path: Path | str = NOTE_INDEX_PATH) -> dict[str, Any]:
    """Load persistent note index from disk."""
    path = Path(index_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": NOTE_INDEX_VERSION, "built": "", "entries": {}, "inverted": {}}
    except json.JSONDecodeError:
        return {"version": NOTE_INDEX_VERSION, "built": "", "entries": {}, "inverted": {}}

    if not isinstance(raw, dict):
        return {"version": NOTE_INDEX_VERSION, "built": "", "entries": {}, "inverted": {}}
    if int(raw.get("version", 0)) != NOTE_INDEX_VERSION:
        return {"version": NOTE_INDEX_VERSION, "built": "", "entries": {}, "inverted": {}}
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


def rebuild_note_index(index_path: Path | str = NOTE_INDEX_PATH) -> dict[str, Any]:
    """Incrementally rebuild persistent note metadata index."""
    path = Path(index_path)
    existing = load_note_index(path)
    existing_entries: dict[str, dict[str, Any]] = existing.get("entries", {})
    updated_entries: dict[str, dict[str, Any]] = {}

    for note_type, note_path in _list_note_paths():
        rel = note_path.resolve().relative_to(ROOT_DIR).as_posix()
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
        return existing

    payload = {
        "version": NOTE_INDEX_VERSION,
        "built": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": updated_entries,
        "inverted": inverted,
    }
    safe_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return payload


def build_candidates(use_cache: bool = False) -> list[dict[str, Any]]:
    """Build retrieval candidates from all core note types."""
    global _CANDIDATE_CACHE
    if use_cache and _CANDIDATE_CACHE is not None:
        return _CANDIDATE_CACHE

    index = rebuild_note_index(NOTE_INDEX_PATH)
    entries = index.get("entries", {})
    candidates = []
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


def candidate_index_tokens(candidate: dict[str, Any]) -> set[str]:
    """Return all tokens that should index a candidate."""
    return (
        set(candidate.get("note_tokens", set()))
        | set(candidate.get("tag_tokens", set()))
        | set(candidate.get("attention_tokens", set()))
    )


def build_candidate_index(use_cache: bool = False) -> dict[str, Any]:
    """Build token -> candidate-id inverted index for fast preselection."""
    global _CANDIDATE_INDEX_CACHE
    if use_cache and _CANDIDATE_INDEX_CACHE is not None:
        return _CANDIDATE_INDEX_CACHE

    note_index = rebuild_note_index(NOTE_INDEX_PATH)
    entries = note_index.get("entries", {})
    rel_paths = sorted(entries.keys())
    candidates = []
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
    minimum_pool: int = SHORTLIST_MIN_CANDIDATES,
) -> list[dict[str, Any]]:
    """Retrieve candidate subset from token index while preserving fallback pool size."""
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
            if scope_matches(candidate.get("scope", ""), query_scope):
                matched_ids.add(idx)

    if not matched_ids:
        return candidates

    ordered_ids = sorted(
        matched_ids,
        key=lambda idx: (
            candidates[idx].get("updated") or "",
            candidates[idx].get("path", ""),
        ),
        reverse=True,
    )
    selected = [candidates[idx] for idx in ordered_ids]
    if len(selected) >= min(len(candidates), minimum_pool):
        return selected

    seen_ids = set(ordered_ids)
    remainder = [idx for idx in range(len(candidates)) if idx not in seen_ids]
    remainder.sort(
        key=lambda idx: (
            candidates[idx].get("updated") or "",
            candidates[idx].get("path", ""),
        ),
        reverse=True,
    )
    for idx in remainder:
        selected.append(candidates[idx])
        if len(selected) >= min(len(candidates), minimum_pool):
            break

    return selected


def coarse_candidate_score(
    candidate: dict[str, Any],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
) -> tuple[float, dict[str, float | int]]:
    """Cheap first-pass score used to shortlist candidates."""
    if query_tokens:
        lexical_overlap_count = len(candidate["note_tokens"] & query_tokens)
        lexical_match = lexical_overlap_count / len(query_tokens)
        tag_overlap_count = len(candidate["tag_tokens"] & query_tokens)
        tag_overlap = tag_overlap_count / len(query_tokens)
    else:
        lexical_overlap_count = 0
        lexical_match = 0.0
        tag_overlap_count = 0
        tag_overlap = 0.0

    scope_match = 1.0 if scope_matches(candidate["scope"], query_scope) else 0.0
    score = (0.70 * lexical_match) + (0.20 * tag_overlap) + (0.10 * scope_match)

    if query_scope != "all":
        if scope_match >= 1.0:
            score += 0.03
        else:
            score -= 0.03
    if candidate["type"] == "loop" and candidate.get("status") == "closed" and not history_mode:
        score -= 0.05
    if history_mode and candidate["type"] == "loop" and candidate.get("status") == "closed":
        score += 0.05
    if loop_mode and candidate["type"] == "loop" and candidate.get("status") == "open":
        score += 0.04
    if preference_mode and candidate["type"] == "pref":
        score += 0.04

    return score, {
        "lexical_overlap_count": lexical_overlap_count,
        "tag_overlap_count": tag_overlap_count,
        "scope_match": scope_match,
    }


def shortlist_candidates(
    candidates: list[dict[str, Any]],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    limit: int,
) -> list[dict[str, Any]]:
    """Shortlist candidate set using coarse score."""
    if not candidates:
        return []

    shortlist_target = min(
        len(candidates),
        max(
            SHORTLIST_MIN_CANDIDATES,
            min(SHORTLIST_MAX_CANDIDATES, max(1, limit) * SHORTLIST_LIMIT_MULTIPLIER),
        ),
    )

    scored = []
    for candidate in candidates:
        coarse_score, coarse_components = coarse_candidate_score(
            candidate,
            query_tokens,
            query_scope,
            history_mode,
            loop_mode,
            preference_mode,
        )

        if (
            query_scope == "all"
            and coarse_components["lexical_overlap_count"] == 0
            and coarse_components["tag_overlap_count"] == 0
        ):
            continue
        if query_scope == "all" and coarse_score <= 0:
            continue

        scored.append((coarse_score, candidate))

    top_scored = heapq.nlargest(
        shortlist_target,
        scored,
        key=lambda item: (
            item[0],
            item[1]["updated"] or "",
            item[1]["path"],
        ),
    )
    return [item[1] for item in top_scored]


def compressed_attention_candidate_score(
    candidate: dict[str, Any],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
) -> tuple[float, dict[str, float | int]]:
    """Cheap score variant that includes attention-token overlap."""
    if query_tokens:
        attention_overlap_count = len(candidate.get("attention_tokens", set()) & query_tokens)
        attention_overlap = attention_overlap_count / len(query_tokens)
        lexical_overlap_count = len(candidate["note_tokens"] & query_tokens)
        lexical_match = lexical_overlap_count / len(query_tokens)
        tag_overlap_count = len(candidate["tag_tokens"] & query_tokens)
        tag_overlap = tag_overlap_count / len(query_tokens)
    else:
        attention_overlap_count = 0
        attention_overlap = 0.0
        lexical_overlap_count = 0
        lexical_match = 0.0
        tag_overlap_count = 0
        tag_overlap = 0.0

    scope_match = 1.0 if scope_matches(candidate["scope"], query_scope) else 0.0
    score = (0.55 * attention_overlap) + (0.20 * lexical_match) + (0.15 * tag_overlap) + (0.10 * scope_match)

    if query_scope != "all":
        if scope_match >= 1.0:
            score += 0.04
        else:
            score -= 0.04
    if candidate["type"] == "loop" and candidate.get("status") == "closed" and not history_mode:
        score -= 0.05
    if history_mode and candidate["type"] == "loop" and candidate.get("status") == "closed":
        score += 0.06
    if loop_mode and candidate["type"] == "loop" and candidate.get("status") == "open":
        score += 0.05
    if preference_mode and candidate["type"] == "pref":
        score += 0.05

    return score, {
        "attention_overlap_count": attention_overlap_count,
        "lexical_overlap_count": lexical_overlap_count,
        "tag_overlap_count": tag_overlap_count,
        "scope_match": scope_match,
    }


def shortlist_attention_candidates(
    candidates: list[dict[str, Any]],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    limit: int,
) -> list[dict[str, Any]]:
    """Shortlist candidate set using compressed-attention coarse score."""
    if not candidates:
        return []

    shortlist_target = min(
        len(candidates),
        max(
            ATTENTION_SHORTLIST_MIN_CANDIDATES,
            min(
                ATTENTION_SHORTLIST_MAX_CANDIDATES,
                max(1, limit) * ATTENTION_SHORTLIST_LIMIT_MULTIPLIER,
            ),
        ),
    )

    scored = []
    for candidate in candidates:
        coarse_score, coarse_components = compressed_attention_candidate_score(
            candidate,
            query_tokens,
            query_scope,
            history_mode,
            loop_mode,
            preference_mode,
        )

        if (
            query_scope == "all"
            and coarse_components["attention_overlap_count"] == 0
            and coarse_components["lexical_overlap_count"] == 0
            and coarse_components["tag_overlap_count"] == 0
        ):
            continue
        if query_scope == "all" and coarse_score <= 0:
            continue

        scored.append((coarse_score, candidate))

    top_scored = heapq.nlargest(
        shortlist_target,
        scored,
        key=lambda item: (
            item[0],
            item[1]["updated"] or "",
            item[1]["path"],
        ),
    )
    return [item[1] for item in top_scored]


def has_token_overlap(candidate: dict[str, Any], query_tokens: set[str]) -> bool:
    """Whether candidate has overlap with note/tag tokens."""
    if not query_tokens:
        return False
    return bool((candidate["note_tokens"] & query_tokens) or (candidate["tag_tokens"] & query_tokens))


def compute_bm25_scores(
    candidates: list[dict[str, Any]],
    query_tokens: set[str],
) -> dict[str, float]:
    """Compute normalized BM25 scores (0.0-1.0) keyed by candidate path."""
    if BM25Okapi is None or not candidates or not query_tokens:
        return {}

    corpus = [list(candidate.get("note_tokens", set())) for candidate in candidates]
    if not any(corpus):
        return {}

    bm25 = BM25Okapi(corpus)
    raw_scores = bm25.get_scores(list(query_tokens))
    if len(raw_scores) != len(candidates):
        return {}

    max_score = max(float(score) for score in raw_scores) if len(raw_scores) else 0.0
    if max_score <= 0:
        return {}

    return {
        candidate["path"]: max(0.0, float(score) / max_score)
        for candidate, score in zip(candidates, raw_scores, strict=False)
    }


def prefilter_candidates_by_scope_and_type(
    candidates: list[dict[str, Any]],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    limit: int,
) -> list[dict[str, Any]]:
    """Filter obvious noise before shortlist/final scoring."""
    if not candidates:
        return []

    filtered = []
    for candidate in candidates:
        token_overlap = has_token_overlap(candidate, query_tokens)

        if query_scope != "all" and not scope_matches(candidate["scope"], query_scope) and not token_overlap:
            continue
        if preference_mode and candidate["type"] not in {"pref", "concept"} and not token_overlap:
            continue
        if loop_mode and candidate["type"] not in {"loop", "goal"} and not token_overlap:
            continue
        if history_mode and candidate["type"] not in {"loop", "fact", "concept"} and not token_overlap:
            continue

        filtered.append(candidate)

    if not filtered:
        return candidates

    if query_scope == "all" and (history_mode or loop_mode or preference_mode):
        return filtered

    minimum_pool = min(len(candidates), max(SHORTLIST_MIN_CANDIDATES, max(1, limit) * 2))
    if len(filtered) >= minimum_pool:
        return filtered

    seen_paths = {item["path"] for item in filtered}
    remainder = [item for item in candidates if item["path"] not in seen_paths]
    remainder.sort(key=lambda item: (item["updated"] or "", item["path"]), reverse=True)
    for candidate in remainder:
        filtered.append(candidate)
        if len(filtered) >= minimum_pool:
            break

    return filtered


def score_candidate(
    candidate: dict[str, Any],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    now_dt: dt.datetime,
    expansion_events: list[dict[str, Any]],
    include_reasons: bool = True,
    bm25_score: float = 0.0,
) -> tuple[float, list[str], dict[str, float]]:
    """Final score for candidate in lexical retrieval modes."""
    if query_tokens:
        lexical_overlap_count = len(candidate["note_tokens"] & query_tokens)
        lexical_match = lexical_overlap_count / len(query_tokens)
        tag_overlap_count = len(candidate["tag_tokens"] & query_tokens)
        tag_overlap = tag_overlap_count / len(query_tokens)
    else:
        lexical_overlap_count = 0
        lexical_match = 0.0
        tag_overlap_count = 0
        tag_overlap = 0.0

    scope_match = 1.0 if scope_matches(candidate["scope"], query_scope) else 0.0
    recency = compute_recency_component(candidate["updated_ts"], now_dt)
    confidence = candidate["confidence"]

    score = (
        (0.30 * bm25_score)
        + (0.15 * lexical_match)
        + (0.15 * tag_overlap)
        + (0.15 * scope_match)
        + (0.15 * recency)
        + (0.10 * confidence)
    )

    reasons = []
    if include_reasons and bm25_score > 0:
        reasons.append(f"bm25={bm25_score:.3f}")
    if include_reasons and lexical_overlap_count > 0:
        reasons.append(f"lexical_overlap={lexical_overlap_count}")
    if include_reasons and tag_overlap_count > 0:
        reasons.append(f"tag_overlap={tag_overlap_count}")
    if query_scope != "all":
        if scope_match >= 1.0:
            score += 0.05
            if include_reasons:
                reasons.append("scope_boost")
        else:
            score -= 0.05
            if include_reasons:
                reasons.append("scope_demote")
    if candidate["type"] == "loop" and candidate.get("status") == "closed" and not history_mode:
        score -= 0.20
        if include_reasons:
            reasons.append("closed_loop_penalty")
    if history_mode and candidate["type"] == "loop":
        if candidate.get("status") == "closed":
            score += 0.12
            if include_reasons:
                reasons.append("history_closed_loop_boost")
        elif candidate.get("status") == "open":
            score -= 0.05
            if include_reasons:
                reasons.append("history_open_loop_demote")
    if loop_mode and candidate["type"] == "loop" and candidate.get("status") == "open":
        score += 0.07
        if include_reasons:
            reasons.append("open_loop_intent_boost")
    if preference_mode and candidate["type"] == "pref":
        score += 0.07
        if include_reasons:
            reasons.append("preference_intent_boost")

    if include_reasons and expansion_events:
        alias_summary = ", ".join(sorted({f"{e['alias']}->{e['phrase']}" for e in expansion_events}))
        reasons.append(f"alias_expansion={alias_summary}")

    if include_reasons and recency > 0:
        reasons.append(f"recency={recency:.2f}")
    if include_reasons:
        reasons.append(f"confidence={confidence:.2f}")

    score = max(0.0, min(1.0, score))
    return score, reasons, {
        "bm25_score": bm25_score,
        "lexical_match": lexical_match,
        "tag_overlap": tag_overlap,
        "scope_match": scope_match,
        "recency": recency,
        "confidence": confidence,
    }


def apply_progressive_disclosure(
    ranked_results: list[dict[str, Any]],
    limit: int,
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    now_dt: dt.datetime,
    expansion_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach full rationales to top-N items and compact output for remainder."""
    output = ranked_results[:limit]
    top_n = min(PROGRESSIVE_RATIONALE_TOP, len(output))
    for idx, item in enumerate(output):
        if idx < top_n:
            _, reasons, _ = score_candidate(
                item,
                query_tokens,
                query_scope,
                history_mode,
                loop_mode,
                preference_mode,
                now_dt,
                expansion_events,
                include_reasons=True,
            )
            item["reasons"] = reasons
            item["disclosure_level"] = "rationale"
        else:
            item["reasons"] = []
            item["disclosure_level"] = "compact"
    return output


def rank_lexical(
    query: str,
    scope: str = "all",
    limit: int = 8,
    aliases_path: Path | str = ALIASES_PATH,
    now_dt: dt.datetime | None = None,
    retrieval_mode: str = "legacy",
) -> dict[str, Any]:
    """Rank notes using lexical retrieval modes."""
    now_dt = now_dt or now_utc()
    mode = resolve_retrieval_mode(retrieval_mode)
    two_stage_active = mode in {"legacy", "two_stage"}
    compressed_attention_active = mode in {"legacy", "compressed_attention"}
    scope_prefilter_active = mode in {"legacy", "scope_type_prefilter"}
    precomputed_index_active = mode in {"legacy", "precomputed_index"}
    progressive_disclosure_active = mode in {"legacy", "progressive_disclosure"}
    aliases = load_aliases(Path(aliases_path))
    query_tokens = tokenize(query)
    expanded_tokens, expansion_events = expand_query_tokens(query_tokens, aliases)

    history_mode = any(token in HISTORY_HINTS for token in query_tokens)
    loop_mode = any(token in LOOP_HINTS for token in query_tokens)
    preference_mode = any(token in PREFERENCE_HINTS for token in query_tokens)
    include_reasons = True if mode == "legacy" else (limit <= DETAILED_REASONS_LIMIT)
    if progressive_disclosure_active:
        include_reasons = False

    index_pool_size = None
    if precomputed_index_active:
        index = build_candidate_index(use_cache=True)
        candidates = index["candidates"]
        prefiltered_candidates = retrieve_candidates_from_index(
            index,
            expanded_tokens,
            scope,
            minimum_pool=max(SHORTLIST_MIN_CANDIDATES, max(1, limit) * 2),
        )
        index_pool_size = len(prefiltered_candidates)
    else:
        candidates = build_candidates(
            use_cache=(
                mode
                in {
                    "two_stage",
                    "compressed_attention",
                    "progressive_disclosure",
                }
            )
            or two_stage_active
        )
        prefiltered_candidates = candidates

    if scope_prefilter_active:
        prefiltered_candidates = prefilter_candidates_by_scope_and_type(
            candidates,
            expanded_tokens,
            scope,
            history_mode,
            loop_mode,
            preference_mode,
            limit=limit,
        )

    if compressed_attention_active and limit <= ATTENTION_SHORTLIST_MAX_CANDIDATES:
        shortlisted = shortlist_attention_candidates(
            prefiltered_candidates,
            expanded_tokens,
            scope,
            history_mode,
            loop_mode,
            preference_mode,
            limit=limit,
        )
    elif two_stage_active and limit <= SHORTLIST_MAX_CANDIDATES:
        shortlisted = shortlist_candidates(
            prefiltered_candidates,
            expanded_tokens,
            scope,
            history_mode,
            loop_mode,
            preference_mode,
            limit=limit,
        )
    elif precomputed_index_active and limit <= SHORTLIST_MAX_CANDIDATES:
        shortlisted = shortlist_candidates(
            prefiltered_candidates,
            expanded_tokens,
            scope,
            history_mode,
            loop_mode,
            preference_mode,
            limit=limit,
        )
    elif precomputed_index_active:
        shortlisted = prefiltered_candidates
    elif scope_prefilter_active and limit <= SHORTLIST_MAX_CANDIDATES:
        shortlisted = shortlist_candidates(
            prefiltered_candidates,
            expanded_tokens,
            scope,
            history_mode,
            loop_mode,
            preference_mode,
            limit=limit,
        )
    elif scope_prefilter_active:
        shortlisted = prefiltered_candidates
    else:
        shortlisted = candidates

    ranked = []
    bm25_scores = compute_bm25_scores(shortlisted, expanded_tokens)
    for candidate in shortlisted:
        score, reasons, components = score_candidate(
            candidate,
            expanded_tokens,
            scope,
            history_mode,
            loop_mode,
            preference_mode,
            now_dt,
            expansion_events if include_reasons else [],
            include_reasons=include_reasons,
            bm25_score=bm25_scores.get(candidate["path"], 0.0),
        )

        if score <= 0:
            continue
        if components["lexical_match"] == 0 and components["tag_overlap"] == 0 and scope == "all":
            continue

        enriched = dict(candidate)
        enriched["score"] = score
        enriched["reasons"] = reasons
        enriched["components"] = components
        ranked.append(enriched)

    ranked.sort(
        key=lambda item: (
            item["score"],
            item["updated"] or "",
            item["path"],
        ),
        reverse=True,
    )

    if progressive_disclosure_active:
        output_results = apply_progressive_disclosure(
            ranked_results=ranked,
            limit=limit,
            query_tokens=expanded_tokens,
            query_scope=scope,
            history_mode=history_mode,
            loop_mode=loop_mode,
            preference_mode=preference_mode,
            now_dt=now_dt,
            expansion_events=expansion_events,
        )
    else:
        output_results = ranked[:limit]

    return {
        "query": query,
        "scope": scope,
        "retrieval_mode": mode,
        "progressive_top_n": PROGRESSIVE_RATIONALE_TOP if progressive_disclosure_active else 0,
        "expanded_tokens": sorted(expanded_tokens),
        "expansion_events": expansion_events,
        "candidate_pool_size": len(candidates),
        "indexed_pool_size": index_pool_size,
        "prefilter_size": len(prefiltered_candidates),
        "shortlist_size": len(shortlisted),
        "results": output_results,
    }


# Backward-compat alias used by scripts/ledger and tests.
_rank_query_lexical = rank_lexical


__all__ = [
    "ROOT_DIR",
    "NOTE_INDEX_PATH",
    "NOTE_TYPES",
    "CORE_NOTE_TYPES",
    "ALIASES_PATH",
    "SHORTLIST_MIN_CANDIDATES",
    "SHORTLIST_MAX_CANDIDATES",
    "SHORTLIST_LIMIT_MULTIPLIER",
    "ATTENTION_SHORTLIST_MIN_CANDIDATES",
    "ATTENTION_SHORTLIST_MAX_CANDIDATES",
    "ATTENTION_SHORTLIST_LIMIT_MULTIPLIER",
    "DETAILED_REASONS_LIMIT",
    "PROGRESSIVE_RATIONALE_TOP",
    "HISTORY_HINTS",
    "PREFERENCE_HINTS",
    "LOOP_HINTS",
    "now_utc",
    "parse_ts",
    "canonical_scope",
    "scope_matches",
    "resolve_retrieval_mode",
    "resolve_embed_backend",
    "confidence_value",
    "compute_recency_component",
    "load_aliases",
    "expand_query_tokens",
    "read_note_for_retrieval",
    "build_attention_tokens",
    "candidate_from_note",
    "load_note_index",
    "rebuild_note_index",
    "build_candidates",
    "clear_candidate_cache",
    "candidate_index_tokens",
    "build_candidate_index",
    "retrieve_candidates_from_index",
    "coarse_candidate_score",
    "shortlist_candidates",
    "compressed_attention_candidate_score",
    "shortlist_attention_candidates",
    "has_token_overlap",
    "compute_bm25_scores",
    "prefilter_candidates_by_scope_and_type",
    "score_candidate",
    "apply_progressive_disclosure",
    "rank_lexical",
    "_rank_query_lexical",
]

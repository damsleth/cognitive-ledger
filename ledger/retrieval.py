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
import os
import time
from pathlib import Path
from typing import Any, Union

from ledger.config import get_config
from ledger.io import safe_write_text
from ledger.layout import indices_dir, logical_path, note_type_dir
from ledger.parsing import (
    extract_link_tokens,
    extract_title,
    first_checkbox,
    first_content_line,
    parse_frontmatter_text,
    parse_sections,
    tokenize,
)
from ledger.retrieval_types import (
    RetrievalCandidate,
    RetrievalResult,
    ScoreComponents,
    ScoredResult,
    TimingInfo,
)

try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - optional dependency fallback
    BM25Okapi = None


NOTE_INDEX_VERSION = 3

# Intent detection hints
HISTORY_HINTS = frozenset({"history", "closed", "past"})
PREFERENCE_HINTS = frozenset({"preference", "preferences", "style", "workflow", "habit", "habits", "tools", "editors", "editor"})
LOOP_HINTS = frozenset({"loop", "loops", "unresolved", "next", "pending", "todo", "task", "tasks", "do"})


CandidateLike = Union[RetrievalCandidate, ScoredResult, dict[str, Any]]

_CANDIDATE_CACHE: list[RetrievalCandidate] | None = None
_CANDIDATE_INDEX_CACHE: dict[str, Any] | None = None


def _cfg():
    return get_config()


def _note_index_path() -> Path:
    return indices_dir(_cfg().ledger_notes_dir) / "note_index.json"


def _note_types() -> dict[str, dict[str, Any]]:
    config = _cfg()
    return {
        name: {
            "dir": note_type_dir(config.ledger_notes_dir, name),
            "label": info["label"],
        }
        for name, info in config.note_types.items()
    }


def _candidate_value(candidate: CandidateLike, field: str, default: Any = None) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(field, default)
    return getattr(candidate, field, default)


def _candidate_sort_key(candidate: CandidateLike) -> tuple[str, str]:
    return (
        str(_candidate_value(candidate, "updated", "") or ""),
        str(_candidate_value(candidate, "path", "") or ""),
    )


def _candidate_label(note_type: str) -> str:
    return str(_note_types()[note_type]["label"])


def _copy_candidate(candidate: CandidateLike) -> RetrievalCandidate:
    return RetrievalCandidate(
        path=str(_candidate_value(candidate, "path", "") or ""),
        rel_path=str(_candidate_value(candidate, "rel_path", "") or ""),
        type=str(_candidate_value(candidate, "type", "") or ""),
        title=str(_candidate_value(candidate, "title", "") or ""),
        statement=str(_candidate_value(candidate, "statement", "") or ""),
        body=str(_candidate_value(candidate, "body", "") or ""),
        updated=str(_candidate_value(candidate, "updated", "") or ""),
        updated_ts=_candidate_value(candidate, "updated_ts"),
        confidence=float(_candidate_value(candidate, "confidence", 0.0) or 0.0),
        source=str(_candidate_value(candidate, "source", "") or ""),
        scope=str(_candidate_value(candidate, "scope", "") or ""),
        status=str(_candidate_value(candidate, "status", "") or ""),
        tags=list(_candidate_value(candidate, "tags", []) or []),
        note_tokens=set(_candidate_value(candidate, "note_tokens", set()) or set()),
        tag_tokens=set(_candidate_value(candidate, "tag_tokens", set()) or set()),
        attention_tokens=set(_candidate_value(candidate, "attention_tokens", set()) or set()),
        snippet=str(_candidate_value(candidate, "snippet", "") or ""),
        has_next_action_checkbox=bool(_candidate_value(candidate, "has_next_action_checkbox", False)),
        word_count=int(_candidate_value(candidate, "word_count", 0) or 0),
    )


def _scored_result(
    candidate: CandidateLike,
    score: float,
    reasons: list[str],
    components: ScoreComponents,
) -> ScoredResult:
    base = _copy_candidate(candidate)
    return ScoredResult(
        path=base.path,
        rel_path=base.rel_path,
        type=base.type,
        title=base.title,
        statement=base.statement,
        body=base.body,
        updated=base.updated,
        updated_ts=base.updated_ts,
        confidence=base.confidence,
        source=base.source,
        scope=base.scope,
        status=base.status,
        tags=list(base.tags),
        note_tokens=set(base.note_tokens),
        tag_tokens=set(base.tag_tokens),
        attention_tokens=set(base.attention_tokens),
        snippet=base.snippet,
        has_next_action_checkbox=base.has_next_action_checkbox,
        word_count=base.word_count,
        score=score,
        reasons=list(reasons),
        components=components,
    )


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
        retrieval_mode = os.getenv("LEDGER_RETRIEVAL_MODE", "semantic_hybrid")
    mode = str(retrieval_mode or "").strip().lower() or "semantic_hybrid"
    if mode not in config.retrieval_modes:
        return "semantic_hybrid"
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
    """Compute recency score (0.0-1.0) based on age."""
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
    """Expand query tokens using aliases."""
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
    """Read a note file for retrieval."""
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


def _candidate_from_parts(
    path: Path,
    note_type: str,
    frontmatter: dict[str, Any],
    body: str,
) -> RetrievalCandidate:
    """Build retrieval candidate from parsed note content."""
    from ledger.parsing import strip_private_tags
    body = strip_private_tags(body)
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

    cfg = _cfg()
    resolved = path.resolve()
    rel_path = logical_path(
        resolved,
        ledger_root=cfg.ledger_root,
        ledger_notes_dir=cfg.ledger_notes_dir,
    )
    slug = path.stem

    searchable_text = " ".join([title, statement, body, " ".join(tags), slug])
    note_tokens = tokenize(searchable_text)

    tag_tokens: set[str] = set()
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

    return RetrievalCandidate(
        path=str(path.resolve()),
        rel_path=str(rel_path),
        type=_candidate_label(note_type),
        title=title,
        statement=statement,
        body=body,
        updated=updated_str,
        updated_ts=updated_ts,
        confidence=confidence,
        source=source,
        scope=scope,
        status=status,
        tags=tags,
        note_tokens=note_tokens,
        tag_tokens=tag_tokens,
        attention_tokens=attention_tokens,
        snippet=snippet_source,
        has_next_action_checkbox=bool(next_action),
        word_count=len(body.split()),
    )


def candidate_from_note(path: Path, note_type: str) -> RetrievalCandidate:
    """Build retrieval candidate from a note path."""
    frontmatter, body = read_note_for_retrieval(path)
    return _candidate_from_parts(path, note_type, frontmatter, body)


def _candidate_to_json(candidate: RetrievalCandidate) -> dict[str, Any]:
    """Serialize candidate to JSON-friendly payload."""
    return {
        "path": candidate.path,
        "rel_path": candidate.rel_path,
        "type": candidate.type,
        "title": candidate.title,
        "statement": candidate.statement,
        "body": candidate.body,
        "updated": candidate.updated,
        "confidence": candidate.confidence,
        "source": candidate.source,
        "scope": candidate.scope,
        "status": candidate.status,
        "tags": list(candidate.tags),
        "note_tokens": sorted(candidate.note_tokens),
        "tag_tokens": sorted(candidate.tag_tokens),
        "attention_tokens": sorted(candidate.attention_tokens),
        "snippet": candidate.snippet,
        "has_next_action_checkbox": candidate.has_next_action_checkbox,
        "word_count": candidate.word_count,
    }


def _candidate_from_json(candidate_json: dict[str, Any]) -> RetrievalCandidate:
    """Deserialize candidate from JSON-friendly payload."""
    updated = str(candidate_json.get("updated", ""))
    try:
        confidence = float(candidate_json.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return RetrievalCandidate(
        path=str(candidate_json.get("path", "")),
        rel_path=str(candidate_json.get("rel_path", "")),
        type=str(candidate_json.get("type", "")),
        title=str(candidate_json.get("title", "")),
        statement=str(candidate_json.get("statement", "")),
        body=str(candidate_json.get("body", "")),
        updated=updated,
        updated_ts=parse_ts(updated),
        confidence=confidence,
        source=str(candidate_json.get("source", "")),
        scope=str(candidate_json.get("scope", "")),
        status=str(candidate_json.get("status", "")),
        tags=list(candidate_json.get("tags", []) or []),
        note_tokens=set(candidate_json.get("note_tokens", [])),
        tag_tokens=set(candidate_json.get("tag_tokens", [])),
        attention_tokens=set(candidate_json.get("attention_tokens", [])),
        snippet=str(candidate_json.get("snippet", "")),
        has_next_action_checkbox=bool(candidate_json.get("has_next_action_checkbox", False)),
        word_count=int(candidate_json.get("word_count", 0) or 0),
    )


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


def coarse_candidate_score(
    candidate: CandidateLike,
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
) -> tuple[float, dict[str, float | int]]:
    """Cheap first-pass score used to shortlist candidates."""
    note_tokens = set(_candidate_value(candidate, "note_tokens", set()) or set())
    tag_tokens = set(_candidate_value(candidate, "tag_tokens", set()) or set())
    candidate_type = str(_candidate_value(candidate, "type", "") or "")
    status = str(_candidate_value(candidate, "status", "") or "")

    if query_tokens:
        lexical_overlap_count = len(note_tokens & query_tokens)
        lexical_match = lexical_overlap_count / len(query_tokens)
        tag_overlap_count = len(tag_tokens & query_tokens)
        tag_overlap = tag_overlap_count / len(query_tokens)
    else:
        lexical_overlap_count = 0
        lexical_match = 0.0
        tag_overlap_count = 0
        tag_overlap = 0.0

    scope_match = 1.0 if scope_matches(str(_candidate_value(candidate, "scope", "") or ""), query_scope) else 0.0
    score = (0.70 * lexical_match) + (0.20 * tag_overlap) + (0.10 * scope_match)

    if query_scope != "all":
        score += 0.03 if scope_match >= 1.0 else -0.03
    if candidate_type == "loop" and status == "closed" and not history_mode:
        score -= 0.05
    if history_mode and candidate_type == "loop" and status == "closed":
        score += 0.05
    if loop_mode and candidate_type == "loop" and status == "open":
        score += 0.04
    if preference_mode and candidate_type == "pref":
        score += 0.04

    return score, {
        "lexical_overlap_count": lexical_overlap_count,
        "tag_overlap_count": tag_overlap_count,
        "scope_match": scope_match,
    }


def shortlist_candidates(
    candidates: list[CandidateLike],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    limit: int,
) -> list[CandidateLike]:
    """Shortlist candidate set using coarse score."""
    if not candidates:
        return []

    shortlist_target = min(
        len(candidates),
        max(
            _cfg().shortlist_min_candidates,
            min(_cfg().shortlist_max_candidates, max(1, limit) * _cfg().shortlist_limit_multiplier),
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
        key=lambda item: (item[0], *_candidate_sort_key(item[1])),
    )
    return [item[1] for item in top_scored]


def compressed_attention_candidate_score(
    candidate: CandidateLike,
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
) -> tuple[float, dict[str, float | int]]:
    """Cheap score variant that includes attention-token overlap."""
    attention_tokens = set(_candidate_value(candidate, "attention_tokens", set()) or set())
    note_tokens = set(_candidate_value(candidate, "note_tokens", set()) or set())
    tag_tokens = set(_candidate_value(candidate, "tag_tokens", set()) or set())
    candidate_type = str(_candidate_value(candidate, "type", "") or "")
    status = str(_candidate_value(candidate, "status", "") or "")

    if query_tokens:
        attention_overlap_count = len(attention_tokens & query_tokens)
        attention_overlap = attention_overlap_count / len(query_tokens)
        lexical_overlap_count = len(note_tokens & query_tokens)
        lexical_match = lexical_overlap_count / len(query_tokens)
        tag_overlap_count = len(tag_tokens & query_tokens)
        tag_overlap = tag_overlap_count / len(query_tokens)
    else:
        attention_overlap_count = 0
        attention_overlap = 0.0
        lexical_overlap_count = 0
        lexical_match = 0.0
        tag_overlap_count = 0
        tag_overlap = 0.0

    scope_match = 1.0 if scope_matches(str(_candidate_value(candidate, "scope", "") or ""), query_scope) else 0.0
    score = (0.55 * attention_overlap) + (0.20 * lexical_match) + (0.15 * tag_overlap) + (0.10 * scope_match)

    if query_scope != "all":
        score += 0.04 if scope_match >= 1.0 else -0.04
    if candidate_type == "loop" and status == "closed" and not history_mode:
        score -= 0.05
    if history_mode and candidate_type == "loop" and status == "closed":
        score += 0.06
    if loop_mode and candidate_type == "loop" and status == "open":
        score += 0.05
    if preference_mode and candidate_type == "pref":
        score += 0.05

    return score, {
        "attention_overlap_count": attention_overlap_count,
        "lexical_overlap_count": lexical_overlap_count,
        "tag_overlap_count": tag_overlap_count,
        "scope_match": scope_match,
    }


def shortlist_attention_candidates(
    candidates: list[CandidateLike],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    limit: int,
) -> list[CandidateLike]:
    """Shortlist candidate set using compressed-attention coarse score."""
    if not candidates:
        return []

    shortlist_target = min(
        len(candidates),
        max(
            _cfg().attention_shortlist_min,
            min(
                _cfg().attention_shortlist_max,
                max(1, limit) * _cfg().attention_shortlist_limit_multiplier,
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
        key=lambda item: (item[0], *_candidate_sort_key(item[1])),
    )
    return [item[1] for item in top_scored]


def has_token_overlap(candidate: CandidateLike, query_tokens: set[str]) -> bool:
    """Whether candidate has overlap with note/tag tokens."""
    if not query_tokens:
        return False
    note_tokens = set(_candidate_value(candidate, "note_tokens", set()) or set())
    tag_tokens = set(_candidate_value(candidate, "tag_tokens", set()) or set())
    return bool((note_tokens & query_tokens) or (tag_tokens & query_tokens))


def compute_bm25_scores(
    candidates: list[CandidateLike],
    query_tokens: set[str],
) -> dict[str, float]:
    """Compute normalized BM25 scores (0.0-1.0) keyed by candidate path."""
    if BM25Okapi is None or not candidates or not query_tokens:
        return {}

    corpus = [list(set(_candidate_value(candidate, "note_tokens", set()) or set())) for candidate in candidates]
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
        str(_candidate_value(candidate, "path", "") or ""): max(0.0, float(score) / max_score)
        for candidate, score in zip(candidates, raw_scores, strict=False)
    }


def prefilter_candidates_by_scope_and_type(
    candidates: list[CandidateLike],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    limit: int,
) -> list[CandidateLike]:
    """Filter obvious noise before shortlist/final scoring."""
    if not candidates:
        return []

    filtered = []
    for candidate in candidates:
        token_overlap = has_token_overlap(candidate, query_tokens)
        candidate_type = str(_candidate_value(candidate, "type", "") or "")

        if query_scope != "all" and not scope_matches(str(_candidate_value(candidate, "scope", "") or ""), query_scope) and not token_overlap:
            continue
        if preference_mode and candidate_type not in {"pref", "concept"} and not token_overlap:
            continue
        if loop_mode and candidate_type not in {"loop", "goal"} and not token_overlap:
            continue
        if history_mode and candidate_type not in {"loop", "fact", "concept"} and not token_overlap:
            continue

        filtered.append(candidate)

    if not filtered:
        return candidates

    if query_scope == "all" and (history_mode or loop_mode or preference_mode):
        return filtered

    minimum_pool = min(len(candidates), max(_cfg().shortlist_min_candidates, max(1, limit) * 2))
    if len(filtered) >= minimum_pool:
        return filtered

    seen_paths = {str(_candidate_value(item, "path", "") or "") for item in filtered}
    remainder = [item for item in candidates if str(_candidate_value(item, "path", "") or "") not in seen_paths]
    remainder.sort(key=_candidate_sort_key, reverse=True)
    for candidate in remainder:
        filtered.append(candidate)
        if len(filtered) >= minimum_pool:
            break

    return filtered


def score_candidate(
    candidate: CandidateLike,
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    now_dt: dt.datetime,
    expansion_events: list[dict[str, Any]],
    include_reasons: bool = True,
    bm25_score: float = 0.0,
    signal_summary: dict[str, Any] | None = None,
) -> tuple[float, list[str], ScoreComponents]:
    """Final score for candidate in lexical retrieval modes."""
    note_tokens = set(_candidate_value(candidate, "note_tokens", set()) or set())
    tag_tokens = set(_candidate_value(candidate, "tag_tokens", set()) or set())
    candidate_type = str(_candidate_value(candidate, "type", "") or "")
    status = str(_candidate_value(candidate, "status", "") or "")

    if query_tokens:
        lexical_overlap_count = len(note_tokens & query_tokens)
        lexical_match = lexical_overlap_count / len(query_tokens)
        tag_overlap_count = len(tag_tokens & query_tokens)
        tag_overlap = tag_overlap_count / len(query_tokens)
    else:
        lexical_overlap_count = 0
        lexical_match = 0.0
        tag_overlap_count = 0
        tag_overlap = 0.0

    scope_match = 1.0 if scope_matches(str(_candidate_value(candidate, "scope", "") or ""), query_scope) else 0.0
    recency = compute_recency_component(_candidate_value(candidate, "updated_ts"), now_dt)
    confidence = float(_candidate_value(candidate, "confidence", 0.0) or 0.0)

    config = _cfg()
    score = (
        (config.score_weight_bm25 * bm25_score)
        + (config.score_weight_lexical * lexical_match)
        + (config.score_weight_tag * tag_overlap)
        + (config.score_weight_scope * scope_match)
        + (config.score_weight_recency * recency)
        + (config.score_weight_confidence * confidence)
    )

    # Identity note boost
    if candidate_type == "id":
        score += config.identity_score_boost
        if include_reasons:
            reasons_prefix = [f"identity_boost=+{config.identity_score_boost:.2f}"]
        else:
            reasons_prefix = []
    else:
        reasons_prefix = []

    # Signal feedback score
    if config.score_weight_signal > 0 and signal_summary is not None:
        rel_path = str(_candidate_value(candidate, "rel_path", "") or "")
        if rel_path:
            from ledger.signals import get_signal_score
            sig_score = get_signal_score(rel_path, summary=signal_summary)
            score += config.score_weight_signal * sig_score
            if include_reasons and sig_score != 0:
                reasons_prefix.append(f"signal={sig_score:.3f}")

    reasons = list(reasons_prefix)
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
    if candidate_type == "loop" and status == "closed" and not history_mode:
        score -= 0.20
        if include_reasons:
            reasons.append("closed_loop_penalty")
    if history_mode and candidate_type == "loop":
        if status == "closed":
            score += 0.12
            if include_reasons:
                reasons.append("history_closed_loop_boost")
        elif status == "open":
            score -= 0.05
            if include_reasons:
                reasons.append("history_open_loop_demote")
    if loop_mode and candidate_type == "loop" and status == "open":
        score += 0.07
        if include_reasons:
            reasons.append("open_loop_intent_boost")
    if preference_mode and candidate_type == "pref":
        score += 0.07
        if include_reasons:
            reasons.append("preference_intent_boost")
    if preference_mode and candidate_type not in {"pref", "fact"}:
        score -= 0.05
        if include_reasons:
            reasons.append("preference_non_pref_demote")

    if include_reasons and expansion_events:
        alias_summary = ", ".join(sorted({f"{e['alias']}->{e['phrase']}" for e in expansion_events}))
        reasons.append(f"alias_expansion={alias_summary}")

    if include_reasons and recency > 0:
        reasons.append(f"recency={recency:.2f}")
    if include_reasons:
        reasons.append(f"confidence={confidence:.2f}")

    score = max(0.0, min(1.0, score))
    return score, reasons, ScoreComponents(
        bm25_score=bm25_score,
        lexical_match=lexical_match,
        tag_overlap=tag_overlap,
        scope_match=scope_match,
        recency=recency,
        confidence=confidence,
    )


def apply_progressive_disclosure(
    ranked_results: list[ScoredResult],
    limit: int,
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    now_dt: dt.datetime,
    expansion_events: list[dict[str, Any]],
) -> list[ScoredResult]:
    """Attach full rationales to top-N items and compact output for remainder.

    Returns new ScoredResult instances to avoid mutating cached objects.
    """
    from dataclasses import replace

    output: list[ScoredResult] = []
    top_n = min(_cfg().progressive_rationale_top, limit)
    for idx, item in enumerate(ranked_results[:limit]):
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
                bm25_score=item.components.bm25_score,
            )
            output.append(replace(item, reasons=reasons, disclosure_level="rationale"))
        else:
            output.append(replace(item, reasons=[], disclosure_level="compact"))
    return output


def _maybe_log_query(result: "RetrievalResult") -> None:
    """Append a JSONL telemetry line if LEDGER_QUERY_LOG=1."""
    if os.environ.get("LEDGER_QUERY_LOG") != "1":
        return
    try:
        log_path = indices_dir(_cfg().ledger_notes_dir) / "query_log.jsonl"
        entry = json.dumps(
            {
                "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "query": result.query,
                "scope": result.scope,
                "mode": result.retrieval_mode,
                "top_3": [r.rel_path for r in result.results[:3]],
                "latency_ms": round(result.timing.total_ms, 1),
                "candidates": result.candidate_pool_size,
            },
            ensure_ascii=False,
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass


def rank_lexical(
    query: str,
    scope: str = "all",
    limit: int = 8,
    aliases_path: Path | str | None = None,
    now_dt: dt.datetime | None = None,
    retrieval_mode: str = "legacy",
) -> RetrievalResult:
    """Rank notes using lexical retrieval modes."""
    t0 = time.perf_counter()
    now_dt = now_dt or now_utc()
    mode = resolve_retrieval_mode(retrieval_mode)
    two_stage_active = mode in {"legacy", "two_stage"}
    compressed_attention_active = mode in {"legacy", "compressed_attention"}
    scope_prefilter_active = mode in {"legacy", "scope_type_prefilter"}
    precomputed_index_active = mode in {"legacy", "precomputed_index"}
    progressive_disclosure_active = mode in {"legacy", "progressive_disclosure"}
    aliases = load_aliases(Path(aliases_path) if aliases_path is not None else _cfg().aliases_path)
    query_tokens = tokenize(query)
    expanded_tokens, expansion_events = expand_query_tokens(query_tokens, aliases)
    t_expand = time.perf_counter()

    history_mode = any(token in HISTORY_HINTS for token in query_tokens)
    loop_mode = any(token in LOOP_HINTS for token in query_tokens)
    preference_mode = any(token in PREFERENCE_HINTS for token in query_tokens)

    # Load signal summary once if signal scoring is enabled
    config = _cfg()
    _signal_summary: dict[str, Any] | None = None
    if config.score_weight_signal > 0:
        from ledger.signals import load_signal_summary
        summary = load_signal_summary()
        total = summary.get("_meta", {}).get("total_signals", 0)
        if total >= config.signal_min_entries:
            _signal_summary = summary
    include_reasons = True if mode == "legacy" else (limit <= _cfg().detailed_reasons_limit)
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
            minimum_pool=max(_cfg().shortlist_min_candidates, max(1, limit) * 2),
        )
        index_pool_size = len(prefiltered_candidates)
    else:
        candidates = build_candidates(
            use_cache=(
                mode in {"two_stage", "compressed_attention", "progressive_disclosure"}
            )
            or two_stage_active
        )
        prefiltered_candidates = candidates
    t_candidates = time.perf_counter()

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
    t_prefilter = time.perf_counter()

    if compressed_attention_active and limit <= _cfg().attention_shortlist_max:
        shortlisted = shortlist_attention_candidates(
            prefiltered_candidates,
            expanded_tokens,
            scope,
            history_mode,
            loop_mode,
            preference_mode,
            limit=limit,
        )
    elif two_stage_active and limit <= _cfg().shortlist_max_candidates:
        shortlisted = shortlist_candidates(
            prefiltered_candidates,
            expanded_tokens,
            scope,
            history_mode,
            loop_mode,
            preference_mode,
            limit=limit,
        )
    elif precomputed_index_active and limit <= _cfg().shortlist_max_candidates:
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
    elif scope_prefilter_active and limit <= _cfg().shortlist_max_candidates:
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
    t_shortlist = time.perf_counter()

    ranked: list[ScoredResult] = []
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
            bm25_score=bm25_scores.get(str(_candidate_value(candidate, "path", "") or ""), 0.0),
            signal_summary=_signal_summary,
        )

        if score <= 0:
            continue
        if components.lexical_match == 0 and components.tag_overlap == 0 and scope == "all":
            continue

        ranked.append(_scored_result(candidate, score, reasons, components))

    ranked.sort(key=lambda item: (item.score, item.updated or "", item.path), reverse=True)

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
    t_score = time.perf_counter()

    result = RetrievalResult(
        query=query,
        scope=scope,
        retrieval_mode=mode,
        progressive_top_n=_cfg().progressive_rationale_top if progressive_disclosure_active else 0,
        expanded_tokens=sorted(expanded_tokens),
        expansion_events=expansion_events,
        candidate_pool_size=len(candidates),
        indexed_pool_size=index_pool_size,
        prefilter_size=len(prefiltered_candidates),
        shortlist_size=len(shortlisted),
        results=output_results,
        timing=TimingInfo(
            expand_ms=(t_expand - t0) * 1000.0,
            candidates_ms=(t_candidates - t_expand) * 1000.0,
            prefilter_ms=(t_prefilter - t_candidates) * 1000.0,
            shortlist_ms=(t_shortlist - t_prefilter) * 1000.0,
            score_ms=(t_score - t_shortlist) * 1000.0,
            total_ms=(t_score - t0) * 1000.0,
        ),
    )
    _maybe_log_query(result)
    return result


# Backward-compat alias used by scripts/ledger and tests.
_rank_query_lexical = rank_lexical


__all__ = [
    "NOTE_INDEX_VERSION",
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
    "related_to_text",
]


def related_to_text(
    text: str,
    top_k: int = 5,
    scope: str = "all",
) -> list[dict[str, Any]]:
    """Find ledger notes related to arbitrary text.

    Tokenizes the input text and runs it through the candidate index
    pipeline, returning ranked results. Designed for use by external
    tools (Obsidian plugin, CLI) that need to query with free text
    rather than ledger note paths.

    Args:
        text: Arbitrary text to find related notes for.
        top_k: Maximum results to return.
        scope: Scope filter (default: all).

    Returns:
        List of dicts with path, title, score, snippet.
    """
    from ledger.parsing import tokenize

    config = get_config()
    query_tokens = tokenize(text)
    if not query_tokens:
        return []

    candidates = build_candidates()
    if not candidates:
        return []

    index = build_candidate_index(candidates)
    shortlisted = retrieve_candidates_from_index(index, query_tokens)

    if scope != "all":
        shortlisted = [c for c in shortlisted if scope_matches(c.scope, scope)]

    now = now_utc()
    scored: list[ScoredResult] = []
    for candidate in shortlisted:
        result = score_candidate(
            candidate,
            query_tokens,
            aliases={},
            scope=scope,
            now_dt=now,
        )
        scored.append(result)

    scored.sort(key=lambda r: r.score, reverse=True)

    return [
        {
            "path": r.rel_path or r.path,
            "title": r.title,
            "score": round(r.score, 4),
            "snippet": r.snippet[:200],
            "tags": r.tags,
            "updated": r.updated,
        }
        for r in scored[:top_k]
    ]

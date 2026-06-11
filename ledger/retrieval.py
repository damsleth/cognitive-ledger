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
import warnings as _warnings
from pathlib import Path
from typing import Any, Union

from ledger.config import get_config
from ledger.io import safe_write_text
from ledger.layout import NOTE_LAYOUTS, indices_dir, logical_path, note_index_path, note_type_dir, resolve_path
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
from ledger.scoring import (
    COARSE_LADDER,
    FINE_LADDER,
    canonical_scope,
    clamp01,
    derive_provenance,
    effective_confidence,
    half_life_for_type,
    intent_adjustments,
    overlap_components,
    scope_component as _scope_component,
    scope_matches,
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
    return note_index_path(_cfg().ledger_notes_dir)


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
        valid_from=str(_candidate_value(candidate, "valid_from", "") or ""),
        valid_to=str(_candidate_value(candidate, "valid_to", "") or ""),
        superseded_by=str(_candidate_value(candidate, "superseded_by", "") or ""),
        created_ts=_candidate_value(candidate, "created_ts"),
        provenance=str(_candidate_value(candidate, "provenance", "") or ""),
        via=str(_candidate_value(candidate, "via", "") or ""),
        validation_count=float(_candidate_value(candidate, "validation_count", 0.0) or 0.0),
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
        valid_from=base.valid_from,
        valid_to=base.valid_to,
        superseded_by=base.superseded_by,
        created_ts=base.created_ts,
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


# canonical_scope and scope_matches are imported from ledger.scoring above.
# The names are re-exported here for back-compat with existing callers and
# tests that do ``from ledger.retrieval import scope_matches``.
__all__ = [*globals().get("__all__", []), "canonical_scope", "scope_matches"]


def resolve_retrieval_mode(retrieval_mode: str | None) -> str:
    """Resolve retrieval mode from argument, environment, or config."""
    config = get_config()
    if retrieval_mode is None:
        retrieval_mode = os.getenv("LEDGER_RETRIEVAL_MODE") or config.retrieval_mode
    mode = str(retrieval_mode or "").strip().lower() or "semantic_hybrid"
    if mode not in config.retrieval_modes:
        return "semantic_hybrid"
    return mode


def resolve_embed_backend(embed_backend: str | None) -> str:
    """Resolve embedding backend from argument, environment, or config."""
    config = get_config()
    if embed_backend is None:
        embed_backend = os.getenv("LEDGER_EMBED_BACKEND") or config.embed_backend
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


def resolve_confidence(
    candidate: "CandidateLike",
    signal_summary: dict[str, Any] | None = None,
) -> float:
    """Confidence value to use in ranking, honouring provenance weighting.

    With ``provenance_weighting_enabled`` False (default) this returns the raw,
    clamped ``confidence`` — bit-identical to pre-plan-42 behaviour. With it
    enabled, confidence is replaced by ``scoring.effective_confidence``, derived
    from the note's provenance (explicit or derived from source/via) and its
    affirmation count.

    Validation count is read from the live ``signal_summary`` when one is in
    scope (authoritative), otherwise falls back to the candidate's
    ``validation_count`` snapshot. This is the single place both the weighted-sum
    confidence term and the prior's importance term resolve confidence, so they
    never diverge.
    """
    raw = float(_candidate_value(candidate, "confidence", 0.0) or 0.0)
    raw = max(0.0, min(1.0, raw))
    config = _cfg()
    if not config.provenance_weighting_enabled:
        return raw

    resolved_prov = derive_provenance(
        str(_candidate_value(candidate, "source", "") or ""),
        str(_candidate_value(candidate, "via", "") or ""),
        str(_candidate_value(candidate, "provenance", "") or ""),
    )

    rel_path = str(_candidate_value(candidate, "rel_path", "") or "")
    if signal_summary is not None and rel_path:
        from ledger.signals import get_validation_count
        validations = get_validation_count(rel_path, summary=signal_summary)
    else:
        validations = float(_candidate_value(candidate, "validation_count", 0.0) or 0.0)

    return effective_confidence(
        raw,
        resolved_prov,
        validations,
        boost_per_signal=config.validation_boost_per_signal,
        boost_cap=config.validation_boost_cap,
    )


def compute_recency_component(updated_ts: dt.datetime | None, now_dt: dt.datetime) -> float:
    """Compute recency score (0.0-1.0) based on age."""
    if not updated_ts:
        return 0.0
    age_days = max(0.0, (now_dt - updated_ts).total_seconds() / 86400.0)
    return max(0.0, 1.0 - (age_days / 90.0))


def compute_prior_score(
    candidate: "CandidateLike",
    now_dt: dt.datetime,
    query_lexical_relevance: float = 0.0,
    signal_summary: dict[str, Any] | None = None,
) -> float:
    """Compute the prior score for a candidate (0.0-1.0).

    The prior is a query-independent quality signal combining:
    - importance: note confidence (curated quality proxy)
    - recency: half-life decay from creation date (falls back to updated_ts)
    - relevance: caller-supplied query-relevance value (lexical or cosine)

    The prior is designed to be additive — it nudges ranking non-flat
    before signal feedback has accrued, without displacing the primary
    lexical/semantic relevance score.

    Age is derived from ``created_ts`` when available, otherwise falls
    back to ``updated_ts``.  Notes without any timestamp get 0 recency.

    Config weights: ``prior_w_importance``, ``prior_w_recency``,
    ``prior_w_relevance``, ``prior_recency_half_life_days``.
    """
    import math

    config = _cfg()

    # Importance term — provenance-weighted when enabled, else raw confidence.
    confidence = resolve_confidence(candidate, signal_summary)

    # Prefer created_ts for age; fall back to updated_ts if absent.
    age_ts: dt.datetime | None = (
        _candidate_value(candidate, "created_ts")
        or _candidate_value(candidate, "updated_ts")
    )
    if age_ts is not None:
        age_days = max(0.0, (now_dt - age_ts).total_seconds() / 86400.0)
        # Per-type half-life (plan 43); empty map → global default, unchanged.
        half_life = half_life_for_type(
            str(_candidate_value(candidate, "type", "") or ""),
            by_type=config.recency_half_life_by_type,
            default_days=config.prior_recency_half_life_days,
        )
        lam = math.log(2.0) / half_life
        prior_recency = math.exp(-lam * age_days)
    else:
        prior_recency = 0.0

    relevance = max(0.0, min(1.0, query_lexical_relevance))

    prior = (
        config.prior_w_importance * confidence
        + config.prior_w_recency * prior_recency
        + config.prior_w_relevance * relevance
    )
    return max(0.0, min(1.0, prior))


def prior_tiebreak_factor(
    base_score: float,
    leader_base_score: float,
    tie_band: float,
) -> float:
    """Continuous scaling factor (0.0-1.0) for a candidate's prior contribution.

    The prior is a TIE-BREAKER: it may only reorder candidates whose base
    (pre-prior) scores are near-tied. A candidate with a clear base-score lead
    must keep its rank regardless of its prior. This function computes the
    factor by which a candidate's prior term is scaled so that the prior fades
    to zero as the candidate's base-score gap to the local leader grows.

    Definitions
    -----------
    - ``leader_base_score``: the highest base score in the candidate pool.
    - ``gap``: the candidate's *relative* base-score shortfall to the leader,
      ``(leader_base_score - base_score) / leader_base_score`` (clamped to
      [0, inf); 0 when the candidate IS the leader). A relative gap makes the
      band scale-invariant across queries with different absolute score ranges.
    - ``tie_band``: the relative gap at which the prior contribution reaches 0.

    Formula
    -------
        factor = clamp(1 - gap / tie_band, 0.0, 1.0)

    This is a continuous linear ramp (not a hard cutoff) from 1.0 at gap == 0
    to 0.0 at gap >= tie_band, avoiding rank instability at the boundary.
    A non-positive ``tie_band`` disables the tie-breaker entirely (factor 0,
    i.e. the prior never reorders anything). A non-positive or non-finite
    leader score yields factor 1.0 (degenerate pool: fall back to flat prior).
    """
    if tie_band <= 0.0:
        return 0.0
    if leader_base_score <= 0.0:
        return 1.0
    gap = (leader_base_score - base_score) / leader_base_score
    if gap <= 0.0:
        return 1.0
    factor = 1.0 - (gap / tie_band)
    return max(0.0, min(1.0, factor))


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
    created_str = str(frontmatter.get("created", "")).strip()
    created_ts = parse_ts(created_str) if created_str else None
    confidence = confidence_value(frontmatter)
    source = str(frontmatter.get("source", "")).strip().lower()
    provenance = str(frontmatter.get("provenance", "")).strip().lower()
    via = str(frontmatter.get("via", "")).strip().lower()

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

    # Capture bitemporal validity fields (optional; absent on legacy notes).
    def _str_or_empty(val: Any) -> str:
        s = str(val or "").strip()
        return "" if s in ("null", "~", "None", "[]") else s

    valid_from = _str_or_empty(frontmatter.get("valid_from", ""))
    valid_to = _str_or_empty(frontmatter.get("valid_to", ""))
    superseded_by = _str_or_empty(frontmatter.get("superseded_by", ""))

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
        valid_from=valid_from,
        valid_to=valid_to,
        superseded_by=superseded_by,
        created_ts=created_ts,
        provenance=provenance,
        via=via,
    )


def candidate_from_note(path: Path, note_type: str) -> RetrievalCandidate:
    """Build retrieval candidate from a note path."""
    frontmatter, body = read_note_for_retrieval(path)
    return _candidate_from_parts(path, note_type, frontmatter, body)


def _candidate_to_json(candidate: RetrievalCandidate) -> dict[str, Any]:
    """Serialize candidate to JSON-friendly payload."""
    persisted_path = candidate.rel_path or logical_path(
        candidate.path,
        ledger_root=_cfg().ledger_root,
        ledger_notes_dir=_cfg().ledger_notes_dir,
    ).as_posix()
    payload: dict[str, Any] = {
        "path": persisted_path,
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
    # Persist bitemporal fields only when set (keeps index lean for legacy notes).
    if candidate.valid_from:
        payload["valid_from"] = candidate.valid_from
    if candidate.valid_to:
        payload["valid_to"] = candidate.valid_to
    if candidate.superseded_by:
        payload["superseded_by"] = candidate.superseded_by
    # Provenance inputs — persist only when set (keeps index lean for legacy notes).
    if candidate.provenance:
        payload["provenance"] = candidate.provenance
    if candidate.via:
        payload["via"] = candidate.via
    # Persist created ISO string when available (created_ts is derived from it,
    # mirroring the pattern used for updated / updated_ts).
    if candidate.created_ts is not None:
        payload["created"] = candidate.created_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    return payload


def _candidate_from_json(candidate_json: dict[str, Any]) -> RetrievalCandidate:
    """Deserialize candidate from JSON-friendly payload."""
    updated = str(candidate_json.get("updated", ""))
    created = str(candidate_json.get("created", ""))
    raw_path = str(candidate_json.get("path", "") or "")
    rel_path = str(candidate_json.get("rel_path", "") or "")
    resolved_candidate_path = raw_path
    if raw_path and not Path(raw_path).is_absolute():
        resolved_candidate_path = str(
            resolve_path(
                raw_path,
                ledger_root=_cfg().ledger_root,
                ledger_notes_dir=_cfg().ledger_notes_dir,
            )
        )
    elif not raw_path and rel_path:
        resolved_candidate_path = str(
            resolve_path(
                rel_path,
                ledger_root=_cfg().ledger_root,
                ledger_notes_dir=_cfg().ledger_notes_dir,
            )
        )
    try:
        confidence = float(candidate_json.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return RetrievalCandidate(
        path=resolved_candidate_path,
        rel_path=rel_path,
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
        # Bitemporal fields — absent in older indexes; default to empty string.
        valid_from=str(candidate_json.get("valid_from", "") or ""),
        valid_to=str(candidate_json.get("valid_to", "") or ""),
        superseded_by=str(candidate_json.get("superseded_by", "") or ""),
        # created_ts is recomputed from the persisted "created" ISO string,
        # mirroring the updated / updated_ts pattern.
        created_ts=parse_ts(created) if created else None,
        # Provenance inputs — absent in older indexes; default to empty string.
        provenance=str(candidate_json.get("provenance", "") or ""),
        via=str(candidate_json.get("via", "") or ""),
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


def _build_archive_candidates() -> list[RetrievalCandidate]:
    """Build retrieval candidates from notes/09_archive for --as-of queries.

    Archive notes are excluded from the normal corpus (not in CORE_NOTE_TYPES)
    so they need to be loaded separately when --as-of widens the candidate pool.
    Returns an empty list if the archive directory does not exist.
    """
    config = _cfg()
    archive_dir = config.ledger_notes_dir / "09_archive"
    if not archive_dir.is_dir():
        return []

    candidates: list[RetrievalCandidate] = []
    for note_path in sorted(archive_dir.glob("*.md")):
        if note_path.name == ".gitkeep":
            continue
        try:
            frontmatter, body = read_note_for_retrieval(note_path)
        except Exception:
            continue
        # Determine note type from filename prefix.
        stem = note_path.stem
        note_type = "facts"  # default / fallback type for archived notes
        for nt in _note_types():
            nl = NOTE_LAYOUTS.get(nt)
            if nl and stem.startswith(nl.prefix):
                note_type = nt
                break
        candidate = _candidate_from_parts(note_path, note_type, frontmatter, body)
        candidates.append(candidate)
    return candidates


def build_candidates_with_archive() -> list[RetrievalCandidate]:
    """Return all core candidates PLUS archive notes for --as-of queries.

    This is the widened candidate pool used when a temporal filter is requested.
    The result is NOT cached (archive candidates are only needed for as-of queries).
    """
    return build_candidates(use_cache=False) + _build_archive_candidates()


# ---------------------------------------------------------------------------
# Temporal filter helpers
# ---------------------------------------------------------------------------


def apply_temporal_filter(
    candidates: list[RetrievalCandidate],
    *,
    as_of: dt.datetime | None,
    now_dt: dt.datetime,
) -> list[RetrievalCandidate]:
    """Filter candidates by valid-time interval.

    Default path (as_of=None):
    - Notes with NO validity fields pass through unchanged (golden requirement).
    - Notes with a valid_to in the past (< now_dt) are excluded — they are
      superseded/expired facts that should not appear in current retrieval.

    --as-of path (as_of is a datetime):
    - Keep only notes where valid_from <= as_of AND (valid_to null OR valid_to >= as_of).
    - Notes with null valid_from are valid for all time (never silently dropped;
      a warning is emitted to stderr so the user knows migration would help).
    - This path naturally INCLUDES archive notes (if they are in the candidate list).

    Args:
        candidates: Full candidate pool.
        as_of:      Target instant for temporal query, or None for current retrieval.
        now_dt:     Current UTC datetime (used for the default-path expiry check).

    Returns:
        Filtered list of candidates.
    """
    from ledger.bitemporal import is_valid_at, parse_validity

    filtered: list[RetrievalCandidate] = []
    _unmigrated_count = 0  # track notes with no valid_from for a single summary warning
    for c in candidates:
        vf_raw = c.valid_from
        vt_raw = c.valid_to

        superseded_by_raw = c.superseded_by
        has_any_validity_fields = bool(vf_raw or vt_raw or superseded_by_raw)

        if as_of is None:
            # Default path: pass-through notes with no validity fields.
            if not has_any_validity_fields:
                filtered.append(c)
                continue
            # Notes with valid_to in the past are hidden in current retrieval.
            fm_stub: dict[str, Any] = {}
            if vf_raw:
                fm_stub["valid_from"] = vf_raw
            if vt_raw:
                fm_stub["valid_to"] = vt_raw
            if is_valid_at(fm_stub, now_dt):
                filtered.append(c)
            # else: expired — skip silently
        else:
            # --as-of path: apply temporal filter strictly.
            if not vf_raw:
                # Null valid_from = valid for all time (open left bound).
                # Count rather than warn per-note to avoid spam.
                _unmigrated_count += 1
                # Still apply valid_to check if present.
                fm_stub: dict[str, Any] = {}
                if vt_raw:
                    fm_stub["valid_to"] = vt_raw
                if is_valid_at(fm_stub, as_of):
                    filtered.append(c)
                continue
            fm_stub = {}
            if vf_raw:
                fm_stub["valid_from"] = vf_raw
            if vt_raw:
                fm_stub["valid_to"] = vt_raw
            if is_valid_at(fm_stub, as_of):
                filtered.append(c)

    # Emit at most one warning per call for all unmigrated notes.
    if _unmigrated_count:
        _warnings.warn(
            f"{_unmigrated_count} note(s) have no valid_from; treating as valid "
            "for all time. Run `ledger migrate bitemporal --check` to see "
            "migration candidates.",
            stacklevel=4,
        )
    return filtered


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

    lexical_match, tag_overlap, lexical_overlap_count, tag_overlap_count = overlap_components(
        note_tokens, tag_tokens, query_tokens
    )
    sm = _scope_component(str(_candidate_value(candidate, "scope", "") or ""), query_scope)
    score = (0.70 * lexical_match) + (0.20 * tag_overlap) + (0.10 * sm)

    delta, _ = intent_adjustments(
        COARSE_LADDER,
        candidate_type=candidate_type,
        status=status,
        query_scope=query_scope,
        sm=sm,
        history_mode=history_mode,
        loop_mode=loop_mode,
        preference_mode=preference_mode,
        include_reasons=False,
    )
    score += delta

    return score, {
        "lexical_overlap_count": lexical_overlap_count,
        "tag_overlap_count": tag_overlap_count,
        "scope_match": sm,
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
    """Final score for candidate in lexical retrieval modes.

    The returned ``score`` is the BASE score (relevance + identity boost +
    signal + intent boosts) and does NOT include the prior. The prior is
    computed via ``compute_prior_score`` and returned separately in
    ``ScoreComponents.prior_score``; it is blended in afterwards by
    ``apply_prior_tiebreak`` as a tie-breaker (see that function and
    ``prior_tiebreak_factor``). This keeps the prior from displacing a clear
    relevance winner — it only reorders near-tied candidates.

    The identity-type boost remains part of the base score (a separate
    additive term) so it is independently measurable from the prior.
    """
    note_tokens = set(_candidate_value(candidate, "note_tokens", set()) or set())
    tag_tokens = set(_candidate_value(candidate, "tag_tokens", set()) or set())
    candidate_type = str(_candidate_value(candidate, "type", "") or "")
    status = str(_candidate_value(candidate, "status", "") or "")

    lexical_match, tag_overlap, lexical_overlap_count, tag_overlap_count = overlap_components(
        note_tokens, tag_tokens, query_tokens
    )
    sm = _scope_component(str(_candidate_value(candidate, "scope", "") or ""), query_scope)
    scope_match = sm  # alias used in ScoreComponents below
    recency = compute_recency_component(_candidate_value(candidate, "updated_ts"), now_dt)
    # Provenance-weighted when enabled (plan 42); raw confidence otherwise.
    confidence = resolve_confidence(candidate, signal_summary)

    config = _cfg()
    score = (
        (config.score_weight_bm25 * bm25_score)
        + (config.score_weight_lexical * lexical_match)
        + (config.score_weight_tag * tag_overlap)
        + (config.score_weight_scope * sm)
        + (config.score_weight_recency * recency)
        + (config.score_weight_confidence * confidence)
    )

    # Prior score — quality signal active before signal feedback accrues.
    # Uses lexical_match as the relevance proxy in lexical retrieval mode.
    # NOTE: the prior is NOT added to the score here. It is returned in
    # ScoreComponents.prior_score and blended in as a tie-breaker by
    # apply_prior_tiebreak once the full candidate pool (and its leader base
    # score) is known. This keeps the prior from displacing a clear winner.
    prior = 0.0
    if config.prior_enabled:
        prior = compute_prior_score(
            candidate,
            now_dt=now_dt,
            query_lexical_relevance=lexical_match,
            signal_summary=signal_summary,
        )

    # Identity note boost — kept as a separate additive term so it is
    # independently measurable from the prior.
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

    intent_delta, intent_reasons = intent_adjustments(
        FINE_LADDER,
        candidate_type=candidate_type,
        status=status,
        query_scope=query_scope,
        sm=sm,
        history_mode=history_mode,
        loop_mode=loop_mode,
        preference_mode=preference_mode,
        include_reasons=include_reasons,
    )
    score += intent_delta
    reasons.extend(intent_reasons)

    if include_reasons and expansion_events:
        alias_summary = ", ".join(sorted({f"{e['alias']}->{e['phrase']}" for e in expansion_events}))
        reasons.append(f"alias_expansion={alias_summary}")

    if include_reasons and recency > 0:
        reasons.append(f"recency={recency:.2f}")
    if include_reasons:
        reasons.append(f"confidence={confidence:.2f}")
    if include_reasons and prior > 0:
        reasons.append(f"prior={prior:.3f}")

    score = clamp01(score)
    return score, reasons, ScoreComponents(
        bm25_score=bm25_score,
        lexical_match=lexical_match,
        tag_overlap=tag_overlap,
        scope_match=scope_match,
        recency=recency,
        confidence=confidence,
        prior_score=prior,
    )


def attach_trust_verdicts(
    results: list["ScoredResult"],
    signal_summary: dict[str, Any] | None = None,
) -> list["ScoredResult"]:
    """Attach a display-only trust verdict to each result (plan 46).

    Sets ``ScoredResult.trust`` in place and returns the same list. This NEVER
    reorders results — it only annotates them — so it is safe to call after the
    final ranking is fixed. Confidence is resolved via ``resolve_confidence`` so
    it reflects provenance weighting when enabled and falls back to raw
    confidence otherwise. Validation/contradiction counts come from the signal
    summary when one is in scope; ``superseded_by`` is read off the candidate.
    """
    config = _cfg()
    if not config.show_trust_verdict:
        return results
    from ledger.scoring import trust_verdict
    for result in results:
        conf = resolve_confidence(result, signal_summary)
        components = getattr(result, "components", None)
        recency = float(getattr(components, "recency", 0.0) or 0.0)
        rel_path = str(_candidate_value(result, "rel_path", "") or "")
        validations = 0.0
        contradicted = False
        if signal_summary is not None and rel_path:
            from ledger.signals import get_contradiction_count, get_validation_count
            validations = get_validation_count(rel_path, summary=signal_summary)
            contradicted = get_contradiction_count(rel_path, summary=signal_summary) > 0
        superseded = bool(str(_candidate_value(result, "superseded_by", "") or ""))
        result.trust = trust_verdict(
            effective_confidence=conf,
            validation_count=validations,
            contradicted=contradicted,
            superseded=superseded,
            recency=recency,
        )
    return results


def apply_prior_tiebreak(ranked: list["ScoredResult"]) -> list["ScoredResult"]:
    """Blend the prior into base scores as a TIE-BREAKER, then re-sort.

    This is the single, shared place where the prior is folded into the final
    score for BOTH retrieval paths (lexical ``score_candidate`` and dense
    ``rank_query_semantic_hybrid``). Each input ``ScoredResult`` must carry:

    - ``score``: the BASE score (relevance + boosts + signal), prior excluded.
    - ``components.prior_score``: the raw prior in [0, 1] (0 when disabled).

    Mechanism
    ---------
    1. Find the local leader's base score (the max base score in the pool).
    2. For each candidate compute ``factor = prior_tiebreak_factor(base,
       leader, config.prior_tie_band)`` — a continuous ramp from 1.0 (the
       candidate is at/near the leader) to 0.0 (its relative base gap exceeds
       the tie band). Candidates with a clear base-score lead over their
       followers keep their rank: a trailing candidate's prior is scaled to 0,
       so it cannot leapfrog a clear winner.
    3. Final score = ``base + config.prior_weight * factor * prior_score``,
       clamped to [0, 1]. The list is re-sorted by (score, updated, path).

    When the prior is disabled (every ``prior_score`` is 0) or
    ``prior_weight`` is 0, this is a no-op on the scores — it only re-sorts,
    which is order-stable for already-sorted input, so prior_enabled=False
    reproduces pre-prior scores exactly.
    """
    if not ranked:
        return ranked
    config = _cfg()
    weight = float(config.prior_weight)
    tie_band = float(config.prior_tie_band)
    leader_base = max(item.score for item in ranked)
    for item in ranked:
        prior = float(getattr(item.components, "prior_score", 0.0) or 0.0)
        if prior <= 0.0 or weight <= 0.0:
            continue
        factor = prior_tiebreak_factor(item.score, leader_base, tie_band)
        if factor <= 0.0:
            continue
        item.score = max(0.0, min(1.0, item.score + weight * factor * prior))
    ranked.sort(key=lambda item: (item.score, item.updated or "", item.path), reverse=True)
    return ranked


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
    as_of: dt.datetime | None = None,
) -> RetrievalResult:
    """Rank notes using lexical retrieval modes.

    Args:
        as_of: When set, widen the candidate pool to include 09_archive notes
               and apply a valid-time filter so only notes valid at *as_of* are
               returned.  The default (None) applies the current-validity filter:
               notes with an expired valid_to are hidden, but notes with no
               validity fields at all are passed through unchanged.
    """
    t0 = time.perf_counter()
    now_dt = now_dt or now_utc()
    mode = resolve_retrieval_mode(retrieval_mode)
    two_stage_active = mode in {"legacy", "two_stage"}
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
    from ledger.signals import signal_summary_if_active
    _signal_summary: dict[str, Any] | None = signal_summary_if_active(config)
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
                mode in {"two_stage", "progressive_disclosure"}
            )
            or two_stage_active
        )
        prefiltered_candidates = candidates

    # When --as-of is requested widen the pool to include archive notes.
    if as_of is not None:
        archive_extras = _build_archive_candidates()
        candidates = list(candidates) + archive_extras
        prefiltered_candidates = list(prefiltered_candidates) + archive_extras

    # Apply temporal filter after candidate generation.
    candidates = apply_temporal_filter(candidates, as_of=as_of, now_dt=now_dt)
    prefiltered_candidates = apply_temporal_filter(
        prefiltered_candidates, as_of=as_of, now_dt=now_dt
    )

    t_candidates = time.perf_counter()

    if scope_prefilter_active:
        prefiltered_candidates = prefilter_candidates_by_scope_and_type(
            prefiltered_candidates,
            expanded_tokens,
            scope,
            history_mode,
            loop_mode,
            preference_mode,
            limit=limit,
        )
    t_prefilter = time.perf_counter()

    if two_stage_active and limit <= _cfg().shortlist_max_candidates:
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

    # Blend the prior in as a tie-breaker over the full pool, then sort.
    apply_prior_tiebreak(ranked)

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


# Backward-compat alias used by tests.
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
    "build_candidates_with_archive",
    "apply_temporal_filter",
    "clear_candidate_cache",
    "candidate_index_tokens",
    "build_candidate_index",
    "retrieve_candidates_from_index",
    "coarse_candidate_score",
    "shortlist_candidates",
    "has_token_overlap",
    "compute_bm25_scores",
    "prefilter_candidates_by_scope_and_type",
    "compute_prior_score",
    "prior_tiebreak_factor",
    "apply_prior_tiebreak",
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

    index = build_candidate_index(use_cache=True)
    if not index.get("candidates"):
        return []

    shortlisted = retrieve_candidates_from_index(index, query_tokens, scope)

    now = now_utc()
    scored: list[ScoredResult] = []
    for candidate in shortlisted:
        sc, reasons, components = score_candidate(
            candidate,
            query_tokens,
            scope,
            False,
            False,
            False,
            now,
            [],
        )
        scored.append(_scored_result(candidate, sc, reasons, components))

    apply_prior_tiebreak(scored)

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

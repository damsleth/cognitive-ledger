"""Lexical operations for retrieval."""

from __future__ import annotations

from typing import Any
from ledger.scoring import COARSE_LADDER
from ledger.scoring import FINE_LADDER
from pathlib import Path
from ledger.retrieval_types import RetrievalResult
from ledger.retrieval_types import ScoreComponents
from ledger.retrieval_types import ScoredResult
from ledger.retrieval_types import TimingInfo
from ledger.scoring import scope_component as _scope_component
from ledger.scoring import clamp01
import datetime as dt
from ledger.config import get_config
import heapq
from ledger.scoring import intent_adjustments
from ledger.scoring import overlap_components
import time
from ledger.parsing import tokenize
from ledger.ranking.adapters import CandidateLike, _candidate_sort_key, _candidate_value, _scored_result
from ledger.ranking.candidates import _build_archive_candidates
from ledger.ranking.expansion import expand_query_tokens, load_aliases
from ledger.ranking.filters import has_token_overlap, apply_changed_since_filter, apply_temporal_filter, prefilter_candidates_by_scope_and_type
from ledger.ranking.index import build_candidate_index, build_candidates, retrieve_candidates_from_index
from ledger.ranking.prior import apply_prior_tiebreak, compute_prior_score, compute_recency_component, resolve_confidence
from ledger.ranking.settings import _cfg, now_utc, resolve_retrieval_mode
from ledger.ranking.telemetry import _maybe_log_query


try:
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - optional dependency fallback
    BM25Okapi = None



HISTORY_HINTS = frozenset({"history", "closed", "past"})



PREFERENCE_HINTS = frozenset({"preference", "preferences", "style", "workflow", "habit", "habits", "tools", "editors", "editor"})



LOOP_HINTS = frozenset({"loop", "loops", "unresolved", "next", "pending", "todo", "task", "tasks", "do"})



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

    # Signal feedback score. Query-independent (a note is "good"/"stale"
    # globally), so it is NOT added to the base score here; it is carried in
    # ScoreComponents.signal_score and folded in by apply_prior_tiebreak as a
    # TIE-BREAKER (same mechanism as the prior), so it can rerank near-tied
    # candidates without floating signalled-but-irrelevant notes above clear
    # winners.
    sig_score = 0.0
    if config.score_weight_signal > 0 and signal_summary is not None:
        rel_path = str(_candidate_value(candidate, "rel_path", "") or "")
        if rel_path:
            from ledger.signals import get_signal_score
            sig_score = get_signal_score(rel_path, summary=signal_summary)
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
        signal_score=sig_score,
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



def rank_lexical(
    query: str,
    scope: str = "all",
    limit: int = 8,
    aliases_path: Path | str | None = None,
    now_dt: dt.datetime | None = None,
    retrieval_mode: str = "legacy",
    as_of: dt.datetime | None = None,
    changed_since: dt.datetime | None = None,
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

    # Record-time window filter (--changed-since); no-op when None.
    candidates = apply_changed_since_filter(candidates, since=changed_since)
    prefiltered_candidates = apply_changed_since_filter(
        prefiltered_candidates, since=changed_since
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



_rank_query_lexical = rank_lexical



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


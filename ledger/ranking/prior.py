"""Prior operations for retrieval."""

from __future__ import annotations

from typing import Any
from ledger.retrieval_types import ScoredResult
from ledger.scoring import derive_provenance
import datetime as dt
from ledger.scoring import effective_confidence
from ledger.scoring import half_life_for_type
from ledger.ranking.adapters import CandidateLike, _candidate_value
from ledger.ranking.settings import _cfg


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
    from memcore.trust import attach_trust_verdicts as _attach

    validation_count_of = None
    contradiction_count_of = None
    if signal_summary is not None:
        from ledger.signals import get_contradiction_count, get_validation_count
        validation_count_of = lambda p: get_validation_count(p, summary=signal_summary)  # noqa: E731
        contradiction_count_of = lambda p: get_contradiction_count(p, summary=signal_summary)  # noqa: E731
    return _attach(
        results,
        confidence_of=lambda r: resolve_confidence(r, signal_summary),
        validation_count_of=validation_count_of,
        contradiction_count_of=contradiction_count_of,
    )



def apply_prior_tiebreak(ranked: list["ScoredResult"]) -> list["ScoredResult"]:
    """Blend the prior into base scores as a TIE-BREAKER, then re-sort.

    This is the single, shared place where the prior is folded into the final
    score for BOTH retrieval paths (lexical ``score_candidate`` and dense
    ``rank_query_semantic_hybrid``). Each input ``ScoredResult`` must carry:

    - ``score``: the BASE score (relevance + boosts); prior AND signal excluded.
    - ``components.prior_score``: the raw prior in [0, 1] (0 when disabled).
    - ``components.signal_score``: the raw signal in [-1, 1] (0 when disabled).

    Mechanism
    ---------
    1. Find the local leader's base score (the max base score in the pool).
    2. For each candidate compute ``factor = prior_tiebreak_factor(base,
       leader, config.prior_tie_band)`` — a continuous ramp from 1.0 (the
       candidate is at/near the leader) to 0.0 (its relative base gap exceeds
       the tie band). Candidates with a clear base-score lead over their
       followers keep their rank: a trailing candidate's prior is scaled to 0,
       so it cannot leapfrog a clear winner.
    3. Final score = ``base + factor * (prior_weight*prior_score
       + score_weight_signal*signal_score)``,
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
    signal_weight = float(config.score_weight_signal)
    tie_band = float(config.prior_tie_band)
    leader_base = max(item.score for item in ranked)
    for item in ranked:
        prior = float(getattr(item.components, "prior_score", 0.0) or 0.0)
        signal = float(getattr(item.components, "signal_score", 0.0) or 0.0)
        prior_active = prior > 0.0 and weight > 0.0
        signal_active = signal != 0.0 and signal_weight > 0.0
        if not (prior_active or signal_active):
            continue
        # Same tie factor gates BOTH the prior and the signal: a candidate with
        # a clear base lead keeps its rank (trailing candidates' bonuses scale to
        # 0), so neither can leapfrog a clear winner. The leader has factor 1.0,
        # so a negative signal still demotes a stale/corrected #1 at full force.
        factor = prior_tiebreak_factor(item.score, leader_base, tie_band)
        if factor <= 0.0:
            continue
        delta = 0.0
        if prior_active:
            delta += weight * factor * prior
        if signal_active:
            delta += signal_weight * factor * signal
        item.score = max(0.0, min(1.0, item.score + delta))
    ranked.sort(key=lambda item: (item.score, item.updated or "", item.path), reverse=True)
    return ranked


"""Tests for Mechanism 1 — prior score (cold-start ranking).

Covers:
- Zero-signal corpus: high-confidence recent relevant note ranks above
  stale low-confidence one.
- Identical notes differing only in age order by recency with half-life decay.
- Identity boost not double-counted (prior + identity_boost are independent).
- prior_enabled=False reproduces pre-change scores exactly (golden).
- compute_prior_score returns values in [0, 1].
- prior_recency half-life decay is mathematically correct.
- created_ts is preferred over updated_ts for age estimation in the prior.
"""

from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.config import LedgerConfig, set_config, reset_config
from ledger.retrieval import (
    apply_prior_tiebreak,
    compute_prior_score,
    prior_tiebreak_factor,
    score_candidate,
)
from ledger.retrieval_types import ScoreComponents, ScoredResult


@pytest.fixture(autouse=True)
def clean_config():
    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Fixed reference time
# ---------------------------------------------------------------------------
NOW = dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.timezone.utc)

# 10 days old — "recent"
RECENT_TS = NOW - dt.timedelta(days=10)
# 365 days old — "stale"
STALE_TS = NOW - dt.timedelta(days=365)
# 1 day old
FRESH_TS = NOW - dt.timedelta(days=1)
# 180 days old (= half-life)
HALF_LIFE_TS = NOW - dt.timedelta(days=180)
# 360 days old (= 2 × half-life)
DOUBLE_HALF_LIFE_TS = NOW - dt.timedelta(days=360)


_SENTINEL = object()


def _make_candidate(
    *,
    note_type: str = "fact",
    confidence: float = 0.9,
    updated_ts: dt.datetime | None = _SENTINEL,  # type: ignore[assignment]
    created_ts: dt.datetime | None = None,
    note_tokens: set[str] | None = None,
    tag_tokens: set[str] | None = None,
    scope: str = "all",
    status: str = "",
    rel_path: str = "notes/02_facts/fact__test.md",
) -> dict:
    """Build a minimal candidate dict for scoring tests.

    ``updated_ts`` defaults to NOW when not provided, mirroring real notes.
    Pass ``updated_ts=None`` explicitly to simulate a note with no timestamp.
    """
    if updated_ts is _SENTINEL:
        resolved_updated_ts: dt.datetime | None = NOW
    else:
        resolved_updated_ts = updated_ts  # type: ignore[assignment]

    updated_str = (
        resolved_updated_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        if resolved_updated_ts is not None
        else ""
    )
    return {
        "path": f"/tmp/{rel_path}",
        "rel_path": rel_path,
        "type": note_type,
        "title": "Test Note",
        "statement": "A test statement",
        "body": "Some body text",
        "updated": updated_str,
        "updated_ts": resolved_updated_ts,
        "created_ts": created_ts,
        "confidence": confidence,
        "source": "user",
        "scope": scope,
        "status": status,
        "tags": [],
        "note_tokens": note_tokens or {"test", "note"},
        "tag_tokens": tag_tokens or set(),
        "attention_tokens": set(),
        "snippet": "A test statement",
        "has_next_action_checkbox": False,
        "word_count": 5,
    }


# ---------------------------------------------------------------------------
# compute_prior_score unit tests
# ---------------------------------------------------------------------------


class TestComputePriorScore:
    """Unit tests for compute_prior_score."""

    def test_returns_float_in_unit_interval(self):
        """prior score must be in [0, 1]."""
        config = LedgerConfig()
        set_config(config)
        candidate = _make_candidate(confidence=0.9, updated_ts=RECENT_TS)
        score = compute_prior_score(candidate, now_dt=NOW, query_lexical_relevance=0.5)
        assert 0.0 <= score <= 1.0

    def test_high_confidence_scores_higher(self):
        """A note with confidence=1.0 should score higher than confidence=0.1."""
        config = LedgerConfig()
        set_config(config)
        high = _make_candidate(confidence=1.0, updated_ts=RECENT_TS)
        low = _make_candidate(confidence=0.1, updated_ts=RECENT_TS)
        assert compute_prior_score(high, NOW, 0.5) > compute_prior_score(low, NOW, 0.5)

    def test_recent_scores_higher_than_stale(self):
        """A recently created note should get higher prior recency than a stale one."""
        config = LedgerConfig()
        set_config(config)
        recent = _make_candidate(confidence=0.8, created_ts=RECENT_TS, updated_ts=RECENT_TS)
        stale = _make_candidate(confidence=0.8, created_ts=STALE_TS, updated_ts=STALE_TS)
        assert compute_prior_score(recent, NOW, 0.5) > compute_prior_score(stale, NOW, 0.5)

    def test_higher_relevance_scores_higher(self):
        """Higher query_lexical_relevance should push the prior up."""
        config = LedgerConfig()
        set_config(config)
        candidate = _make_candidate(confidence=0.7, updated_ts=RECENT_TS)
        low_rel = compute_prior_score(candidate, NOW, 0.0)
        high_rel = compute_prior_score(candidate, NOW, 1.0)
        assert high_rel > low_rel

    def test_half_life_decay_at_half_life(self):
        """At age == half_life_days, recency component should be ~0.5."""
        config = LedgerConfig()
        config.prior_w_importance = 0.0
        config.prior_w_relevance = 0.0
        config.prior_w_recency = 1.0
        config.prior_recency_half_life_days = 180.0
        set_config(config)

        candidate = _make_candidate(confidence=0.0, created_ts=HALF_LIFE_TS)
        score = compute_prior_score(candidate, NOW, 0.0)
        # At exactly the half-life, exp(-ln2) = 0.5
        assert abs(score - 0.5) < 1e-6, f"expected 0.5 got {score}"

    def test_double_half_life_decay(self):
        """At 2 × half_life_days, recency component should be ~0.25."""
        config = LedgerConfig()
        config.prior_w_importance = 0.0
        config.prior_w_relevance = 0.0
        config.prior_w_recency = 1.0
        config.prior_recency_half_life_days = 180.0
        set_config(config)

        candidate = _make_candidate(confidence=0.0, created_ts=DOUBLE_HALF_LIFE_TS)
        score = compute_prior_score(candidate, NOW, 0.0)
        assert abs(score - 0.25) < 1e-6, f"expected 0.25 got {score}"

    def test_created_ts_preferred_over_updated_ts(self):
        """created_ts should be used for age, not updated_ts."""
        config = LedgerConfig()
        config.prior_w_importance = 0.0
        config.prior_w_relevance = 0.0
        config.prior_w_recency = 1.0
        config.prior_recency_half_life_days = 180.0
        set_config(config)

        # updated_ts is fresh but created_ts is old — should use created_ts
        candidate_with_old_created = _make_candidate(
            confidence=0.0,
            created_ts=STALE_TS,
            updated_ts=FRESH_TS,
        )
        # no created_ts — falls back to updated_ts (fresh)
        candidate_no_created = _make_candidate(
            confidence=0.0,
            created_ts=None,
            updated_ts=FRESH_TS,
        )
        score_old_created = compute_prior_score(candidate_with_old_created, NOW, 0.0)
        score_fresh_updated = compute_prior_score(candidate_no_created, NOW, 0.0)
        assert score_old_created < score_fresh_updated

    def test_no_timestamp_gives_zero_recency(self):
        """A note with no timestamps should get 0 recency contribution."""
        config = LedgerConfig()
        config.prior_w_importance = 0.0
        config.prior_w_relevance = 0.0
        config.prior_w_recency = 1.0
        set_config(config)
        candidate = _make_candidate(confidence=0.0, created_ts=None, updated_ts=None)
        score = compute_prior_score(candidate, NOW, 0.0)
        assert score == 0.0


class TestPriorTiebreakFactor:
    """Unit tests for prior_tiebreak_factor — the continuous tie-band ramp."""

    def test_leader_gets_full_factor(self):
        """The leader (gap 0) gets factor 1.0."""
        assert prior_tiebreak_factor(0.9, 0.9, 0.05) == 1.0

    def test_beyond_band_gets_zero(self):
        """A candidate trailing by more than the band gets factor 0.0."""
        # leader 1.0, candidate 0.9 → relative gap 0.10 > band 0.05 → 0.0
        assert prior_tiebreak_factor(0.9, 1.0, 0.05) == 0.0

    def test_continuous_ramp_midpoint(self):
        """At half the band the factor is ~0.5 (continuous, not a cliff)."""
        # leader 1.0, candidate 0.975 → rel gap 0.025 = band/2 → factor 0.5
        f = prior_tiebreak_factor(0.975, 1.0, 0.05)
        assert abs(f - 0.5) < 1e-9, f

    def test_monotonic_decreasing_with_gap(self):
        """Factor decreases as the base-score gap widens."""
        f_small = prior_tiebreak_factor(0.99, 1.0, 0.05)
        f_large = prior_tiebreak_factor(0.97, 1.0, 0.05)
        assert f_small > f_large

    def test_zero_band_disables_prior(self):
        """A non-positive tie band disables the prior entirely (factor 0)."""
        assert prior_tiebreak_factor(1.0, 1.0, 0.0) == 0.0

    def test_degenerate_leader_returns_one(self):
        """A non-positive leader score falls back to factor 1.0."""
        assert prior_tiebreak_factor(0.0, 0.0, 0.05) == 1.0


# ---------------------------------------------------------------------------
# score_candidate integration tests
# ---------------------------------------------------------------------------


def _as_scored(candidate: dict, score: float, reasons, components) -> ScoredResult:
    """Wrap a scored candidate into a ScoredResult for apply_prior_tiebreak."""
    return ScoredResult(
        path=candidate["path"],
        rel_path=candidate["rel_path"],
        type=candidate.get("type", ""),
        title=candidate.get("title", ""),
        statement=candidate.get("statement", ""),
        body=candidate.get("body", ""),
        updated=candidate.get("updated", ""),
        updated_ts=candidate.get("updated_ts"),
        confidence=candidate.get("confidence", 0.0),
        source=candidate.get("source", ""),
        scope=candidate.get("scope", ""),
        status=candidate.get("status", ""),
        tags=candidate.get("tags", []),
        note_tokens=candidate.get("note_tokens", set()),
        tag_tokens=candidate.get("tag_tokens", set()),
        attention_tokens=candidate.get("attention_tokens", set()),
        snippet=candidate.get("snippet", ""),
        has_next_action_checkbox=candidate.get("has_next_action_checkbox", False),
        created_ts=candidate.get("created_ts"),
        score=score,
        reasons=list(reasons),
        components=components,
    )


def _final_scores(candidates: list[dict], config: LedgerConfig, query_tokens: set[str], bm25_score: float = 0.5) -> dict[str, float]:
    """Run the full base-score + prior-tiebreak pipeline; return rel_path->final score."""
    set_config(config)
    ranked = []
    for cand in candidates:
        sc, reasons, comps = score_candidate(
            cand, query_tokens, "all",
            False, False, False, NOW, [],
            include_reasons=True, bm25_score=bm25_score,
        )
        ranked.append(_as_scored(cand, sc, reasons, comps))
    apply_prior_tiebreak(ranked)
    return {r.rel_path: r.score for r in ranked}


class TestScoreCandidatePrior:
    """Integration tests for the prior term inside score_candidate."""

    def _score(
        self,
        candidate: dict,
        config: LedgerConfig,
        query_tokens: set[str] | None = None,
    ) -> tuple[float, list[str], ScoreComponents]:
        set_config(config)
        return score_candidate(
            candidate,
            query_tokens=query_tokens or {"test"},
            query_scope="all",
            history_mode=False,
            loop_mode=False,
            preference_mode=False,
            now_dt=NOW,
            expansion_events=[],
            include_reasons=True,
            bm25_score=0.5,
        )

    def test_prior_enabled_false_golden(self):
        """prior_enabled=False must reproduce pre-prior scores exactly."""
        candidate = _make_candidate(confidence=0.8, updated_ts=RECENT_TS)
        query_tokens = {"test", "note"}

        # Compute reference score with prior disabled
        config_off = LedgerConfig()
        config_off.prior_enabled = False
        score_off, _, _ = self._score(candidate, config_off, query_tokens)

        # Re-compute manually using the existing formula directly
        # (BM25=0.5, lexical=2/2=1.0, tag=0, scope=all→1.0, recency, confidence)
        recency = max(0.0, 1.0 - (10.0 / 90.0))
        expected = (
            config_off.score_weight_bm25 * 0.5
            + config_off.score_weight_lexical * 1.0
            + config_off.score_weight_tag * 0.0
            + config_off.score_weight_scope * 1.0
            + config_off.score_weight_recency * recency
            + config_off.score_weight_confidence * 0.8
        )
        expected = max(0.0, min(1.0, expected))
        assert abs(score_off - expected) < 1e-9, f"golden mismatch: {score_off} != {expected}"

    def test_prior_enabled_true_adds_to_score(self):
        """Enabling the prior should increase the *final* score for a high-quality
        note that is the local leader (the prior is now blended as a tie-breaker
        by apply_prior_tiebreak, not baked into score_candidate)."""
        candidate = _make_candidate(
            confidence=0.9,
            updated_ts=RECENT_TS,
            note_tokens={"test"},
        )
        query_tokens = {"test"}

        # Base score (no prior) is identical whether or not the prior is enabled.
        config_off = LedgerConfig()
        config_off.prior_enabled = False
        base_off = _final_scores([candidate], config_off, query_tokens)[candidate["rel_path"]]

        config_on = LedgerConfig()
        config_on.prior_enabled = True
        config_on.prior_weight = 0.10
        final_on = _final_scores([candidate], config_on, query_tokens)[candidate["rel_path"]]

        # As the sole (leader) candidate, the prior gets factor 1.0 and lifts it.
        assert final_on > base_off

        # The raw prior is still surfaced via reasons and components.
        _, reasons_on, components_on = self._score(candidate, config_on, query_tokens)
        assert any("prior=" in r for r in reasons_on)
        assert components_on.prior_score > 0.0

    def test_near_tied_candidates_ordered_by_prior(self):
        """TIE-BREAKER (a): two candidates with IDENTICAL base scores get ordered
        by their confidence/recency prior."""
        # Both notes share the same lexical tokens, scope, bm25 → identical base.
        query_tokens = {"test"}
        good = _make_candidate(
            confidence=0.95,
            updated_ts=RECENT_TS,
            created_ts=RECENT_TS,
            note_tokens={"test"},
        )
        bad = _make_candidate(
            confidence=0.20,
            updated_ts=RECENT_TS,
            created_ts=STALE_TS,
            note_tokens={"test"},
            rel_path="notes/02_facts/fact__bad.md",
        )
        config = LedgerConfig()
        config.prior_enabled = True
        config.prior_weight = 0.10
        scores = _final_scores([good, bad], config, query_tokens, bm25_score=0.5)
        assert scores[good["rel_path"]] > scores[bad["rel_path"]], (
            f"good={scores[good['rel_path']]:.4f} bad={scores[bad['rel_path']]:.4f}"
        )

    def test_clear_base_winner_keeps_rank_despite_low_prior(self):
        """TIE-BREAKER (b) — REGRESSION GUARD: a clear base-score winner with LOW
        confidence/recency must stay rank-1 even with the prior enabled.

        This is the exact case the flat-additive prior regressed: a strong
        relevance leader (high lexical overlap) but poor quality prior must not
        be displaced by a weak-relevance, high-quality note."""
        query_tokens = {"alpha", "beta", "gamma", "delta"}
        # Equal base recency (same updated_ts) and equal base confidence so the
        # BASE score difference comes purely from lexical overlap: the winner
        # matches all four query tokens, the loser only one — a clear base lead.
        winner = _make_candidate(
            confidence=0.5,
            updated_ts=NOW,
            created_ts=STALE_TS,  # poor prior recency
            note_tokens={"alpha", "beta", "gamma", "delta"},
            rel_path="notes/02_facts/fact__winner.md",
        )
        # Loser: weak relevance (one token) but a pristine prior (fresh created_ts).
        # Drive the prior with recency only so it does not leak into the base.
        loser = _make_candidate(
            confidence=0.5,
            updated_ts=NOW,
            created_ts=FRESH_TS,  # strong prior recency
            note_tokens={"alpha"},
            rel_path="notes/02_facts/fact__loser.md",
        )
        config = LedgerConfig()
        config.prior_enabled = True
        config.prior_weight = 0.10  # default weight — the tie band keeps it safe
        config.prior_w_importance = 0.0
        config.prior_w_relevance = 0.0
        config.prior_w_recency = 1.0
        scores = _final_scores([winner, loser], config, query_tokens, bm25_score=0.0)
        assert scores[winner["rel_path"]] > scores[loser["rel_path"]], (
            "Clear semantic/lexical winner must not be displaced by a high-prior "
            f"loser: winner={scores[winner['rel_path']]:.4f} "
            f"loser={scores[loser['rel_path']]:.4f}"
        )

    def test_same_note_different_age_orders_by_recency(self):
        """Two identical-base notes differing only in creation age order younger first
        (the prior breaks the tie by recency)."""
        query_tokens = {"test"}
        young = _make_candidate(
            confidence=0.8,
            created_ts=FRESH_TS,
            updated_ts=NOW,
            note_tokens={"test"},
        )
        old = _make_candidate(
            confidence=0.8,
            created_ts=STALE_TS,
            updated_ts=NOW,
            note_tokens={"test"},
            rel_path="notes/02_facts/fact__old.md",
        )
        config = LedgerConfig()
        config.prior_enabled = True
        config.prior_weight = 0.10
        # Make recency the dominant prior signal for this test
        config.prior_w_importance = 0.0
        config.prior_w_relevance = 0.0
        config.prior_w_recency = 1.0
        scores = _final_scores([young, old], config, query_tokens, bm25_score=0.3)
        assert scores[young["rel_path"]] > scores[old["rel_path"]], (
            f"young={scores[young['rel_path']]:.4f} old={scores[old['rel_path']]:.4f}"
        )

    def test_identity_boost_in_base_and_prior_independent(self):
        """The identity boost lives in the base score (score_candidate) while the
        prior is a separate component blended later by apply_prior_tiebreak.

        With identical confidence/age/relevance the identity and fact candidates
        get the same raw prior; the only score difference is the identity boost,
        which is part of the base score returned by score_candidate.
        """
        query_tokens = {"mission"}
        identity_candidate = _make_candidate(
            note_type="id",
            confidence=0.95,
            updated_ts=RECENT_TS,
            created_ts=RECENT_TS,
            note_tokens={"mission"},
            rel_path="notes/01_identity/id__mission.md",
        )
        fact_candidate = _make_candidate(
            note_type="fact",
            confidence=0.95,
            updated_ts=RECENT_TS,
            created_ts=RECENT_TS,
            note_tokens={"mission"},
            rel_path="notes/02_facts/fact__mission.md",
        )

        config_on = LedgerConfig()
        config_on.prior_enabled = True
        config_on.prior_weight = 0.10
        set_config(config_on)
        id_base, id_reasons, id_components = score_candidate(
            identity_candidate, query_tokens, "all",
            False, False, False, NOW, [], include_reasons=True, bm25_score=0.4,
        )
        fact_base, _, fact_components = score_candidate(
            fact_candidate, query_tokens, "all",
            False, False, False, NOW, [], include_reasons=True, bm25_score=0.4,
        )

        # Base score difference equals exactly the identity boost.
        assert abs((id_base - fact_base) - config_on.identity_score_boost) < 1e-9, (
            f"identity base Δ={id_base - fact_base:.6f} "
            f"boost={config_on.identity_score_boost:.6f}"
        )
        # The raw prior is identical (same confidence/age/relevance) and tracked separately.
        assert abs(id_components.prior_score - fact_components.prior_score) < 1e-9
        assert id_components.prior_score > 0.0
        # Identity boost appears in reasons; prior surfaced separately.
        assert any("identity_boost" in r for r in id_reasons)
        assert any("prior=" in r for r in id_reasons)

    def test_prior_weight_zero_has_no_effect(self):
        """prior_weight=0 must produce the same FINAL score as prior_enabled=False
        (verified through the full base + tie-break pipeline)."""
        candidate = _make_candidate(confidence=0.9, updated_ts=RECENT_TS)
        query_tokens = {"test"}

        config_off = LedgerConfig()
        config_off.prior_enabled = False
        score_off = _final_scores([candidate], config_off, query_tokens)[candidate["rel_path"]]

        config_zero = LedgerConfig()
        config_zero.prior_enabled = True
        config_zero.prior_weight = 0.0
        score_zero = _final_scores([candidate], config_zero, query_tokens)[candidate["rel_path"]]

        assert abs(score_off - score_zero) < 1e-9

    def test_prior_score_stored_in_components(self):
        """ScoreComponents.prior_score should be 0 when prior is disabled."""
        candidate = _make_candidate(confidence=0.9, updated_ts=RECENT_TS)
        config = LedgerConfig()
        config.prior_enabled = False
        set_config(config)
        _, _, components = score_candidate(
            candidate, {"test"}, "all",
            False, False, False, NOW, [],
            include_reasons=True, bm25_score=0.3,
        )
        assert components.prior_score == 0.0

    def test_prior_reason_absent_when_disabled(self):
        """'prior=' reason string should not appear when prior_enabled=False."""
        candidate = _make_candidate(confidence=0.9, updated_ts=RECENT_TS)
        config = LedgerConfig()
        config.prior_enabled = False
        set_config(config)
        _, reasons, _ = score_candidate(
            candidate, {"test"}, "all",
            False, False, False, NOW, [],
            include_reasons=True, bm25_score=0.3,
        )
        assert not any("prior=" in r for r in reasons)


# ---------------------------------------------------------------------------
# Semantic hybrid path: prior and signal scoring (acceptance criteria)
# ---------------------------------------------------------------------------


class TestSemanticHybridPriorAndSignal:
    """Acceptance tests: prior and signal terms applied in rank_query_semantic_hybrid.

    These tests use a fake embeddings module so no real index is needed.
    """

    def _make_note(self, path, confidence: float = 0.9, updated: str = "2026-01-01T00:00:00Z") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ncreated: 2025-06-01T00:00:00Z\nupdated: {updated}\n"
            f"tags: [test]\nconfidence: {confidence}\nsource: user\nscope: dev\nlang: en\n---\n\n# Note\n\nBody.\n",
            encoding="utf-8",
        )

    def _fake_embed_fn(self, scores: dict[str, float]):
        """Return a lambda that produces a fake semantic_score_map."""
        class FakeEmbeddings:
            @staticmethod
            def semantic_score_map(*, query, target, backend, model):
                return {
                    "available": True,
                    "index_item_count": len(scores),
                    "score_by_rel_path": scores,
                }
        return lambda: FakeEmbeddings()

    def test_prior_applied_in_semantic_hybrid(self, tmp_path):
        """Higher-confidence note should outscore equal-cosine lower-confidence note
        when prior is enabled (using cosine as the relevance proxy)."""
        from ledger import query as q
        from ledger.retrieval import clear_candidate_cache

        config_on = LedgerConfig(ledger_root=tmp_path)
        config_on.prior_enabled = True
        config_on.prior_weight = 0.15
        config_on.prior_w_importance = 1.0
        config_on.prior_w_recency = 0.0
        config_on.prior_w_relevance = 0.0

        note_high = config_on.ledger_notes_dir / "02_facts" / "fact__high.md"
        note_low = config_on.ledger_notes_dir / "02_facts" / "fact__low.md"
        self._make_note(note_high, confidence=0.9)
        self._make_note(note_low, confidence=0.1)

        set_config(config_on)
        clear_candidate_cache()
        try:
            result = q.rank_query_semantic_hybrid(
                "test query",
                scope="all",
                limit=10,
                now_dt=NOW,
                load_embeddings_module=self._fake_embed_fn({
                    "notes/02_facts/fact__high.md": 0.80,
                    "notes/02_facts/fact__low.md": 0.80,
                }),
                resolve_embed_model=lambda b, m: "fake",
            )
            scores = {r.rel_path: r.score for r in result.results}
            assert "notes/02_facts/fact__high.md" in scores
            assert "notes/02_facts/fact__low.md" in scores
            assert scores["notes/02_facts/fact__high.md"] > scores["notes/02_facts/fact__low.md"], (
                "High-confidence note should rank above low-confidence when prior is enabled "
                f"high={scores['notes/02_facts/fact__high.md']:.4f} "
                f"low={scores['notes/02_facts/fact__low.md']:.4f}"
            )
            # Reasons should mention prior for at least one result
            all_reasons = [r for result_item in result.results for r in result_item.reasons]
            assert any("prior=" in r for r in all_reasons), f"Prior missing from reasons: {all_reasons}"
        finally:
            clear_candidate_cache()
            reset_config()

    def test_prior_disabled_no_prior_reason_in_semantic_hybrid(self, tmp_path):
        """When prior_enabled=False, 'prior=' must not appear in reasons."""
        from ledger import query as q
        from ledger.retrieval import clear_candidate_cache

        config = LedgerConfig(ledger_root=tmp_path)
        config.prior_enabled = False

        note = config.ledger_notes_dir / "02_facts" / "fact__x.md"
        self._make_note(note)

        set_config(config)
        clear_candidate_cache()
        try:
            result = q.rank_query_semantic_hybrid(
                "test",
                scope="all",
                limit=5,
                now_dt=NOW,
                load_embeddings_module=self._fake_embed_fn({"notes/02_facts/fact__x.md": 0.85}),
                resolve_embed_model=lambda b, m: "fake",
            )
            all_reasons = [r for ri in result.results for r in ri.reasons]
            assert not any("prior=" in r for r in all_reasons), (
                f"Unexpected prior in reasons when prior_enabled=False: {all_reasons}"
            )
        finally:
            clear_candidate_cache()
            reset_config()

    def test_signal_applied_in_semantic_hybrid(self, tmp_path):
        """When a signal_summary with sufficient real signals is provided and
        score_weight_signal > 0, the note with a positive signal_score should
        rank above an equal-cosine note without signals."""
        import json
        from ledger import query as q
        from ledger.retrieval import clear_candidate_cache

        config = LedgerConfig(ledger_root=tmp_path)
        config.score_weight_signal = 0.20
        config.signal_min_entries = 0  # allow gate to open with 0 real signals
        config.prior_enabled = False

        note_sig = config.ledger_notes_dir / "02_facts" / "fact__sig.md"
        note_plain = config.ledger_notes_dir / "02_facts" / "fact__plain.md"
        self._make_note(note_sig)
        self._make_note(note_plain)

        # Write a signal_summary that gives fact__sig.md a positive score
        signal_summary = {
            "_meta": {"total_signals": 5, "real_signals": 5, "summarized_at": "2026-01-01T00:00:00Z"},
            "notes": {
                "notes/02_facts/fact__sig.md": {
                    "hit_count": 5.0,
                    "affirmations": 3.0,
                    "corrections": 0,
                    "stale_flags": 0,
                    "preference_applied": 0,
                    "rating_count": 0,
                    "rating_sum": 0,
                    "rating_min": None,
                    "rating_max": None,
                    "last_hit": "2026-01-01T00:00:00Z",
                    "synthetic_hits": 0,
                    "synthetic_corrections": 0,
                    "synthetic_affirmations": 0,
                    "signal_score": 0.75,
                }
            },
            "retrieval_misses": {},
        }
        config.signal_summary_path.parent.mkdir(parents=True, exist_ok=True)
        config.signal_summary_path.write_text(json.dumps(signal_summary), encoding="utf-8")

        set_config(config)
        clear_candidate_cache()
        try:
            result = q.rank_query_semantic_hybrid(
                "test",
                scope="all",
                limit=10,
                now_dt=NOW,
                load_embeddings_module=self._fake_embed_fn({
                    "notes/02_facts/fact__sig.md": 0.80,
                    "notes/02_facts/fact__plain.md": 0.80,
                }),
                resolve_embed_model=lambda b, m: "fake",
            )
            scores = {r.rel_path: r.score for r in result.results}
            assert "notes/02_facts/fact__sig.md" in scores
            assert "notes/02_facts/fact__plain.md" in scores
            assert scores["notes/02_facts/fact__sig.md"] > scores["notes/02_facts/fact__plain.md"], (
                "Signal-boosted note should outscore equal-cosine note without signals: "
                f"sig={scores['notes/02_facts/fact__sig.md']:.4f} "
                f"plain={scores['notes/02_facts/fact__plain.md']:.4f}"
            )
            all_reasons = [r for ri in result.results for r in ri.reasons]
            assert any("signal=" in r for r in all_reasons), (
                f"signal= missing from reasons: {all_reasons}"
            )
        finally:
            clear_candidate_cache()
            reset_config()

    def test_clear_winner_keeps_rank1_despite_low_prior(self, tmp_path):
        """REGRESSION (b) on the semantic_hybrid path: a clear cosine winner with
        LOW confidence must stay rank-1 even with the prior enabled at the
        default weight. This is the exact regression the tie-band design fixes."""
        from ledger import query as q
        from ledger.retrieval import clear_candidate_cache

        config = LedgerConfig(ledger_root=tmp_path)
        config.prior_enabled = True
        config.prior_weight = 0.10  # default
        config.prior_w_importance = 1.0  # confidence-dominated prior
        config.prior_w_recency = 0.0
        config.prior_w_relevance = 0.0

        # Clear winner: much higher cosine but LOW confidence.
        note_winner = config.ledger_notes_dir / "02_facts" / "fact__winner.md"
        # Distractor: lower cosine but pristine (high) confidence.
        note_distractor = config.ledger_notes_dir / "02_facts" / "fact__distractor.md"
        self._make_note(note_winner, confidence=0.05)
        self._make_note(note_distractor, confidence=1.0)

        set_config(config)
        clear_candidate_cache()
        try:
            result = q.rank_query_semantic_hybrid(
                "test query",
                scope="all",
                limit=10,
                now_dt=NOW,
                load_embeddings_module=self._fake_embed_fn({
                    "notes/02_facts/fact__winner.md": 0.92,       # clear lead
                    "notes/02_facts/fact__distractor.md": 0.70,
                }),
                resolve_embed_model=lambda b, m: "fake",
            )
            paths = [r.rel_path for r in result.results]
            assert paths[0] == "notes/02_facts/fact__winner.md", (
                f"Clear cosine winner must stay rank-1; got order {paths}"
            )
        finally:
            clear_candidate_cache()
            reset_config()

    def test_prior_disabled_reproduces_prechange_scores_exactly(self, tmp_path):
        """(c) prior_enabled=False must reproduce the pre-prior scores exactly:
        the final scores must equal those computed with prior_weight forced to 0,
        across a multi-note pool."""
        from ledger import query as q
        from ledger.retrieval import clear_candidate_cache

        cosines = {
            "notes/02_facts/fact__a.md": 0.92,
            "notes/02_facts/fact__b.md": 0.71,
            "notes/02_facts/fact__c.md": 0.55,
        }

        def _run(prior_enabled: bool):
            config = LedgerConfig(ledger_root=tmp_path)
            config.prior_enabled = prior_enabled
            config.prior_weight = 0.10
            for name, conf in (("a", 0.1), ("b", 0.95), ("c", 0.5)):
                self._make_note(
                    config.ledger_notes_dir / "02_facts" / f"fact__{name}.md",
                    confidence=conf,
                )
            set_config(config)
            clear_candidate_cache()
            try:
                result = q.rank_query_semantic_hybrid(
                    "test query",
                    scope="all",
                    limit=10,
                    now_dt=NOW,
                    load_embeddings_module=self._fake_embed_fn(cosines),
                    resolve_embed_model=lambda b, m: "fake",
                )
                return {r.rel_path: r.score for r in result.results}
            finally:
                clear_candidate_cache()
                reset_config()

        scores_off = _run(prior_enabled=False)
        # With prior disabled, no prior_score is set → tie-break is a pure no-op,
        # so scores must equal the base (pre-change) scores exactly.
        # Recompute base directly with prior_weight=0 to confirm equivalence.
        config_zero = LedgerConfig(ledger_root=tmp_path)
        config_zero.prior_enabled = True
        config_zero.prior_weight = 0.0
        for name, conf in (("a", 0.1), ("b", 0.95), ("c", 0.5)):
            self._make_note(
                config_zero.ledger_notes_dir / "02_facts" / f"fact__{name}.md",
                confidence=conf,
            )
        set_config(config_zero)
        clear_candidate_cache()
        try:
            result_zero = q.rank_query_semantic_hybrid(
                "test query",
                scope="all",
                limit=10,
                now_dt=NOW,
                load_embeddings_module=self._fake_embed_fn(cosines),
                resolve_embed_model=lambda b, m: "fake",
            )
            scores_zero = {r.rel_path: r.score for r in result_zero.results}
        finally:
            clear_candidate_cache()
            reset_config()

        assert scores_off.keys() == scores_zero.keys()
        for path in scores_off:
            assert abs(scores_off[path] - scores_zero[path]) < 1e-12, (
                f"{path}: prior-off {scores_off[path]} != weight-0 {scores_zero[path]}"
            )

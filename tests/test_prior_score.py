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
from ledger.retrieval import compute_prior_score, score_candidate
from ledger.retrieval_types import ScoreComponents


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


# ---------------------------------------------------------------------------
# score_candidate integration tests
# ---------------------------------------------------------------------------


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
        """Enabling the prior should increase the score for a high-quality note."""
        candidate = _make_candidate(
            confidence=0.9,
            updated_ts=RECENT_TS,
            note_tokens={"test"},
        )
        config_off = LedgerConfig()
        config_off.prior_enabled = False
        score_off, _, _ = self._score(candidate, config_off)

        config_on = LedgerConfig()
        config_on.prior_enabled = True
        config_on.prior_weight = 0.10
        score_on, reasons_on, components_on = self._score(candidate, config_on)

        assert score_on > score_off
        assert any("prior=" in r for r in reasons_on)
        assert components_on.prior_score > 0.0

    def test_high_confidence_recent_ranks_above_low_confidence_stale(self):
        """In a zero-signal corpus the high-quality note should rank higher."""
        # Both notes have the same lexical tokens → same BM25/lexical signals.
        query_tokens = {"test"}
        good = _make_candidate(
            confidence=0.95,
            updated_ts=RECENT_TS,
            created_ts=RECENT_TS,
            note_tokens={"test"},
        )
        bad = _make_candidate(
            confidence=0.20,
            updated_ts=STALE_TS,
            created_ts=STALE_TS,
            note_tokens={"test"},
            rel_path="notes/02_facts/fact__bad.md",
        )
        config = LedgerConfig()
        config.prior_enabled = True
        config.prior_weight = 0.10
        set_config(config)

        score_good, _, _ = score_candidate(
            good, query_tokens, "all",
            False, False, False, NOW, [],
            include_reasons=True, bm25_score=0.5,
        )
        score_bad, _, _ = score_candidate(
            bad, query_tokens, "all",
            False, False, False, NOW, [],
            include_reasons=True, bm25_score=0.5,
        )
        assert score_good > score_bad, f"good={score_good:.4f} bad={score_bad:.4f}"

    def test_same_note_different_age_orders_by_recency(self):
        """Two identical notes differing only in creation age must order younger first."""
        query_tokens = {"test"}
        young = _make_candidate(
            confidence=0.8,
            created_ts=FRESH_TS,
            updated_ts=FRESH_TS,
            note_tokens={"test"},
        )
        old = _make_candidate(
            confidence=0.8,
            created_ts=STALE_TS,
            updated_ts=STALE_TS,
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
        set_config(config)

        score_young, _, _ = score_candidate(
            young, query_tokens, "all",
            False, False, False, NOW, [],
            include_reasons=False, bm25_score=0.3,
        )
        score_old, _, _ = score_candidate(
            old, query_tokens, "all",
            False, False, False, NOW, [],
            include_reasons=False, bm25_score=0.3,
        )
        assert score_young > score_old, f"young={score_young:.4f} old={score_old:.4f}"

    def test_identity_boost_not_double_counted(self):
        """Identity boost and prior should both appear but be independently additive.

        Specifically: score_identity(prior_on) - score_identity(prior_off)
        should equal score_fact(prior_on) - score_fact(prior_off),
        confirming the prior adds the same delta regardless of type,
        and the identity boost is a separate, constant additive term.
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

        config_off = LedgerConfig()
        config_off.prior_enabled = False
        set_config(config_off)
        id_off, _, _ = score_candidate(
            identity_candidate, query_tokens, "all",
            False, False, False, NOW, [], include_reasons=True, bm25_score=0.4,
        )
        fact_off, _, _ = score_candidate(
            fact_candidate, query_tokens, "all",
            False, False, False, NOW, [], include_reasons=True, bm25_score=0.4,
        )

        config_on = LedgerConfig()
        config_on.prior_enabled = True
        config_on.prior_weight = 0.10
        set_config(config_on)
        id_on, id_reasons, id_components = score_candidate(
            identity_candidate, query_tokens, "all",
            False, False, False, NOW, [], include_reasons=True, bm25_score=0.4,
        )
        fact_on, _, _ = score_candidate(
            fact_candidate, query_tokens, "all",
            False, False, False, NOW, [], include_reasons=True, bm25_score=0.4,
        )

        # Prior delta should be the same for both (same confidence, age, relevance)
        delta_id = id_on - id_off
        delta_fact = fact_on - fact_off
        assert abs(delta_id - delta_fact) < 1e-9, (
            f"Prior delta differs by type: id Δ={delta_id:.6f} fact Δ={delta_fact:.6f}"
        )

        # Identity boost appears in reasons
        assert any("identity_boost" in r for r in id_reasons)
        # Prior appears in reasons too (they are separate)
        assert any("prior=" in r for r in id_reasons)
        # The prior score component is tracked in ScoreComponents
        assert id_components.prior_score > 0.0

    def test_prior_weight_zero_has_no_effect(self):
        """Setting prior_weight=0 should produce the same score as prior_enabled=False."""
        candidate = _make_candidate(confidence=0.9, updated_ts=RECENT_TS)
        query_tokens = {"test"}

        config_off = LedgerConfig()
        config_off.prior_enabled = False
        set_config(config_off)
        score_off, _, _ = score_candidate(
            candidate, query_tokens, "all",
            False, False, False, NOW, [], include_reasons=False, bm25_score=0.5,
        )

        config_zero = LedgerConfig()
        config_zero.prior_enabled = True
        config_zero.prior_weight = 0.0
        set_config(config_zero)
        score_zero, _, _ = score_candidate(
            candidate, query_tokens, "all",
            False, False, False, NOW, [], include_reasons=False, bm25_score=0.5,
        )

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

"""Unit tests for the trust verdict primitive (plan 46)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.scoring import TrustVerdict, trust_verdict


def _v(**kw) -> TrustVerdict:
    base = dict(
        effective_confidence=0.9,
        validation_count=0.0,
        contradicted=False,
        superseded=False,
        recency=0.5,
    )
    base.update(kw)
    return trust_verdict(**base)


class TestTrustVerdict:
    def test_superseded_is_low(self):
        v = _v(superseded=True, effective_confidence=0.95)
        assert v.level == "low"
        assert "supersed" in v.reason

    def test_contradicted_is_low_with_reason(self):
        v = _v(contradicted=True, effective_confidence=0.95)
        assert v.level == "low"
        assert "contradict" in v.reason

    def test_superseded_takes_precedence_over_contradicted(self):
        v = _v(superseded=True, contradicted=True)
        assert "supersed" in v.reason

    def test_high_confidence_affirmed_is_high(self):
        v = _v(effective_confidence=0.9, validation_count=3)
        assert v.level == "high"
        assert "affirmed 3×" in v.reason

    def test_high_confidence_unaffirmed_is_high(self):
        v = _v(effective_confidence=0.9, validation_count=0)
        assert v.level == "high"
        assert "affirmed" not in v.reason

    def test_moderate_unaffirmed_is_medium(self):
        v = _v(effective_confidence=0.7, validation_count=0)
        assert v.level == "medium"

    def test_low_confidence_is_low(self):
        v = _v(effective_confidence=0.3, recency=0.9)
        assert v.level == "low"
        assert v.reason == "low confidence"

    def test_low_confidence_stale(self):
        v = _v(effective_confidence=0.3, recency=0.05)
        assert v.level == "low"
        assert "stale" in v.reason

    def test_reason_is_nonempty_human_sentence(self):
        for kw in ({}, {"superseded": True}, {"effective_confidence": 0.7},
                   {"effective_confidence": 0.2}):
            assert _v(**kw).reason.strip()

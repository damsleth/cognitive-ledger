"""Tests for provenance-weighted effective confidence (plan 42).

Pure-function unit tests for the scoring primitives:
- provenance weights are applied to base confidence
- validation boost is additive and capped
- result is clamped to [0, 1]
- derive_provenance: explicit wins, else derived from source/via
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.scoring import (
    PROVENANCE_WEIGHTS,
    PROVENANCE_WEIGHT_FLOOR,
    derive_provenance,
    effective_confidence,
)

approx = lambda v: pytest.approx(v, abs=1e-9)  # noqa: E731


class TestEffectiveConfidence:
    def test_provenance_weights_applied(self):
        # explicit_statement (1.0) leaves base untouched; inferred (0.7) discounts.
        assert effective_confidence(0.8, "explicit_statement", 0.0) == approx(0.8)
        assert effective_confidence(0.8, "inferred", 0.0) == approx(0.8 * 0.70)

    def test_validation_boost_caps_at_015(self):
        # 10 validations × 0.03 = 0.30 raw, but capped at +0.15.
        base = effective_confidence(0.5, "explicit_statement", 0.0)
        boosted = effective_confidence(0.5, "explicit_statement", 10.0)
        assert boosted == approx(min(1.0, base + 0.15))
        # one validation adds exactly 0.03
        assert effective_confidence(0.5, "explicit_statement", 1.0) == approx(0.5 + 0.03)

    def test_effective_confidence_clamped_01(self):
        # base 1.0 × 1.0 + 0.15 boost would be 1.15 → clamps to 1.0
        assert effective_confidence(1.0, "explicit_statement", 10.0) == 1.0
        # negative / over-range base is clamped before weighting
        assert effective_confidence(-0.5, "explicit_statement", 0.0) == 0.0
        assert effective_confidence(2.0, "inferred", 0.0) == approx(0.70)

    def test_custom_boost_params(self):
        # boost_per_signal and boost_cap are honoured
        assert effective_confidence(0.5, "explicit_statement", 4.0,
                                    boost_per_signal=0.05, boost_cap=0.10) == approx(0.6)

    def test_unknown_provenance_uses_floor(self):
        assert effective_confidence(1.0, "nonsense", 0.0) == approx(PROVENANCE_WEIGHT_FLOOR)
        # floor equals the most conservative known weight
        assert PROVENANCE_WEIGHT_FLOOR == min(PROVENANCE_WEIGHTS.values())


class TestDeriveProvenance:
    def test_derive_provenance_from_source_user(self):
        assert derive_provenance("user", "", "") == "explicit_statement"

    def test_derive_provenance_from_source_inferred(self):
        assert derive_provenance("inferred", "", "") == "inferred"

    def test_derive_provenance_from_via_obsidian(self):
        # non-user source, imported via obsidian
        assert derive_provenance("assistant", "obsidian", "") == "imported"
        assert derive_provenance("", "claude-memory", "") == "imported"
        assert derive_provenance("", "folder", "") == "imported"

    def test_derive_provenance_default_observed(self):
        assert derive_provenance("assistant", "", "") == "observed"
        assert derive_provenance("", "", "") == "observed"

    def test_derive_provenance_explicit_wins(self):
        # an explicit, recognised value beats any source/via derivation
        assert derive_provenance("user", "obsidian", "validated") == "validated"
        # case-insensitive
        assert derive_provenance("user", "", "VALIDATED") == "validated"
        # unrecognised explicit value falls back to derivation
        assert derive_provenance("user", "", "bogus") == "explicit_statement"

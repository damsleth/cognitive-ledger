"""Regressions for the eight deferred findings from the repo-deep review.

Each test fails against the pre-fix code.
"""
from __future__ import annotations

import pytest

from ledger.scoring import scope_matches, trust_verdict
from ledger.validation import ScopeValidationError, validate_scope


class TestScopeAlias:
    """'life' is a documented alias for 'personal'; only the note side was
    canonicalised, so querying either spelling matched nothing."""

    @pytest.mark.parametrize(
        "note,query,expected",
        [
            ("personal", "life", True),
            ("life", "personal", True),
            ("life", "life", True),
            ("personal", "personal", True),
            ("dev", "life", False),
            ("personal", "all", True),
        ],
    )
    def test_alias_matches_both_directions(self, note, query, expected):
        assert scope_matches(note, query) is expected


class TestScopeValidation:
    """A scope typo must raise instead of silently returning an empty result set."""

    def test_alias_canonicalises(self):
        assert validate_scope("life") == "personal"

    def test_bad_scope_raises(self):
        with pytest.raises(ScopeValidationError):
            validate_scope("bogus")


class TestTrustVerdictAffirmations:
    """The medium branch hardcoded 'unaffirmed' regardless of validation_count."""

    _BASE = dict(contradicted=False, superseded=False, recency=0.9)

    def test_medium_with_affirmations_is_not_called_unaffirmed(self):
        v = trust_verdict(effective_confidence=0.70, validation_count=3, **self._BASE)
        assert v.level == "medium"
        assert "unaffirmed" not in v.reason
        assert "affirmed 3×" in v.reason

    def test_medium_without_affirmations_still_unaffirmed(self):
        v = trust_verdict(effective_confidence=0.70, validation_count=0, **self._BASE)
        assert v.reason == "moderate confidence, unaffirmed"

    def test_fractional_count_has_no_trailing_zero(self):
        v = trust_verdict(effective_confidence=0.90, validation_count=2.0, **self._BASE)
        assert "affirmed 2×" in v.reason

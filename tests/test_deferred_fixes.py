"""Regressions for the eight deferred findings from the repo-deep review.

Each test fails against the pre-fix code.
"""
from __future__ import annotations

import datetime as dt

import pytest

from ledger.mcp.server import _parse_date
from ledger.scoring import scope_matches, trust_verdict
from ledger.validation import ScopeValidationError, validate_scope


class TestParseDate:
    """_parse_date fell through to date.fromisoformat, which rejects times."""

    @pytest.mark.parametrize(
        "raw",
        [
            "2026-01-15T10:00:00Z",
            "2026-01-15T10:00:00+01:00",
            "2026-01-15T10:00:00.123Z",
            "2026-01-15T10:00:00",
            "2026-01-15",
        ],
    )
    def test_accepts_documented_iso_forms(self, raw):
        assert _parse_date(raw) is not None

    def test_naive_input_is_assumed_utc(self):
        assert _parse_date("2026-01-15T10:00:00").tzinfo == dt.timezone.utc

    def test_offset_is_preserved(self):
        assert _parse_date("2026-01-15T10:00:00+01:00").utcoffset() == dt.timedelta(hours=1)

    def test_empty_is_none(self):
        assert _parse_date("") is None and _parse_date(None) is None


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


class TestMcpScopeValidation:
    """The CLI validated scope; the MCP tools did not, so a typo silently
    returned an empty result set instead of an error."""

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

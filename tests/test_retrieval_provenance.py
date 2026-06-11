"""Integration tests for provenance-weighted confidence in retrieval (plan 42).

Covers:
- the `provenance`/`via` frontmatter fields are parsed into the candidate
- legacy notes (no `provenance`) fall back to a derived value
- with weighting disabled (default), resolve_confidence == raw confidence
- validation count is read from the signal summary and boosts effective confidence
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.config import LedgerConfig, set_config, reset_config
from ledger.retrieval import _candidate_from_parts, resolve_confidence

approx = lambda v: pytest.approx(v, abs=1e-9)  # noqa: E731


@pytest.fixture(autouse=True)
def clean_config():
    reset_config()
    yield
    reset_config()


def _candidate(*, confidence=0.8, source="user", via="", provenance="",
               rel_path="notes/02_facts/fact__x.md", validation_count=0.0) -> dict:
    return {
        "rel_path": rel_path,
        "type": "fact",
        "confidence": confidence,
        "source": source,
        "via": via,
        "provenance": provenance,
        "validation_count": validation_count,
    }


class TestCandidateParsing:
    def test_candidate_parses_provenance_field(self, tmp_path):
        body = "# Title\n\n## Statement\n\nA fact.\n"
        fm = {"confidence": 0.9, "source": "user", "provenance": "validated", "via": "yaams"}
        cand = _candidate_from_parts(tmp_path / "fact__x.md", "facts", fm, body)
        assert cand.provenance == "validated"
        assert cand.via == "yaams"

    def test_legacy_note_no_provenance_uses_derived(self, tmp_path):
        # No `provenance` in frontmatter — field is empty, derivation happens at
        # score time (not stored on the candidate).
        body = "# Title\n\n## Statement\n\nA fact.\n"
        fm = {"confidence": 0.9, "source": "user"}
        cand = _candidate_from_parts(tmp_path / "fact__x.md", "facts", fm, body)
        assert cand.provenance == ""
        # resolve_confidence derives explicit_statement (weight 1.0) → unchanged
        set_config(LedgerConfig(provenance_weighting_enabled=True))
        assert resolve_confidence(cand) == approx(0.9)


class TestResolveConfidence:
    def test_weighting_disabled_uses_raw_confidence(self):
        # Default flag off → effective == raw, regardless of provenance.
        set_config(LedgerConfig())  # provenance_weighting_enabled defaults False
        cand = _candidate(confidence=0.8, source="assistant", provenance="inferred")
        assert resolve_confidence(cand) == approx(0.8)

    def test_weighting_enabled_applies_provenance(self):
        set_config(LedgerConfig(provenance_weighting_enabled=True))
        cand = _candidate(confidence=0.8, source="assistant", provenance="inferred")
        assert resolve_confidence(cand) == approx(0.8 * 0.70)

    def test_validation_count_from_signal_summary(self):
        set_config(LedgerConfig(provenance_weighting_enabled=True))
        cand = _candidate(confidence=0.5, source="user", rel_path="notes/02_facts/fact__x.md")
        summary = {"notes": {"notes/02_facts/fact__x.md": {"affirmations": 3.0}}}
        # explicit_statement weight 1.0, + 3 × 0.03 boost = 0.5 + 0.09
        assert resolve_confidence(cand, signal_summary=summary) == approx(0.5 + 0.09)

    def test_validation_count_falls_back_to_candidate_when_no_summary(self):
        set_config(LedgerConfig(provenance_weighting_enabled=True))
        cand = _candidate(confidence=0.5, source="user", validation_count=2.0)
        # no summary → use candidate.validation_count (2 × 0.03 = 0.06)
        assert resolve_confidence(cand, signal_summary=None) == approx(0.5 + 0.06)

    def test_summary_overrides_candidate_snapshot(self):
        set_config(LedgerConfig(provenance_weighting_enabled=True))
        cand = _candidate(confidence=0.5, source="user", validation_count=99.0)
        summary = {"notes": {"notes/02_facts/fact__x.md": {"affirmations": 1.0}}}
        # live summary wins over the stale snapshot
        assert resolve_confidence(cand, signal_summary=summary) == approx(0.5 + 0.03)

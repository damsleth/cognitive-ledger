"""Surfacing + order-invariance tests for trust verdicts (plan 46)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.config import LedgerConfig, set_config, reset_config
from ledger.retrieval import attach_trust_verdicts
from ledger.retrieval_types import ScoreComponents, ScoredResult
from ledger.query import (
    format_query_results_human,
    scored_result_to_dict,
    _result_context_fields,
)


@pytest.fixture(autouse=True)
def clean_config():
    reset_config()
    yield
    reset_config()


def _result(rel_path: str, *, confidence: float, score: float, recency: float = 0.5,
            superseded_by: str = "", source: str = "user") -> ScoredResult:
    return ScoredResult(
        path=f"/tmp/{rel_path}",
        rel_path=rel_path,
        type="fact",
        title="t",
        statement="s",
        body="b",
        updated="",
        updated_ts=None,
        confidence=confidence,
        source=source,
        scope="all",
        status="",
        tags=[],
        note_tokens=set(),
        tag_tokens=set(),
        attention_tokens=set(),
        snippet="s",
        has_next_action_checkbox=False,
        superseded_by=superseded_by,
        score=score,
        components=ScoreComponents(recency=recency),
    )


class TestSurfacing:
    def test_attach_sets_trust(self):
        set_config(LedgerConfig())
        results = [_result("notes/a.md", confidence=0.9, score=0.8)]
        attach_trust_verdicts(results)
        assert results[0].trust is not None
        assert results[0].trust.level == "high"

    def test_disabled_attaches_nothing(self):
        set_config(LedgerConfig(show_trust_verdict=False))
        results = [_result("notes/a.md", confidence=0.9, score=0.8)]
        attach_trust_verdicts(results)
        assert results[0].trust is None

    def test_json_envelope_includes_trust_object(self):
        set_config(LedgerConfig())
        r = _result("notes/a.md", confidence=0.9, score=0.8)
        attach_trust_verdicts([r])
        payload = scored_result_to_dict(r)
        assert payload["trust"]["level"] == "high"
        assert payload["trust"]["reason"]
        ctx = _result_context_fields(r)
        assert ctx["trust"]["level"] == "high"

    def test_detail_view_includes_trust_line(self):
        set_config(LedgerConfig())
        r = _result("notes/a.md", confidence=0.3, score=0.8, recency=0.9)
        attach_trust_verdicts([r])
        payload = {"query": "q", "scope": "all", "retrieval_mode": "legacy",
                   "results": [scored_result_to_dict(r)]}
        text = format_query_results_human(payload, view="detail")
        assert "trust: low — low confidence" in text

    def test_superseded_surfaces_low(self):
        set_config(LedgerConfig())
        r = _result("notes/a.md", confidence=0.95, score=0.8,
                    superseded_by="notes/b.md")
        attach_trust_verdicts([r])
        assert r.trust.level == "low"
        assert "supersed" in r.trust.reason


class TestOrderInvariance:
    def test_trust_does_not_change_result_order(self):
        # Same pool, attach verdicts; the list order must be byte-identical.
        def pool():
            return [
                _result("notes/a.md", confidence=0.2, score=0.9, source="inferred"),
                _result("notes/b.md", confidence=0.95, score=0.5),
                _result("notes/c.md", confidence=0.6, score=0.7),
            ]
        baseline = [r.rel_path for r in pool()]
        set_config(LedgerConfig())
        with_trust = pool()
        attach_trust_verdicts(with_trust)
        assert [r.rel_path for r in with_trust] == baseline

    def test_falls_back_to_raw_confidence_when_provenance_disabled(self):
        # provenance weighting OFF (default): a low-trust source must NOT discount
        # the verdict — raw confidence 0.9 → high.
        set_config(LedgerConfig(provenance_weighting_enabled=False))
        r = _result("notes/a.md", confidence=0.9, score=0.8, source="inferred")
        attach_trust_verdicts([r])
        assert r.trust.level == "high"

        # provenance weighting ON: inferred (×0.70) drops 0.9 → 0.63 → medium
        reset_config()
        set_config(LedgerConfig(provenance_weighting_enabled=True))
        r2 = _result("notes/a.md", confidence=0.9, score=0.8, source="inferred")
        attach_trust_verdicts([r2])
        assert r2.trust.level == "medium"

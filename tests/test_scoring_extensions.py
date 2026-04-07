"""Tests for identity boost and signal scoring in retrieval."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.config import LedgerConfig, set_config, reset_config
from ledger.retrieval import score_candidate
from ledger.retrieval_types import ScoreComponents


@pytest.fixture(autouse=True)
def clean_config():
    """Reset config singleton between tests."""
    reset_config()
    yield
    reset_config()


NOW = dt.datetime(2026, 4, 7, 12, 0, 0, tzinfo=dt.timezone.utc)

IDENTITY_CANDIDATE = {
    "path": "/tmp/notes/01_identity/id__mission.md",
    "rel_path": "notes/01_identity/id__mission.md",
    "type": "id",
    "title": "Mission",
    "statement": "My north star",
    "body": "My north star is to build useful tools",
    "updated": "2026-04-01T10:00:00Z",
    "updated_ts": dt.datetime(2026, 4, 1, 10, 0, 0, tzinfo=dt.timezone.utc),
    "confidence": 0.95,
    "source": "user",
    "scope": "personal",
    "status": "",
    "tags": ["identity", "mission"],
    "note_tokens": {"mission", "north", "star", "build", "useful", "tools"},
    "tag_tokens": {"identity", "mission"},
    "attention_tokens": set(),
    "snippet": "My north star",
    "has_next_action_checkbox": False,
}

FACT_CANDIDATE = {
    **IDENTITY_CANDIDATE,
    "path": "/tmp/notes/02_facts/fact__k8s.md",
    "rel_path": "notes/02_facts/fact__k8s.md",
    "type": "fact",
    "title": "K8s Deploy",
    "statement": "We use k8s for deploys",
    "body": "We use k8s for deploys",
    "tags": ["deploy", "k8s"],
    "note_tokens": {"k8s", "deploy", "mission"},
    "tag_tokens": {"deploy", "k8s"},
}


class TestIdentityBoost:
    def test_identity_candidate_gets_boost(self):
        """Identity notes should score higher than equivalent non-identity notes."""
        config = LedgerConfig()
        set_config(config)

        query_tokens = {"mission", "north", "star"}

        score_id, reasons_id, _ = score_candidate(
            IDENTITY_CANDIDATE, query_tokens, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
        )
        score_fact, reasons_fact, _ = score_candidate(
            FACT_CANDIDATE, query_tokens, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
        )

        assert score_id > score_fact
        assert any("identity_boost" in r for r in reasons_id)
        assert not any("identity_boost" in r for r in reasons_fact)

    def test_identity_boost_uses_config_value(self):
        """The boost amount should come from config."""
        config = LedgerConfig()
        config.identity_score_boost = 0.30
        set_config(config)

        query_tokens = {"mission"}
        score_default, _, _ = score_candidate(
            IDENTITY_CANDIDATE, query_tokens, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
        )

        config2 = LedgerConfig()
        config2.identity_score_boost = 0.0
        set_config(config2)

        score_no_boost, _, _ = score_candidate(
            IDENTITY_CANDIDATE, query_tokens, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
        )

        assert score_default > score_no_boost


class TestSignalScoring:
    def test_signal_score_applied_when_enabled(self):
        """Signal score should be blended when weight > 0 and summary provided."""
        config = LedgerConfig()
        config.score_weight_signal = 0.10
        set_config(config)

        signal_summary = {
            "_meta": {"total_signals": 50},
            "notes": {
                "notes/02_facts/fact__k8s.md": {"signal_score": 0.8},
            },
        }

        score_with, reasons_with, _ = score_candidate(
            FACT_CANDIDATE, {"deploy"}, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
            signal_summary=signal_summary,
        )
        score_without, _, _ = score_candidate(
            FACT_CANDIDATE, {"deploy"}, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
            signal_summary=None,
        )

        assert score_with > score_without
        assert any("signal=" in r for r in reasons_with)

    def test_signal_score_not_applied_when_weight_zero(self):
        """Signal score should be ignored when weight is 0."""
        config = LedgerConfig()
        config.score_weight_signal = 0.0
        set_config(config)

        signal_summary = {
            "_meta": {"total_signals": 50},
            "notes": {
                "notes/02_facts/fact__k8s.md": {"signal_score": 0.8},
            },
        }

        score_with, reasons, _ = score_candidate(
            FACT_CANDIDATE, {"deploy"}, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
            signal_summary=signal_summary,
        )
        score_without, _, _ = score_candidate(
            FACT_CANDIDATE, {"deploy"}, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
            signal_summary=None,
        )

        assert score_with == score_without
        assert not any("signal=" in r for r in reasons)

    def test_negative_signal_demotes(self):
        """A note with negative signal score should score lower."""
        config = LedgerConfig()
        config.score_weight_signal = 0.10
        set_config(config)

        signal_summary = {
            "_meta": {"total_signals": 50},
            "notes": {
                "notes/02_facts/fact__k8s.md": {"signal_score": -0.75},
            },
        }

        score_demoted, _, _ = score_candidate(
            FACT_CANDIDATE, {"deploy"}, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
            signal_summary=signal_summary,
        )
        score_neutral, _, _ = score_candidate(
            FACT_CANDIDATE, {"deploy"}, "all",
            history_mode=False, loop_mode=False, preference_mode=False,
            now_dt=NOW, expansion_events=[], bm25_score=0.5,
            signal_summary=None,
        )

        assert score_demoted < score_neutral

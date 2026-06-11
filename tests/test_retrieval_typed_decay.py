"""Integration tests for per-type recency decay in the prior (plan 43)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.config import LedgerConfig, set_config, reset_config
from ledger.retrieval import compute_prior_score

approx = lambda v: pytest.approx(v, abs=1e-6)  # noqa: E731

NOW = dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def clean_config():
    reset_config()
    yield
    reset_config()


def _candidate(note_type: str, age_days: int, confidence: float = 0.0) -> dict:
    created = NOW - dt.timedelta(days=age_days)
    return {
        "rel_path": f"notes/{note_type}.md",
        "type": note_type,
        "confidence": confidence,
        "created_ts": created,
        "updated_ts": created,
    }


class TestTypedDecay:
    def test_empty_by_type_is_behavior_neutral(self):
        # With no per-type map, the prior is identical to the global-half-life path.
        set_config(LedgerConfig())  # recency_half_life_by_type == {}
        fact = _candidate("fact", age_days=180)
        # global half-life is 180d → recency 0.5 → prior = 0.30 * 0.5 = 0.15
        assert compute_prior_score(fact, now_dt=NOW) == approx(0.15)

    def test_preference_decays_faster_than_fact(self):
        # Same age; prefs half-life 90d, facts 365d → fact keeps more recency.
        set_config(LedgerConfig(recency_half_life_by_type={"pref": 90.0, "fact": 365.0}))
        age = 180
        pref_prior = compute_prior_score(_candidate("pref", age_days=age), now_dt=NOW)
        fact_prior = compute_prior_score(_candidate("fact", age_days=age), now_dt=NOW)
        assert fact_prior > pref_prior

    def test_override_matches_manual_decay_math(self):
        import math
        set_config(LedgerConfig(recency_half_life_by_type={"pref": 90.0}))
        age = 90  # exactly the pref half-life → recency 0.5
        prior = compute_prior_score(_candidate("pref", age_days=age), now_dt=NOW)
        expected_recency = math.exp(-math.log(2.0) / 90.0 * 90.0)  # 0.5
        assert prior == approx(0.30 * expected_recency)

    def test_env_parser_type_days(self, monkeypatch):
        monkeypatch.setenv("LEDGER_HALF_LIFE_BY_TYPE", "preferences:90,facts:365")
        cfg = LedgerConfig.from_env()
        assert cfg.recency_half_life_by_type == {"preferences": 90.0, "facts": 365.0}

    def test_env_parser_ignores_malformed_pairs(self, monkeypatch):
        monkeypatch.setenv("LEDGER_HALF_LIFE_BY_TYPE", "facts:365,bogus,loops:nan-ish")
        cfg = LedgerConfig.from_env()
        # only the well-formed pair survives
        assert cfg.recency_half_life_by_type == {"facts": 365.0}

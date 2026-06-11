"""Tests for the briefing 'changed since last briefing' window (plan 47C)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.config import LedgerConfig, set_config, reset_config
from ledger import briefing as briefing_lib

NOW = dt.datetime(2026, 6, 11, 12, 0, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def ledger(tmp_path):
    notes = tmp_path / "notes"
    (notes / "08_indices").mkdir(parents=True)
    cfg = LedgerConfig(ledger_root=tmp_path, ledger_notes_dir=notes)
    set_config(cfg)
    yield cfg
    reset_config()


class TestWindowStart:
    def test_first_run_falls_back_to_24h(self, ledger):
        since, label = briefing_lib._changes_window_start(NOW)
        assert label == "last 24h"
        assert since == NOW - dt.timedelta(hours=24)

    def test_uses_persisted_last_run(self, ledger):
        briefing_lib._save_briefing_state(
            {"last_briefing_ts": "2026-06-08T12:00:00Z"}
        )
        since, label = briefing_lib._changes_window_start(NOW)
        assert since == dt.datetime(2026, 6, 8, 12, 0, 0, tzinfo=dt.timezone.utc)
        assert label == "since last briefing (3d)"

    def test_same_day_label(self, ledger):
        briefing_lib._save_briefing_state(
            {"last_briefing_ts": "2026-06-11T06:00:00Z"}
        )
        _, label = briefing_lib._changes_window_start(NOW)
        assert label == "since last briefing (~24h)"

    def test_corrupt_state_falls_back(self, ledger):
        briefing_lib._save_briefing_state({"last_briefing_ts": "not-a-date"})
        _, label = briefing_lib._changes_window_start(NOW)
        assert label == "last 24h"


class TestBriefingPersistsState:
    def test_daily_briefing_records_last_run(self, ledger):
        # Empty corpus is fine; we only assert the state file is written.
        briefing_lib.daily_briefing()
        state = briefing_lib._load_briefing_state()
        assert "last_briefing_ts" in state
        assert state["last_briefing_ts"].endswith("Z")

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


class TestLoopStaleness:
    """`updated:` is bulk-stamped, so staleness reads the timeline instead."""

    class _Loop:
        def __init__(self, path: Path, updated: str):
            self.path = path
            self.updated = updated

    def _loop(self, ledger, name: str, updated: str):
        path = ledger.ledger_notes_dir / "05_open_loops" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
        return self._Loop(path, updated)

    def _timeline(self, ledger, lines: list[str]) -> dict[str, str]:
        ledger.timeline_jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        ledger.timeline_jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return briefing_lib._note_last_activity(ledger.timeline_jsonl_path)

    def test_bulk_touched_updated_does_not_mask_real_staleness(self, ledger):
        """The whole point: a fresh `updated:` must not hide an idle loop."""
        loop = self._loop(ledger, "loop__idle.md", "2026-06-10T00:00:00Z")
        activity = self._timeline(ledger, [
            '{"ts":"2026-05-12T00:00:00Z","action":"updated",'
            '"path":"notes/05_open_loops/loop__idle.md","desc":"real work"}',
        ])
        assert briefing_lib._loop_staleness(loop, NOW) == 1
        assert briefing_lib._loop_staleness(loop, NOW, activity) == 30

    def test_latest_event_wins(self, ledger):
        loop = self._loop(ledger, "loop__busy.md", "2026-01-01T00:00:00Z")
        activity = self._timeline(ledger, [
            '{"ts":"2026-05-12T00:00:00Z","action":"updated",'
            '"path":"notes/05_open_loops/loop__busy.md","desc":"older"}',
            '{"ts":"2026-06-09T00:00:00Z","action":"updated",'
            '"path":"notes/05_open_loops/loop__busy.md","desc":"newer"}',
        ])
        assert briefing_lib._loop_staleness(loop, NOW, activity) == 2

    def test_note_without_timeline_events_falls_back_to_updated(self, ledger):
        loop = self._loop(ledger, "loop__unlogged.md", "2026-06-04T00:00:00Z")
        activity = self._timeline(ledger, [
            '{"ts":"2026-06-10T00:00:00Z","action":"sleep","path":"-","desc":"done"}',
        ])
        assert briefing_lib._loop_staleness(loop, NOW, activity) == 7

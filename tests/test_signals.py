"""Tests for ledger.signals module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ledger.signals import (
    SIGNAL_TYPES,
    append_signal,
    get_signal_score,
    load_signal_summary,
    read_signals,
    signal_stats,
    summarize_signals,
    write_summary,
)


@pytest.fixture
def signals_dir():
    """Create a temp directory for signal files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        indices = Path(tmpdir) / "notes" / "08_indices"
        indices.mkdir(parents=True)
        yield Path(tmpdir)


@pytest.fixture
def signals_path(signals_dir):
    return signals_dir / "notes" / "08_indices" / "signals.jsonl"


@pytest.fixture
def mock_config(signals_dir):
    """Patch get_config to use temp paths."""
    from ledger.config import LedgerConfig
    config = LedgerConfig()
    config.root_dir = signals_dir
    with patch("ledger.signals.get_config", return_value=config):
        yield config


class TestAppendSignal:
    def test_append_valid_signal(self, mock_config):
        entry = append_signal("retrieval_hit", query="test", note="notes/02_facts/fact__x.md")
        assert entry["type"] == "retrieval_hit"
        assert entry["query"] == "test"
        assert entry["note"] == "notes/02_facts/fact__x.md"
        assert "ts" in entry

    def test_append_invalid_type_raises(self, mock_config):
        with pytest.raises(ValueError, match="Unknown signal type"):
            append_signal("invalid_type")

    def test_append_rating_clamped(self, mock_config):
        entry = append_signal("rating", rating=15)
        assert entry["rating"] == 10

        entry = append_signal("rating", rating=-5)
        assert entry["rating"] == 1

    def test_append_creates_file(self, mock_config):
        append_signal("affirmation", note="notes/02_facts/fact__x.md")
        path = mock_config.signals_path
        assert path.is_file()
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["type"] == "affirmation"

    def test_append_multiple_signals(self, mock_config):
        append_signal("retrieval_hit", query="a", note="note_a")
        append_signal("correction", note="note_b", detail="wrong")
        append_signal("retrieval_miss", query="c")
        path = mock_config.signals_path
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 3

    def test_omits_empty_fields(self, mock_config):
        entry = append_signal("retrieval_miss")
        assert "query" not in entry
        assert "note" not in entry
        assert "detail" not in entry


class TestReadSignals:
    def test_read_empty_file(self, signals_path):
        assert read_signals(signals_path=signals_path) == []

    def test_read_nonexistent_file(self):
        assert read_signals(signals_path=Path("/nonexistent/signals.jsonl")) == []

    def test_read_with_type_filter(self, signals_path):
        entries = [
            {"ts": "2026-04-07T10:00:00Z", "type": "retrieval_hit", "note": "a"},
            {"ts": "2026-04-07T10:01:00Z", "type": "correction", "note": "b"},
            {"ts": "2026-04-07T10:02:00Z", "type": "retrieval_hit", "note": "c"},
        ]
        signals_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        result = read_signals(type_filter="correction", signals_path=signals_path)
        assert len(result) == 1
        assert result[0]["note"] == "b"

    def test_read_with_since_filter(self, signals_path):
        entries = [
            {"ts": "2026-04-01T10:00:00Z", "type": "retrieval_hit", "note": "old"},
            {"ts": "2026-04-07T10:00:00Z", "type": "retrieval_hit", "note": "new"},
        ]
        signals_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        result = read_signals(since="2026-04-05T00:00:00Z", signals_path=signals_path)
        assert len(result) == 1
        assert result[0]["note"] == "new"

    def test_skips_invalid_json_lines(self, signals_path):
        signals_path.write_text(
            '{"ts": "2026-04-07T10:00:00Z", "type": "retrieval_hit", "note": "a"}\n'
            'not valid json\n'
            '{"ts": "2026-04-07T10:01:00Z", "type": "correction", "note": "b"}\n'
        )
        result = read_signals(signals_path=signals_path)
        assert len(result) == 2


class TestSummarizeSignals:
    def test_empty(self, signals_path):
        summary = summarize_signals(signals_path=signals_path)
        assert summary["_meta"]["total_signals"] == 0
        assert summary["notes"] == {}

    def test_hit_count_and_affirmations(self, signals_path):
        entries = [
            {"ts": "2026-04-07T10:00:00Z", "type": "retrieval_hit", "note": "note_a", "query": "q"},
            {"ts": "2026-04-07T10:01:00Z", "type": "retrieval_hit", "note": "note_a", "query": "q2"},
            {"ts": "2026-04-07T10:02:00Z", "type": "affirmation", "note": "note_a"},
        ]
        signals_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        summary = summarize_signals(signals_path=signals_path)
        stats = summary["notes"]["note_a"]
        assert stats["hit_count"] == 2
        assert stats["affirmations"] == 1
        assert stats["signal_score"] > 0

    def test_negative_score_without_hits(self, signals_path):
        """Notes with only corrections/stale_flags should get a negative score."""
        entries = [
            {"ts": "2026-04-07T10:00:00Z", "type": "correction", "note": "bad_note"},
            {"ts": "2026-04-07T10:01:00Z", "type": "correction", "note": "bad_note"},
            {"ts": "2026-04-07T10:02:00Z", "type": "stale_flag", "note": "bad_note"},
        ]
        signals_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        summary = summarize_signals(signals_path=signals_path)
        stats = summary["notes"]["bad_note"]
        assert stats["signal_score"] < 0
        assert stats["hit_count"] == 0
        assert stats["corrections"] == 2
        assert stats["stale_flags"] == 1

    def test_ratings_aggregated(self, signals_path):
        entries = [
            {"ts": "2026-04-07T10:00:00Z", "type": "rating", "note": "note_a", "rating": 3},
            {"ts": "2026-04-07T10:01:00Z", "type": "rating", "note": "note_a", "rating": 9},
            {"ts": "2026-04-07T10:02:00Z", "type": "rating", "note": "note_a", "rating": 6},
        ]
        signals_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        summary = summarize_signals(signals_path=signals_path)
        stats = summary["notes"]["note_a"]
        assert stats["rating_count"] == 3
        assert stats["rating_sum"] == 18
        assert stats["rating_min"] == 3
        assert stats["rating_max"] == 9
        # No unbounded list
        assert "ratings" not in stats

    def test_retrieval_misses_tracked(self, signals_path):
        entries = [
            {"ts": "2026-04-07T10:00:00Z", "type": "retrieval_miss", "query": "deploy"},
            {"ts": "2026-04-07T10:01:00Z", "type": "retrieval_miss", "query": "deploy"},
            {"ts": "2026-04-07T10:02:00Z", "type": "retrieval_miss", "query": "oauth"},
        ]
        signals_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        summary = summarize_signals(signals_path=signals_path)
        assert summary["retrieval_misses"]["deploy"] == 2
        assert summary["retrieval_misses"]["oauth"] == 1

    def test_accepts_preloaded_signals(self, signals_path):
        """summarize_signals() should use pre-loaded signals when provided."""
        signals = [
            {"ts": "2026-04-07T10:00:00Z", "type": "retrieval_hit", "note": "n", "query": "q"},
        ]
        summary = summarize_signals(signals=signals)
        assert summary["_meta"]["total_signals"] == 1
        assert summary["notes"]["n"]["hit_count"] == 1


class TestLoadSignalSummary:
    def test_missing_file_returns_empty(self):
        result = load_signal_summary(summary_path=Path("/nonexistent/summary.json"))
        assert result["_meta"]["total_signals"] == 0
        assert result["notes"] == {}

    def test_loads_valid_summary(self, signals_dir):
        summary_path = signals_dir / "notes" / "08_indices" / "signal_summary.json"
        data = {"_meta": {"total_signals": 5}, "notes": {"x": {"signal_score": 0.5}}, "retrieval_misses": {}}
        summary_path.write_text(json.dumps(data))
        result = load_signal_summary(summary_path=summary_path)
        assert result["_meta"]["total_signals"] == 5

    def test_corrupt_file_returns_empty(self, signals_dir):
        summary_path = signals_dir / "notes" / "08_indices" / "signal_summary.json"
        summary_path.write_text("not json")
        result = load_signal_summary(summary_path=summary_path)
        assert result["_meta"]["total_signals"] == 0


class TestGetSignalScore:
    def test_returns_score_from_summary(self):
        summary = {"notes": {"note_a": {"signal_score": 0.42}}}
        assert get_signal_score("note_a", summary=summary) == 0.42

    def test_returns_zero_for_unknown_note(self):
        summary = {"notes": {}}
        assert get_signal_score("nonexistent", summary=summary) == 0.0


class TestSignalStats:
    def test_stats_no_double_read(self, signals_path, monkeypatch):
        """signal_stats should read the JSONL only once."""
        entries = [
            {"ts": "2026-04-07T10:00:00Z", "type": "retrieval_hit", "note": "n", "query": "q"},
        ]
        signals_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        call_count = 0
        original_read = read_signals

        def counting_read(**kwargs):
            nonlocal call_count
            call_count += 1
            return original_read(**kwargs)

        monkeypatch.setattr("ledger.signals.read_signals", counting_read)
        signal_stats(signals_path=signals_path)
        assert call_count == 1, f"read_signals called {call_count} times, expected 1"

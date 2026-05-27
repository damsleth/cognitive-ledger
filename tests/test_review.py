"""Tests for ledger.review — queue prioritization, verdicts, flush, dashboard."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ledger.browse import BrowseItem
from ledger.review import (
    ReviewItem,
    build_review_queue,
    dashboard_data,
    flush_session,
    score_item,
    verdict_signal,
)


def _item(rel_path: str = "notes/02_facts/fact__x.md") -> ReviewItem:
    return ReviewItem(
        stem="fact__x",
        note_type="facts",
        rel_path=rel_path,
        title="X",
        body="body",
        frontmatter={},
    )


# ---------------------------------------------------------------------------
# verdict_signal — keystroke -> append_signal kwargs
# ---------------------------------------------------------------------------


class TestVerdictSignal:
    def test_keep_maps_to_affirmation(self):
        kw = verdict_signal(_item(), "k")
        assert kw == {"note": "notes/02_facts/fact__x.md", "signal_type": "affirmation"}

    def test_wrong_maps_to_correction_with_detail(self):
        kw = verdict_signal(_item(), "w", detail="outdated")
        assert kw["signal_type"] == "correction"
        assert kw["detail"] == "outdated"

    def test_wrong_without_detail_omits_detail(self):
        kw = verdict_signal(_item(), "w")
        assert kw["signal_type"] == "correction"
        assert "detail" not in kw

    def test_stale_maps_to_stale_flag(self):
        assert verdict_signal(_item(), "s")["signal_type"] == "stale_flag"

    def test_digits_map_to_rating(self):
        kw = verdict_signal(_item(), "7")
        assert kw["signal_type"] == "rating"
        assert kw["rating"] == 7

    def test_skip_keys_return_none(self):
        assert verdict_signal(_item(), " ") is None
        assert verdict_signal(_item(), "x") is None

    def test_session_passed_through(self):
        kw = verdict_signal(_item(), "k", session="sess1")
        assert kw["session"] == "sess1"


# ---------------------------------------------------------------------------
# score_item — priority + reasons
# ---------------------------------------------------------------------------


class TestScoreItem:
    def test_never_reviewed_gets_base_priority(self):
        priority, reasons = score_item(None, {}, stale_days=180, min_confidence=0.8)
        assert priority == 3.0
        assert "never reviewed" in reasons

    def test_pending_correction_dominates(self):
        stats = {"corrections": 2, "hit_count": 0, "affirmations": 0}
        priority, reasons = score_item(stats, {}, stale_days=180, min_confidence=0.8)
        assert priority >= 5.0
        assert any("correction" in r for r in reasons)

    def test_high_traffic_unaffirmed(self):
        stats = {"corrections": 0, "hit_count": 8, "affirmations": 0}
        priority, reasons = score_item(stats, {}, stale_days=180, min_confidence=0.8)
        assert priority >= 4.0
        assert any("never affirmed" in r for r in reasons)

    def test_affirmed_high_traffic_is_quiet(self):
        stats = {"corrections": 0, "hit_count": 8, "affirmations": 3}
        priority, reasons = score_item(stats, {}, stale_days=180, min_confidence=0.8)
        assert priority == 0.0
        assert reasons == []

    def test_stale_by_age(self):
        fm = {"updated": "2000-01-01"}
        priority, reasons = score_item({"hit_count": 0}, fm, stale_days=180, min_confidence=0.8)
        assert priority >= 2.0
        assert any("since update" in r for r in reasons)

    def test_low_confidence(self):
        fm = {"confidence": 0.5}
        priority, reasons = score_item(None, fm, stale_days=180, min_confidence=0.8)
        assert any("low confidence" in r for r in reasons)

    def test_inferred_source(self):
        fm = {"confidence": 0.9, "source": "inferred"}
        priority, reasons = score_item({"hit_count": 0}, fm, stale_days=180, min_confidence=0.8)
        assert any("inferred" in r for r in reasons)


# ---------------------------------------------------------------------------
# build_review_queue — ordering
# ---------------------------------------------------------------------------


def _browse_item(stem: str, note_type: str, fm: dict) -> BrowseItem:
    return BrowseItem(
        path=f"notes/{note_type}/{stem}.md",
        frontmatter=fm,
        body="b",
        type=note_type,
        title=stem,
    )


class TestBuildReviewQueue:
    def test_orders_by_priority_desc(self):
        items = [
            _browse_item("affirmed", "facts", {"confidence": 0.9}),
            _browse_item("correction_pending", "facts", {"confidence": 0.9}),
            _browse_item("never", "facts", {"confidence": 0.9}),
        ]
        summary = {
            "notes": {
                "notes/facts/affirmed.md": {
                    "hit_count": 5, "affirmations": 3, "corrections": 0, "signal_score": 0.5,
                },
                "notes/facts/correction_pending.md": {
                    "hit_count": 1, "affirmations": 0, "corrections": 2, "signal_score": -0.5,
                },
                # "never" absent -> unreviewed
            }
        }
        # logical_path identity so rel_path == path
        with patch("ledger.review.browse.sorted_items", return_value=items), patch(
            "ledger.review.logical_path", side_effect=lambda p, **kw: p
        ):
            queue = build_review_queue(summary=summary)
        stems = [it.stem for it in queue]
        assert stems[0] == "correction_pending"  # highest priority
        assert stems[1] == "never"               # base priority
        assert stems[-1] == "affirmed"           # zero priority, last

    def test_unjudged_only_filters(self):
        items = [
            _browse_item("judged", "facts", {}),
            _browse_item("fresh", "facts", {}),
        ]
        summary = {"notes": {"notes/facts/judged.md": {"hit_count": 1, "signal_score": 0}}}
        with patch("ledger.review.browse.sorted_items", return_value=items), patch(
            "ledger.review.logical_path", side_effect=lambda p, **kw: p
        ):
            queue = build_review_queue(summary=summary, unjudged_only=True)
        assert [it.stem for it in queue] == ["fresh"]

    def test_limit_caps_queue(self):
        items = [_browse_item(f"n{i}", "facts", {}) for i in range(10)]
        with patch("ledger.review.browse.sorted_items", return_value=items), patch(
            "ledger.review.logical_path", side_effect=lambda p, **kw: p
        ):
            queue = build_review_queue(summary={"notes": {}}, limit=3)
        assert len(queue) == 3


# ---------------------------------------------------------------------------
# flush_session — writes entries + rebuilds summary
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_config():
    with tempfile.TemporaryDirectory() as tmpdir:
        from ledger.config import LedgerConfig

        config = LedgerConfig(ledger_root=Path(tmpdir))
        config.signals_path.parent.mkdir(parents=True, exist_ok=True)
        with patch("ledger.signals.get_config", return_value=config), patch(
            "ledger.review.get_config", return_value=config
        ):
            yield config


class TestFlushSession:
    def test_empty_returns_none(self, temp_config):
        assert flush_session([]) is None

    def test_writes_entries_and_summary(self, temp_config):
        entries = [
            {"signal_type": "affirmation", "note": "notes/02_facts/fact__a.md"},
            {"signal_type": "correction", "note": "notes/02_facts/fact__b.md", "detail": "x"},
            {"signal_type": "rating", "note": "notes/02_facts/fact__a.md", "rating": 9},
        ]
        path = flush_session(entries)
        assert path == temp_config.signals_path
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 3
        types = [json.loads(line)["type"] for line in lines]
        assert types == ["affirmation", "correction", "rating"]
        # summary rebuilt
        summary = json.loads(temp_config.signal_summary_path.read_text())
        assert summary["_meta"]["total_signals"] == 3


# ---------------------------------------------------------------------------
# dashboard_data — coverage + distribution shape
# ---------------------------------------------------------------------------


class TestDashboardData:
    def test_shape_and_coverage(self, temp_config):
        from ledger import signals

        signals.append_signal("affirmation", note="notes/02_facts/fact__a.md")
        signals.append_signal("correction", note="notes/02_facts/fact__b.md")
        fake_notes = [_browse_item(f"n{i}", "facts", {}) for i in range(4)]
        with patch("ledger.review.browse.sorted_items", return_value=fake_notes):
            data = dashboard_data()
        assert data["total_signals"] == 2
        assert data["total_notes"] == 4
        assert data["judged_notes"] == 2
        assert data["coverage"] == pytest.approx(0.5)
        assert set(data["score_dist"]) == {"positive", "neutral", "negative"}
        assert data["corrections_pending"] == 1

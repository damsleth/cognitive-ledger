"""Tests for the record-time changed-since filter (plan 47)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.retrieval import apply_changed_since_filter, apply_temporal_filter
from ledger.retrieval_types import RetrievalCandidate

NOW = dt.datetime(2026, 6, 11, 12, 0, 0, tzinfo=dt.timezone.utc)
SINCE = dt.datetime(2026, 6, 1, 0, 0, 0, tzinfo=dt.timezone.utc)


def _cand(rel: str, *, created=None, updated=None, valid_to="") -> RetrievalCandidate:
    return RetrievalCandidate(
        path=f"/tmp/{rel}", rel_path=rel, type="fact", title="t", statement="s",
        body="b", updated="", updated_ts=updated, confidence=0.5, source="user",
        scope="all", status="", tags=[], note_tokens=set(), tag_tokens=set(),
        attention_tokens=set(), snippet="s", has_next_action_checkbox=False,
        created_ts=created, valid_to=valid_to,
    )


class TestChangedSinceFilter:
    def test_none_is_noop(self):
        cands = [_cand("a.md"), _cand("b.md")]
        assert apply_changed_since_filter(cands, since=None) == cands

    def test_keeps_notes_updated_after_since(self):
        recent = _cand("a.md", updated=NOW)
        assert apply_changed_since_filter([recent], since=SINCE) == [recent]

    def test_excludes_notes_older_than_since(self):
        old = _cand("a.md", updated=SINCE - dt.timedelta(days=30))
        assert apply_changed_since_filter([old], since=SINCE) == []

    def test_created_or_updated_either_qualifies(self):
        # created old, updated recent → qualifies (newest wins)
        c1 = _cand("a.md", created=SINCE - dt.timedelta(days=90), updated=NOW)
        # created recent, updated old → qualifies
        c2 = _cand("b.md", created=NOW, updated=SINCE - dt.timedelta(days=90))
        kept = apply_changed_since_filter([c1, c2], since=SINCE)
        assert {c.rel_path for c in kept} == {"a.md", "b.md"}

    def test_note_with_no_timestamps_excluded(self):
        assert apply_changed_since_filter([_cand("a.md")], since=SINCE) == []

    def test_boundary_inclusive(self):
        on_boundary = _cand("a.md", updated=SINCE)
        assert apply_changed_since_filter([on_boundary], since=SINCE) == [on_boundary]

    def test_composes_with_as_of(self):
        # valid-at filter first, then changed-since: only the note that is both
        # currently valid AND changed since SINCE survives.
        valid_recent = _cand("valid.md", updated=NOW)  # no valid_to → valid
        expired_recent = _cand("expired.md", updated=NOW,
                                valid_to="2026-01-01T00:00:00Z")  # expired
        pool = [valid_recent, expired_recent]
        after_valid = apply_temporal_filter(pool, as_of=None, now_dt=NOW)
        after_changed = apply_changed_since_filter(after_valid, since=SINCE)
        assert [c.rel_path for c in after_changed] == ["valid.md"]

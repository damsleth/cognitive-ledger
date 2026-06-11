"""Tests for timeline_since type filtering (plan 47)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.timeline import timeline_since


def _write_timeline(tmp_path: Path) -> Path:
    events = [
        {"ts": "2026-05-01T10:00:00Z", "action": "created", "path": "notes/02_facts/a.md", "type": "fact"},
        {"ts": "2026-06-05T10:00:00Z", "action": "updated", "path": "notes/05_open_loops/b.md", "type": "loop"},
        {"ts": "2026-06-08T10:00:00Z", "action": "closed", "path": "notes/05_open_loops/b.md", "type": "loop"},
        {"ts": "2026-06-09T10:00:00Z", "action": "created", "path": "notes/02_facts/c.md", "type": "fact"},
    ]
    p = tmp_path / "timeline.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return p


class TestTimelineSince:
    def test_events_since_filters_by_ts(self, tmp_path):
        p = _write_timeline(tmp_path)
        events = timeline_since(p, "2026-06-01T00:00:00Z")
        assert len(events) == 3
        assert all(e["ts"] >= "2026-06-01" for e in events)

    def test_events_since_filters_by_type_label(self, tmp_path):
        p = _write_timeline(tmp_path)
        events = timeline_since(p, "2026-06-01T00:00:00Z", types=["loop"])
        assert {e["path"] for e in events} == {"notes/05_open_loops/b.md"}

    def test_events_since_filters_by_type_folder_name(self, tmp_path):
        # config-style folder name should match the label-typed events
        p = _write_timeline(tmp_path)
        events = timeline_since(p, "2026-05-01T00:00:00Z", types=["facts"])
        assert {e["path"] for e in events} == {"notes/02_facts/a.md", "notes/02_facts/c.md"}

    def test_multiple_types(self, tmp_path):
        p = _write_timeline(tmp_path)
        events = timeline_since(p, "2026-06-01T00:00:00Z", types=["loops", "facts"])
        assert len(events) == 3

    def test_handles_missing_timeline_gracefully(self, tmp_path):
        assert timeline_since(tmp_path / "nope.jsonl", "2026-06-01T00:00:00Z") == []

"""Tests for activity_type on timeline entries."""

import json
import tempfile
import unittest
from pathlib import Path

from ledger.io.safe_write import append_timeline_entry
from ledger.timeline import load_timeline_jsonl


class TestActivityType(unittest.TestCase):

    def test_no_activity_type(self):
        """Default: no activity_type field in JSONL event."""
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "timeline.md"
            md_path.write_text("# Timeline\n\n---\n", encoding="utf-8")
            jsonl_path = Path(tmp) / "timeline.jsonl"
            jsonl_path.write_text("", encoding="utf-8")

            append_timeline_entry(md_path, "created", "notes/02_facts/fact__test.md", "test note")

            events = load_timeline_jsonl(jsonl_path)
            assert len(events) == 1
            assert "activity_type" not in events[0]

    def test_with_activity_type(self):
        """activity_type is included when provided."""
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "timeline.md"
            md_path.write_text("# Timeline\n\n---\n", encoding="utf-8")
            jsonl_path = Path(tmp) / "timeline.jsonl"
            jsonl_path.write_text("", encoding="utf-8")

            append_timeline_entry(
                md_path, "created", "notes/02_facts/fact__test.md",
                "architectural decision", activity_type="decision",
            )

            events = load_timeline_jsonl(jsonl_path)
            assert len(events) == 1
            assert events[0]["activity_type"] == "decision"

    def test_empty_activity_type_omitted(self):
        """Empty string activity_type is not included."""
        with tempfile.TemporaryDirectory() as tmp:
            md_path = Path(tmp) / "timeline.md"
            md_path.write_text("# Timeline\n\n---\n", encoding="utf-8")
            jsonl_path = Path(tmp) / "timeline.jsonl"
            jsonl_path.write_text("", encoding="utf-8")

            append_timeline_entry(
                md_path, "created", "notes/02_facts/fact__test.md",
                "test", activity_type="",
            )

            events = load_timeline_jsonl(jsonl_path)
            assert len(events) == 1
            assert "activity_type" not in events[0]


if __name__ == "__main__":
    unittest.main()

"""Tests for TUI note writer service."""

import pytest
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tui.services.note_writer import NoteWriter
from tui.services.note_parser import NoteParser


class TestNoteWriter:
    """Tests for NoteWriter class."""

    def test_append_to_timeline(self, temp_ledger_root):
        writer = NoteWriter(temp_ledger_root, temp_ledger_root / "notes")

        writer.append_to_timeline(
            "created",
            temp_ledger_root / "notes" / "02_facts" / "fact__test.md",
            "test note created",
        )

        timeline = (temp_ledger_root / "notes" / "08_indices" / "timeline.md").read_text()
        assert "created" in timeline
        assert "fact__test.md" in timeline

    def test_append_multiple_timeline_entries(self, temp_ledger_root):
        writer = NoteWriter(temp_ledger_root, temp_ledger_root / "notes")

        writer.append_to_timeline("created", Path("notes/02_facts/fact__a.md"), "first")
        writer.append_to_timeline("updated", Path("notes/02_facts/fact__b.md"), "second")

        timeline = (temp_ledger_root / "notes" / "08_indices" / "timeline.md").read_text()
        assert "created" in timeline
        assert "updated" in timeline
        assert "first" in timeline
        assert "second" in timeline

    def test_update_frontmatter(self, populated_ledger):
        writer = NoteWriter(populated_ledger, populated_ledger / "notes")
        parser = NoteParser()

        fact_path = populated_ledger / "notes" / "02_facts" / "fact__sample.md"
        note = parser.parse_file(fact_path)

        original_confidence = note.frontmatter.confidence
        writer.update_frontmatter(note, {"confidence": 0.5})

        # Re-parse and check
        updated_note = parser.parse_file(fact_path)
        assert updated_note.frontmatter.confidence == 0.5

    def test_update_frontmatter_updates_timestamp(self, populated_ledger):
        writer = NoteWriter(populated_ledger, populated_ledger / "notes")
        parser = NoteParser()

        fact_path = populated_ledger / "notes" / "02_facts" / "fact__sample.md"
        note = parser.parse_file(fact_path)
        original_updated = note.frontmatter.updated

        writer.update_frontmatter(note, {"confidence": 0.5})

        updated_note = parser.parse_file(fact_path)
        assert updated_note.frontmatter.updated > original_updated

    def test_update_frontmatter_logs_to_timeline(self, populated_ledger):
        writer = NoteWriter(populated_ledger, populated_ledger / "notes")
        parser = NoteParser()

        fact_path = populated_ledger / "notes" / "02_facts" / "fact__sample.md"
        note = parser.parse_file(fact_path)

        writer.update_frontmatter(note, {"confidence": 0.5})

        timeline = (populated_ledger / "notes" / "08_indices" / "timeline.md").read_text()
        assert "updated" in timeline
        assert "confidence" in timeline

    def test_add_section(self, populated_ledger):
        writer = NoteWriter(populated_ledger, populated_ledger / "notes")
        parser = NoteParser()

        fact_path = populated_ledger / "notes" / "02_facts" / "fact__sample.md"
        note = parser.parse_file(fact_path)

        writer.add_section(note, "New Section", "New content here.")

        content = fact_path.read_text()
        assert "## New Section" in content
        assert "New content here." in content

    def test_add_checkbox(self, populated_ledger):
        writer = NoteWriter(populated_ledger, populated_ledger / "notes")
        parser = NoteParser()

        loop_path = populated_ledger / "notes" / "05_open_loops" / "loop__sample.md"
        note = parser.parse_file(loop_path)

        writer.add_checkbox(note, "Next action", "New action item")

        content = loop_path.read_text()
        assert "- [ ] New action item" in content

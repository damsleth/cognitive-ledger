"""Tests for TUI note parser service."""

import pytest
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tui.services.note_parser import NoteParser
from tui.models.enums import NoteType, Source, Scope, LoopStatus

# Import canonical parsing functions from ledger.parsing
from ledger.parsing import (
    strip_quotes,
    strip_inline_comment,
    parse_inline_list,
    parse_scalar,
    parse_frontmatter_lines,
)


class TestNoteParserHelpers:
    """Test helper functions from ledger.parsing module."""

    def test_strip_quotes_double(self):
        assert strip_quotes('"hello"') == "hello"

    def test_strip_quotes_single(self):
        assert strip_quotes("'hello'") == "hello"

    def test_strip_quotes_none(self):
        assert strip_quotes("hello") == "hello"

    def test_strip_inline_comment(self):
        assert strip_inline_comment("value # comment") == "value"

    def test_strip_inline_comment_in_quotes(self):
        result = strip_inline_comment('"value # not comment"')
        assert "# not comment" in result

    def test_parse_inline_list(self):
        assert parse_inline_list("[a, b, c]") == ["a", "b", "c"]

    def test_parse_inline_list_empty(self):
        assert parse_inline_list("[]") == []

    def test_parse_scalar_int(self):
        assert parse_scalar("42") == 42

    def test_parse_scalar_float(self):
        assert parse_scalar("3.14") == 3.14

    def test_parse_scalar_bool_true(self):
        assert parse_scalar("true") is True

    def test_parse_scalar_bool_false(self):
        assert parse_scalar("false") is False


class TestNoteParser:
    """Tests for NoteParser class."""

    @pytest.fixture
    def parser(self):
        return NoteParser()

    def test_parse_frontmatter_canonical(self):
        """Test that canonical frontmatter parser works correctly."""
        lines = [
            "created: 2026-02-13T10:00:00Z",
            "confidence: 0.9",
        ]
        result = parse_frontmatter_lines(lines)
        assert result["created"] == "2026-02-13T10:00:00Z"
        assert result["confidence"] == 0.9

    def test_parse_frontmatter_inline_list(self):
        """Test that canonical parser handles inline lists."""
        lines = ["tags: [alpha, beta, gamma]"]
        result = parse_frontmatter_lines(lines)
        assert result["tags"] == ["alpha", "beta", "gamma"]

    def test_parse_frontmatter_block_list(self):
        """Test that canonical parser handles block lists."""
        lines = [
            "tags:",
            "  - alpha",
            "  - beta",
        ]
        result = parse_frontmatter_lines(lines)
        assert "alpha" in result["tags"]
        assert "beta" in result["tags"]

    def test_parse_file(self, parser, temp_ledger_root, sample_fact_note_text):
        path = temp_ledger_root / "notes" / "02_facts" / "fact__test.md"
        path.write_text(sample_fact_note_text)

        note = parser.parse_file(path)

        assert note.title == "Sample Fact"
        assert note.note_type == NoteType.FACT
        assert note.frontmatter.confidence == 0.9
        assert note.frontmatter.scope == Scope.DEV
        assert "test" in note.frontmatter.tags

    def test_parse_file_loop(self, parser, temp_ledger_root, sample_loop_note_text):
        path = temp_ledger_root / "notes" / "05_open_loops" / "loop__test.md"
        path.write_text(sample_loop_note_text)

        note = parser.parse_file(path)

        assert note.title == "Test Loop"
        assert note.note_type == NoteType.LOOP
        assert note.frontmatter.status == LoopStatus.OPEN

    def test_extracts_outgoing_links(self, parser, temp_ledger_root, sample_fact_note_text):
        path = temp_ledger_root / "notes" / "02_facts" / "fact__test.md"
        path.write_text(sample_fact_note_text)

        note = parser.parse_file(path)

        assert len(note.outgoing_links) >= 1
        targets = [link.target for link in note.outgoing_links]
        assert "related_note" in targets

    def test_extracts_sections(self, parser, temp_ledger_root, sample_fact_note_text):
        path = temp_ledger_root / "notes" / "02_facts" / "fact__test.md"
        path.write_text(sample_fact_note_text)

        note = parser.parse_file(path)

        # Sections should be extracted
        assert "statement" in note.sections or "Statement" in note.sections

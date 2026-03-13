"""Tests for ledger.notes module."""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ledger.notes import (
    NoteTypeConfig,
    NOTE_TYPE_CONFIGS,
    CORE_NOTE_TYPES,
    LOOP_STATUSES,
    LoopNote,
    GenericNote,
    read_note_file,
    read_note,
    get_notes,
    compute_recency_score,
)


class TestNoteTypeConfig(unittest.TestCase):
    """Tests for NoteTypeConfig dataclass."""

    def test_all_types_have_config(self):
        self.assertIn("facts", NOTE_TYPE_CONFIGS)
        self.assertIn("preferences", NOTE_TYPE_CONFIGS)
        self.assertIn("goals", NOTE_TYPE_CONFIGS)
        self.assertIn("loops", NOTE_TYPE_CONFIGS)
        self.assertIn("concepts", NOTE_TYPE_CONFIGS)

    def test_config_has_required_fields(self):
        cfg = NOTE_TYPE_CONFIGS["facts"]
        self.assertEqual(cfg.folder, "02_facts")
        self.assertEqual(cfg.prefix, "fact__")
        self.assertEqual(cfg.label, "fact")

    def test_path_in_uses_explicit_notes_root(self):
        cfg = NOTE_TYPE_CONFIGS["facts"]
        self.assertEqual(cfg.path_in(Path("/tmp/custom-notes")), Path("/tmp/custom-notes/02_facts"))

    def test_core_note_types(self):
        self.assertEqual(len(CORE_NOTE_TYPES), 5)
        self.assertIn("facts", CORE_NOTE_TYPES)

    def test_loop_statuses(self):
        self.assertIn("open", LOOP_STATUSES)
        self.assertIn("closed", LOOP_STATUSES)


class TestLoopNote(unittest.TestCase):
    """Tests for LoopNote dataclass."""

    def test_from_path(self, sample_loop_note=None):
        # Create temp file for testing
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("""---
created: 2026-02-13T10:00:00Z
updated: 2026-02-13T10:00:00Z
tags: [test]
confidence: 0.8
status: open
---

# Loop: Test Loop

## Question or Task

What should we do?

## Why it matters

This is important.

## Next Action

- [ ] First action
- [ ] Second action

## Exit Condition

When done.
""")
            path = Path(f.name)

        try:
            note = LoopNote.from_path(path)
            self.assertEqual(note.title, "Test Loop")
            self.assertEqual(note.question, "What should we do?")
            self.assertIn("important", note.why)
            self.assertEqual(note.next_action, "First action")
            self.assertEqual(note.note_type, "loops")
        finally:
            path.unlink()

    def test_to_dict(self):
        note = LoopNote(
            path=Path("/test/loop.md"),
            frontmatter={"status": "open"},
            body="body",
            title="Test",
            note_type="loops",
            question="What?",
            why="Because.",
            next_action="Do it.",
        )
        d = note.to_dict()
        self.assertEqual(d["question"], "What?")
        self.assertEqual(d["type"], "loops")


class TestGenericNote(unittest.TestCase):
    """Tests for GenericNote dataclass."""

    def test_from_path(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("""---
created: 2026-02-13T10:00:00Z
updated: 2026-02-13T10:00:00Z
tags: [test]
confidence: 0.9
---

# Sample Fact

## Statement

This is a fact statement.

## Context

Some context here.
""")
            path = Path(f.name)

        try:
            note = GenericNote.from_path(path, "facts")
            self.assertEqual(note.title, "Sample Fact")
            self.assertIn("fact statement", note.statement)
            self.assertIn("context here", note.context)
            self.assertEqual(note.note_type, "facts")
        finally:
            path.unlink()

    def test_to_dict(self):
        note = GenericNote(
            path=Path("/test/fact.md"),
            frontmatter={"confidence": 0.9},
            body="body",
            title="Test",
            note_type="facts",
            statement="Statement.",
            context="Context.",
        )
        d = note.to_dict()
        self.assertEqual(d["statement"], "Statement.")
        self.assertEqual(d["type"], "facts")


class TestBaseNoteProperties(unittest.TestCase):
    """Tests for BaseNote property methods."""

    def test_confidence_valid(self):
        note = GenericNote(
            path=Path("/test.md"),
            frontmatter={"confidence": 0.85},
            body="",
            title="",
            note_type="facts",
        )
        self.assertEqual(note.confidence, 0.85)

    def test_confidence_clamped(self):
        note = GenericNote(
            path=Path("/test.md"),
            frontmatter={"confidence": 1.5},
            body="",
            title="",
            note_type="facts",
        )
        self.assertEqual(note.confidence, 1.0)

    def test_confidence_invalid(self):
        note = GenericNote(
            path=Path("/test.md"),
            frontmatter={"confidence": "invalid"},
            body="",
            title="",
            note_type="facts",
        )
        self.assertEqual(note.confidence, 0.0)

    def test_scope(self):
        note = GenericNote(
            path=Path("/test.md"),
            frontmatter={"scope": "DEV"},
            body="",
            title="",
            note_type="facts",
        )
        self.assertEqual(note.scope, "dev")

    def test_tags_list(self):
        note = GenericNote(
            path=Path("/test.md"),
            frontmatter={"tags": ["Alpha", "Beta"]},
            body="",
            title="",
            note_type="facts",
        )
        self.assertEqual(note.tags, ["alpha", "beta"])

    def test_tags_string(self):
        note = GenericNote(
            path=Path("/test.md"),
            frontmatter={"tags": "single"},
            body="",
            title="",
            note_type="facts",
        )
        self.assertEqual(note.tags, ["single"])


class TestReadNoteFunctions(unittest.TestCase):
    """Tests for read_note_file and read_note functions."""

    def test_read_note_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("""---
created: 2026-02-13T10:00:00Z
tags: [test]
---

# Title

Body.
""")
            path = Path(f.name)

        try:
            fm, body = read_note_file(path)
            self.assertEqual(fm["created"], "2026-02-13T10:00:00Z")
            self.assertIn("# Title", body)
        finally:
            path.unlink()

    def test_read_note_infers_type(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir="/tmp") as f:
            f.write("""---
tags: [test]
---

# Test
""")
            path = Path(f.name)

        try:
            note = read_note(path, note_type="facts")
            self.assertIsInstance(note, GenericNote)
        finally:
            path.unlink()

    def test_get_notes_respects_explicit_notes_dir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            notes_root = Path(tmp) / "notes"
            facts_dir = notes_root / "02_facts"
            facts_dir.mkdir(parents=True)
            (facts_dir / "fact__override.md").write_text(
                """---
created: 2026-02-13T10:00:00Z
updated: 2026-02-13T10:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Override

## Statement

Uses the explicit notes directory.
""",
                encoding="utf-8",
            )

            notes = get_notes("facts", notes_dir=notes_root)
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].path.name, "fact__override.md")


class TestComputeRecencyScore(unittest.TestCase):
    """Tests for compute_recency_score function."""

    def test_recent_note(self):
        now = datetime(2026, 2, 13, 10, 0, 0, tzinfo=timezone.utc)
        updated = datetime(2026, 2, 13, 9, 0, 0, tzinfo=timezone.utc)
        score = compute_recency_score(updated, now)
        self.assertGreater(score, 0.99)

    def test_old_note(self):
        now = datetime(2026, 2, 13, 10, 0, 0, tzinfo=timezone.utc)
        updated = datetime(2025, 11, 13, 10, 0, 0, tzinfo=timezone.utc)
        score = compute_recency_score(updated, now)
        self.assertEqual(score, 0.0)

    def test_none_timestamp(self):
        now = datetime(2026, 2, 13, 10, 0, 0, tzinfo=timezone.utc)
        score = compute_recency_score(None, now)
        self.assertEqual(score, 0.0)


if __name__ == "__main__":
    unittest.main()

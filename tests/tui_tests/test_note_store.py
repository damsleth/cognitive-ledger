"""Tests for TUI note store service."""

import pytest
import sys
from pathlib import Path

# Add root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tui.services.note_store import NoteStore
from ledger.notes import NoteType, Scope


class TestNoteStore:
    """Tests for NoteStore class."""

    def test_load_all(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        notes = store.all_notes()
        assert len(notes) >= 2  # At least fact and loop

    def test_get_note_by_path(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        fact_path = populated_ledger / "notes" / "02_facts" / "fact__sample.md"
        note = store.get_note(fact_path)

        assert note is not None
        assert note.title == "Sample Fact"

    def test_get_note_by_stem(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        note = store.get_note_by_stem("fact__sample")

        assert note is not None
        assert "sample" in note.path.stem.lower()

    def test_get_notes_by_type(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        facts = store.get_notes_by_type(NoteType.FACT)

        assert len(facts) >= 1
        for note in facts:
            assert note.note_type == NoteType.FACT

    def test_get_notes_by_scope(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        dev_notes = store.get_notes_by_scope(Scope.DEV)
        work_notes = store.get_notes_by_scope(Scope.WORK)

        assert len(dev_notes) >= 1
        assert len(work_notes) >= 1

        for note in dev_notes:
            assert note.frontmatter.scope == Scope.DEV

    def test_get_open_loops(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        loops = store.get_open_loops()

        assert len(loops) >= 1
        assert loops[0].note_type == NoteType.LOOP

    def test_get_notes_by_tag(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        tagged = store.get_notes_by_tag("test")

        assert len(tagged) >= 1
        for note in tagged:
            assert "test" in note.frontmatter.tags

    def test_all_tags(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        tags = store.all_tags()

        assert "test" in tags

    def test_search_finds_content(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        results = store.search("sample")

        assert len(results) >= 1

    def test_search_with_scope_filter(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        work_results = store.search("fact", scope=Scope.WORK)
        dev_results = store.search("fact", scope=Scope.DEV)

        # Work scope should find work item
        assert any("work" in str(n.path).lower() for n in work_results)

    def test_refresh_reloads(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        initial_count = len(store.all_notes())

        # Add a new note
        new_fact = populated_ledger / "notes" / "02_facts" / "fact__new.md"
        new_fact.write_text("""---
created: 2026-02-13T11:00:00Z
updated: 2026-02-13T11:00:00Z
tags: [new]
confidence: 0.9
source: user
scope: dev
lang: en
---

# New Fact

## Statement

A newly created fact.
""")

        store.refresh()

        assert len(store.all_notes()) == initial_count + 1

    def test_filesystem_snapshot_includes_note_files(self, populated_ledger):
        store = NoteStore(populated_ledger)
        snapshot = store.filesystem_snapshot()
        assert snapshot
        assert all(str(path).endswith(".md") for path in snapshot)

    def test_sorted_by_updated(self, populated_ledger):
        store = NoteStore(populated_ledger)
        store.load_all()

        notes = store.all_notes()

        # Should be sorted by updated (most recent first)
        for i in range(len(notes) - 1):
            assert notes[i].frontmatter.updated >= notes[i + 1].frontmatter.updated

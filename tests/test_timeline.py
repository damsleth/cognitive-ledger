from __future__ import annotations

from pathlib import Path

from ledger.timeline import (
    ensure_timeline_jsonl,
    load_timeline_jsonl,
    regenerate_timeline_markdown,
    timeline_since,
    timeline_for_note,
)
from ledger.io.safe_write import append_timeline_entry


def test_migrate_markdown_to_jsonl(tmp_path: Path):
    md = tmp_path / "timeline.md"
    jsonl = tmp_path / "timeline.jsonl"
    md.write_text(
        """# Timeline

---
2026-02-01T00:00:00Z | created | notes/02_facts/fact__one.md | seed
2026-02-02T00:00:00Z | updated | notes/02_facts/fact__one.md | update
""",
        encoding="utf-8",
    )

    events = ensure_timeline_jsonl(md, jsonl)

    assert len(events) == 2
    assert jsonl.is_file()
    loaded = load_timeline_jsonl(jsonl)
    assert len(loaded) == 2


def test_timeline_queries(tmp_path: Path):
    md = tmp_path / "timeline.md"
    jsonl = tmp_path / "timeline.jsonl"
    md.write_text(
        """# Timeline

---
2026-02-01T00:00:00Z | created | notes/02_facts/fact__one.md | seed
2026-02-03T00:00:00Z | updated | notes/03_preferences/pref__one.md | update
""",
        encoding="utf-8",
    )

    ensure_timeline_jsonl(md, jsonl)
    regenerate_timeline_markdown(jsonl, md)

    since = timeline_since(jsonl, "2026-02-02T00:00:00Z")
    assert len(since) == 1
    assert since[0]["path"] == "notes/03_preferences/pref__one.md"

    by_note = timeline_for_note(jsonl, "notes/02_facts/fact__one.md")
    assert len(by_note) == 1
    assert by_note[0]["action"] == "created"


def test_jsonl_is_the_only_writable_timeline_source(tmp_path: Path):
    md = tmp_path / "timeline.md"
    jsonl = tmp_path / "timeline.jsonl"
    md.write_text(
        "# Timeline\n\n---\n"
        "2026-02-01T00:00:00Z | created | notes/02_facts/fact__md_only.md | direct append\n",
        encoding="utf-8",
    )
    jsonl.write_text("", encoding="utf-8")

    append_timeline_entry(
        md,
        "created",
        "notes/02_facts/fact__canonical.md",
        "canonical append",
        timestamp="2026-02-02T00:00:00Z",
    )

    events = load_timeline_jsonl(jsonl)
    assert [event["path"] for event in events] == [
        "notes/02_facts/fact__canonical.md"
    ]
    rendered = md.read_text(encoding="utf-8")
    assert "fact__canonical.md" in rendered
    assert "fact__md_only.md" not in rendered


def test_canonical_append_records_current_note_hash(tmp_path: Path):
    notes_dir = tmp_path / "notes"
    note = notes_dir / "02_facts" / "fact__hashed.md"
    note.parent.mkdir(parents=True)
    note.write_text("current content\n", encoding="utf-8")
    md = notes_dir / "08_indices" / "timeline.md"

    append_timeline_entry(
        md,
        "updated",
        note,
        "hashed",
        root_dir=tmp_path,
        ledger_notes_dir=notes_dir,
        timestamp="2026-02-02T00:00:00Z",
    )

    import hashlib

    event = load_timeline_jsonl(md.with_name("timeline.jsonl"))[0]
    assert event["content_hash"] == hashlib.sha256(note.read_bytes()).hexdigest()


def test_missing_jsonl_is_migrated_before_canonical_append(tmp_path: Path):
    md = tmp_path / "timeline.md"
    jsonl = tmp_path / "timeline.jsonl"
    md.write_text(
        "# Timeline\n\n---\n"
        "2026-02-01T00:00:00Z | created | notes/02_facts/fact__legacy.md | legacy\n",
        encoding="utf-8",
    )

    append_timeline_entry(
        md,
        "updated",
        "notes/02_facts/fact__new.md",
        "new",
        timestamp="2026-02-02T00:00:00Z",
    )

    assert [event["path"] for event in load_timeline_jsonl(jsonl)] == [
        "notes/02_facts/fact__legacy.md",
        "notes/02_facts/fact__new.md",
    ]

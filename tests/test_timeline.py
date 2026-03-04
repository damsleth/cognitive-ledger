from __future__ import annotations

from pathlib import Path

from ledger.timeline import (
    ensure_timeline_jsonl,
    load_timeline_jsonl,
    regenerate_timeline_markdown,
    timeline_since,
    timeline_for_note,
)


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

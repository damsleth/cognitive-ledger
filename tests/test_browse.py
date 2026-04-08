from __future__ import annotations

from ledger import browse
from ledger.config import LedgerConfig, reset_config, set_config


def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_loop(path) -> None:
    _write(
        path,
        """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-03T00:00:00Z
tags: [release]
confidence: 0.9
source: user
scope: dev
lang: en
status: open
---

# Release Checklist

## Question or Task

Finish the release checklist

## Next Action

- [ ] Run the final smoke tests
""",
    )


def test_sorted_items_and_formatting_use_typed_browse_items(tmp_path):
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    try:
        note = config.ledger_notes_dir / "05_open_loops" / "loop__release.md"
        _seed_loop(note)

        items = browse.sorted_items("loops")

        assert items
        assert items[0].question == "Finish the release checklist"
        assert "Run the final smoke tests" in browse.compact_line(items[0], width=200)

        detail = browse.format_detail(items[0], width=80)
        assert any("Question or task:" in line for line in detail)
        assert any("Path: notes/05_open_loops/loop__release.md" == line for line in detail)
    finally:
        reset_config()

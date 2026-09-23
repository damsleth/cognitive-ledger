"""The stale-index warning must only count notes the index embeds."""

from __future__ import annotations

import json
from pathlib import Path

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.embeddings import notes_newer_than_index


def _note(notes: Path, rel: str) -> None:
    path = notes / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ntitle: x\n---\nbody\n", encoding="utf-8")


def test_only_embedded_folders_count_as_stale(tmp_path):
    notes = tmp_path / "notes"
    cfg = LedgerConfig(ledger_root=tmp_path, ledger_notes_dir=notes)
    set_config(cfg)
    try:
        cfg.semantic_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        cfg.semantic_manifest_path.write_text(
            json.dumps({"targets": {"ledger": {"latest": {"built_at": "2000-01-01T00:00:00Z"}}}}),
            encoding="utf-8",
        )
        _note(notes, "02_facts/fact__new.md")
        _note(notes, "00_inbox/note__ingest_summary.md")
        _note(notes, "09_archive/note__old.md")

        assert notes_newer_than_index() == ["02_facts/fact__new.md"]
    finally:
        reset_config()

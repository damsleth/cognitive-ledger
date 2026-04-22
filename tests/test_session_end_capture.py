from __future__ import annotations

import subprocess
from pathlib import Path

from ledger.config import LedgerConfig, reset_config, set_config
from scripts.hooks import session_end_capture


def _set_temp_config(tmp_path: Path) -> LedgerConfig:
    root = tmp_path / "repo"
    notes = tmp_path / "ledger-notes"
    root.mkdir()
    (notes / "00_inbox").mkdir(parents=True)
    (notes / "08_indices").mkdir(parents=True)
    config = LedgerConfig(ledger_root=root, ledger_notes_dir=notes)
    set_config(config)
    return config


def test_write_inbox_note_adds_suffix_on_slug_collision(tmp_path):
    config = _set_temp_config(tmp_path)
    try:
        first = session_end_capture._write_inbox_note("Same title", "first", [])
        second = session_end_capture._write_inbox_note("Same title", "second", [])

        assert first.name == "same_title.md"
        assert second.name == "same_title_1.md"
        assert first.exists()
        assert second.exists()
        assert "second" in second.read_text(encoding="utf-8")
        assert config.timeline_path.with_name("timeline.jsonl").is_file()
    finally:
        reset_config()


def test_notes_dirty_paths_ignores_lock_files(tmp_path):
    config = _set_temp_config(tmp_path)
    try:
        subprocess.run(["git", "init"], cwd=config.ledger_notes_dir, check=True, capture_output=True, text=True)
        lock_path = config.ledger_notes_dir / "00_inbox" / "candidate.md.lock"
        note_path = config.ledger_notes_dir / "00_inbox" / "candidate.md"
        lock_path.write_text("", encoding="utf-8")
        note_path.write_text("# Candidate\n", encoding="utf-8")

        assert session_end_capture._notes_dirty_paths() == ["notes/00_inbox/candidate.md"]
    finally:
        reset_config()

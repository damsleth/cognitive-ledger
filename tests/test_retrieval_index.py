from __future__ import annotations

import json
import time
from pathlib import Path

from ledger.config import LedgerConfig, reset_config, set_config
from ledger import retrieval
import importlib


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_note(path: Path, statement: str) -> None:
    _write(
        path,
        f"""---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Test Note

## Statement

{statement}
""",
    )


def test_rebuild_note_index_writes_entries(tmp_path):
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__one.md"
        _seed_note(note, "First statement")

        retrieval_mod = importlib.reload(retrieval)
        payload = retrieval_mod.rebuild_note_index()
        note_index_path = config.ledger_notes_dir / "08_indices" / "note_index.json"

        assert payload["version"] == 2
        assert payload["entries"]
        assert payload["inverted"]
        assert note_index_path.is_file()

        disk = json.loads(note_index_path.read_text(encoding="utf-8"))
        assert disk["entries"]
        assert disk["inverted"]
    finally:
        reset_config()
        importlib.reload(retrieval)


def test_rebuild_note_index_updates_changed_note(tmp_path):
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__one.md"
        _seed_note(note, "First statement")

        retrieval_mod = importlib.reload(retrieval)
        first = retrieval_mod.rebuild_note_index()
        rel = "notes/02_facts/fact__one.md"
        first_hash = first["entries"][rel]["content_hash"]

        time.sleep(0.01)
        _seed_note(note, "Updated statement")

        second = retrieval_mod.rebuild_note_index()
        second_hash = second["entries"][rel]["content_hash"]

        assert first_hash != second_hash
    finally:
        reset_config()
        importlib.reload(retrieval)


def test_rebuild_note_index_is_stable_when_unchanged(tmp_path):
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__one.md"
        _seed_note(note, "Stable statement")

        retrieval_mod = importlib.reload(retrieval)
        first = retrieval_mod.rebuild_note_index()
        second = retrieval_mod.rebuild_note_index()

        assert first["built"] == second["built"]
    finally:
        reset_config()
        importlib.reload(retrieval)


def test_rebuild_note_index_uses_latest_config_without_reload(tmp_path):
    retrieval_mod = importlib.reload(retrieval)
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__one.md"
        _seed_note(note, "Config-sensitive statement")

        payload = retrieval_mod.rebuild_note_index()

        assert payload["entries"]
        assert (config.ledger_notes_dir / "08_indices" / "note_index.json").is_file()
    finally:
        reset_config()
        importlib.reload(retrieval)


def test_rank_lexical_returns_typed_results_and_timing(tmp_path):
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__one.md"
        _seed_note(note, "First statement")

        retrieval_mod = importlib.reload(retrieval)
        result = retrieval_mod.rank_lexical("first statement", scope="dev", limit=5)

        assert result.results
        assert result.results[0].rel_path == "notes/02_facts/fact__one.md"
        assert result.timing.total_ms >= 0.0
        assert result.timing.expand_ms >= 0.0
    finally:
        reset_config()
        importlib.reload(retrieval)

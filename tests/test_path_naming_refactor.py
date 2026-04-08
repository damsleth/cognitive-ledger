from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import patch

from ledger import browse, context, inbox, maintenance, retrieval, voice
from ledger.config import LedgerConfig, reset_config, set_config


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_fact(path: Path, statement: str) -> None:
    _write(
        path,
        f"""---
created: 2026-04-08T09:00:00Z
updated: 2026-04-08T09:00:00Z
tags: [refactor]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Refactor Fact

## Statement

{statement}
""",
    )


def _seed_loop(path: Path, question: str, next_action: str) -> None:
    _write(
        path,
        f"""---
created: 2026-04-08T09:00:00Z
updated: 2026-04-08T09:00:00Z
tags: [release]
confidence: 0.9
source: user
scope: dev
lang: en
status: open
---

# Release Loop

## Question or Task

{question}

## Next Action

- [ ] {next_action}
""",
    )


def _seed_inbox(path: Path) -> None:
    _write(
        path,
        """---
created: 2026-04-08T09:00:00Z
updated: 2026-04-08T09:00:00Z
tags: [capture]
confidence: 0.8
source: assistant
scope: dev
lang: en
---

# Captured decision

We decided to rename the path config.
""",
    )


def _configure_cross_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo_root = tmp_path / "repo"
    notes_root = tmp_path / "external-notes"
    source_root = tmp_path / "source-notes"
    repo_root.mkdir()
    notes_root.mkdir()
    source_root.mkdir()

    config = LedgerConfig(
        ledger_root=repo_root,
        ledger_notes_dir=notes_root,
        source_notes_dir=source_root,
    )
    set_config(config)
    return repo_root, notes_root, source_root


def test_cross_root_indices_browse_and_context_paths_are_logical(tmp_path):
    _repo_root, notes_root, _source_root = _configure_cross_root(tmp_path)
    try:
        _seed_fact(notes_root / "02_facts" / "fact__naming.md", "Canonical path names are in use.")
        _seed_loop(
            notes_root / "05_open_loops" / "loop__release.md",
            "Finish the release checklist.",
            "Run the smoke tests.",
        )

        retrieval_mod = importlib.reload(retrieval)
        maintenance_mod = importlib.reload(maintenance)

        with patch.object(maintenance_mod, "_generate_semantic_index", return_value=None):
            assert maintenance_mod.cmd_index() == 0

        note_index = json.loads((notes_root / "08_indices" / "note_index.json").read_text(encoding="utf-8"))
        assert "notes/02_facts/fact__naming.md" in note_index["entries"]
        assert "notes/05_open_loops/loop__release.md" in note_index["entries"]

        result = retrieval_mod.rank_lexical("smoke tests", scope="dev", limit=3)
        assert result.results
        assert result.results[0].rel_path == "notes/05_open_loops/loop__release.md"

        items = browse.sorted_items("loops")
        assert items
        detail = browse.format_detail(items[0], width=100)
        assert "Path: notes/05_open_loops/loop__release.md" in detail

        context.write_context_profiles(notes_root / "08_indices", notes_root)
        payload = json.loads((notes_root / "08_indices" / "context_profile_dev.json").read_text(encoding="utf-8"))
        fact_paths = [row["path"] for row in payload["facts"]]
        assert fact_paths == ["notes/02_facts/fact__naming.md"]
    finally:
        retrieval.clear_candidate_cache()
        reset_config()
        importlib.reload(retrieval)
        importlib.reload(maintenance)


def test_cross_root_inbox_voice_and_timeline_use_ledger_notes_dir(tmp_path):
    _repo_root, notes_root, _source_root = _configure_cross_root(tmp_path)
    try:
        inbox_note = notes_root / "00_inbox" / "capture.md"
        _seed_inbox(inbox_note)

        promoted = inbox.promote(inbox_note, "facts")
        assert promoted == notes_root / "02_facts" / "fact__capture.md"

        voice_json = tmp_path / "voice.json"
        voice_json.write_text(json.dumps({"tone": "direct"}), encoding="utf-8")
        imported = voice.import_voice_dna(voice_json)
        assert imported == notes_root / "01_identity" / "id__voice_dna.md"

        timeline = (notes_root / "08_indices" / "timeline.md").read_text(encoding="utf-8")
        assert "notes/02_facts/fact__capture.md" in timeline
        assert "notes/01_identity/id__voice_dna.md" in timeline
        assert str(notes_root) not in timeline
    finally:
        reset_config()

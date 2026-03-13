from __future__ import annotations

import json
from pathlib import Path

from ledger.parsing import parse_frontmatter_text
from ledger.obsidian.cli import main as obsidian_main
from ledger.obsidian.config import load_config
from ledger.obsidian.importer import run_import
from ledger.obsidian.queue import sync_queue


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    obsidian_dir = vault / ".obsidian"
    obsidian_dir.mkdir()
    (obsidian_dir / "core-plugins.json").write_text(json.dumps({"bases": True}), encoding="utf-8")
    return vault


def _make_note_root(tmp_path: Path) -> Path:
    root = tmp_path / "note-root"
    root.mkdir(parents=True)
    return root


def test_init_creates_expected_layout(tmp_path):
    vault = _make_vault(tmp_path)

    rc = obsidian_main(["init", "--vault", str(vault), "--no-auto-start"])
    assert rc == 0

    expected_paths = [
        vault / "cognitive-ledger" / "notes" / "00_inbox",
        vault / "cognitive-ledger" / "notes" / "02_facts",
        vault / "cognitive-ledger" / "notes" / "03_preferences",
        vault / "cognitive-ledger" / "notes" / "04_goals",
        vault / "cognitive-ledger" / "notes" / "05_open_loops",
        vault / "cognitive-ledger" / "notes" / "06_concepts",
        vault / "cognitive-ledger" / "notes" / "08_indices" / "timeline.md",
        vault / "cognitive-ledger" / "notes" / "08_indices" / "obsidian_import_log.md",
        vault / "cognitive-ledger" / "notes" / "08_indices" / "obsidian_scan.md",
        vault / "cognitive-ledger" / "config.json",
        vault / "cognitive-ledger" / "bases" / "ledger_candidates.base",
        vault / "cognitive-ledger" / "bases" / "ledger_notes.base",
    ]

    for path in expected_paths:
        assert path.exists(), f"missing {path}"


def test_import_applies_gates_and_does_not_modify_source_files(tmp_path):
    vault = _make_vault(tmp_path)
    obsidian_main(["init", "--vault", str(vault), "--no-auto-start"])

    source_pref = vault / "04-dev" / "workflow.md"
    source_pref.parent.mkdir(parents=True, exist_ok=True)
    source_pref.write_text("I prefer concise responses with explicit tradeoffs.\n", encoding="utf-8")

    source_loop = vault / "04-dev" / "tasks.md"
    source_loop.write_text(
        (
            "# CI Stabilization\n\n"
            "Open question: we need to decide how to stabilize CI before release.\n"
            "- [ ] Investigate flaky CI integration tests across environments.\n"
        ),
        encoding="utf-8",
    )

    pref_before = source_pref.read_text(encoding="utf-8")
    loop_before = source_loop.read_text(encoding="utf-8")

    config = load_config(vault)
    first = run_import(config)

    assert first.notes_created >= 1
    assert first.queue_created >= 1

    preferences = list((vault / "cognitive-ledger" / "notes" / "03_preferences").glob("pref__*.md"))
    queue_notes = list((vault / "cognitive-ledger" / "notes" / "00_inbox").glob("candidate__*.md"))
    assert preferences
    assert queue_notes

    assert source_pref.read_text(encoding="utf-8") == pref_before
    assert source_loop.read_text(encoding="utf-8") == loop_before


def test_reimport_is_idempotent_for_unchanged_files(tmp_path):
    vault = _make_vault(tmp_path)
    obsidian_main(["init", "--vault", str(vault), "--no-auto-start"])

    source = vault / "04-dev" / "workflow.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("I prefer concise responses with explicit tradeoffs.\n", encoding="utf-8")

    config = load_config(vault)
    first = run_import(config)
    second = run_import(config)

    assert first.notes_created >= 1
    assert second.notes_created == 0
    assert second.queue_created == 0


def test_queue_sync_promotes_approved_candidates_idempotently(tmp_path):
    vault = _make_vault(tmp_path)
    obsidian_main(["init", "--vault", str(vault), "--no-auto-start"])

    source = vault / "04-dev" / "tasks.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        (
            "# CI Stabilization\n\n"
            "Open question: we need to decide how to stabilize CI before release.\n"
            "- [ ] Investigate flaky CI integration tests across environments.\n"
        ),
        encoding="utf-8",
    )

    config = load_config(vault)
    result = run_import(config)
    assert result.queue_created >= 1

    candidate_path = sorted((vault / "cognitive-ledger" / "notes" / "00_inbox").glob("candidate__*.md"))[0]
    text = candidate_path.read_text(encoding="utf-8")
    text = text.replace("review_status: pending", "review_status: approved")
    candidate_path.write_text(text, encoding="utf-8")

    first = sync_queue(config)
    second = sync_queue(config)

    assert first["promoted"] == 1
    assert second["promoted"] == 0

    updated_text = candidate_path.read_text(encoding="utf-8")
    frontmatter, _body = parse_frontmatter_text(updated_text)
    assert str(frontmatter.get("review_status", "")).lower() == "promoted"
    promoted_path = str(frontmatter.get("promoted_path", ""))
    assert promoted_path
    assert (vault / promoted_path).exists()

    timeline = (vault / "cognitive-ledger" / "notes" / "08_indices" / "timeline.md").read_text(encoding="utf-8")
    assert "promoted from candidate queue" in timeline
    assert "candidate promoted" in timeline


def test_bootstrap_supports_generic_markdown_root_via_root_alias(tmp_path):
    root = _make_note_root(tmp_path)
    source_pref = root / "projects" / "workflow.md"
    source_pref.parent.mkdir(parents=True, exist_ok=True)
    source_pref.write_text("I prefer concise responses with explicit tradeoffs.\n", encoding="utf-8")

    source_loop = root / "projects" / "tasks.md"
    source_loop.write_text(
        (
            "# CI Stabilization\n\n"
            "Open question: we need to decide how to stabilize CI before release.\n"
            "- [ ] Investigate flaky CI integration tests across environments.\n"
        ),
        encoding="utf-8",
    )

    rc = obsidian_main(["bootstrap", "--root", str(root), "--max-files", "10", "--max-notes", "10"])
    assert rc == 0

    preferences = list((root / "cognitive-ledger" / "notes" / "03_preferences").glob("pref__*.md"))
    queue_notes = list((root / "cognitive-ledger" / "notes" / "00_inbox").glob("candidate__*.md"))
    assert preferences
    assert queue_notes

    config = load_config(root)
    assert config.vault_root == root.resolve()

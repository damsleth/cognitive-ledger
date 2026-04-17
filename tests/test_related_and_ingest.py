"""Tests for related_to_text() and ingest pipeline."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from ledger.config import LedgerConfig, reset_config, set_config
from ledger import retrieval
from ledger.ingest import (
    diff_manifest,
    load_manifest,
    prepare_ingest_context,
    record_ingest,
    save_manifest,
    scan_sources,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_note(notes_dir: Path, folder: str, slug: str, statement: str) -> Path:
    path = notes_dir / folder / slug
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
    return path


class TestRelatedToText:
    """Tests for the related_to_text function."""

    def test_returns_results_for_matching_text(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            _seed_note(config.ledger_notes_dir, "02_facts", "fact__python.md", "Python programming language")
            _seed_note(config.ledger_notes_dir, "02_facts", "fact__rust.md", "Rust systems programming")

            retrieval_mod = importlib.reload(retrieval)
            results = retrieval_mod.related_to_text("python programming")

            assert isinstance(results, list)
            assert len(results) > 0
            assert all("path" in r and "title" in r and "score" in r for r in results)
        finally:
            reset_config()
            retrieval_mod.clear_candidate_cache()
            importlib.reload(retrieval)

    def test_returns_empty_for_empty_text(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            retrieval_mod = importlib.reload(retrieval)
            results = retrieval_mod.related_to_text("")
            assert results == []
        finally:
            reset_config()
            importlib.reload(retrieval)

    def test_respects_top_k(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            for i in range(10):
                _seed_note(
                    config.ledger_notes_dir,
                    "02_facts",
                    f"fact__item{i}.md",
                    f"test item number {i} about programming",
                )

            retrieval_mod = importlib.reload(retrieval)
            results = retrieval_mod.related_to_text("test programming", top_k=3)
            assert len(results) <= 3
        finally:
            reset_config()
            retrieval_mod.clear_candidate_cache()
            importlib.reload(retrieval)

    def test_scope_filter(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            path = config.ledger_notes_dir / "02_facts" / "fact__work.md"
            _write(path, """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: work
lang: en
---

# Work Note

## Statement

Work-related programming tasks.
""")
            retrieval_mod = importlib.reload(retrieval)
            results = retrieval_mod.related_to_text("programming", scope="work")
            assert isinstance(results, list)
        finally:
            reset_config()
            retrieval_mod.clear_candidate_cache()
            importlib.reload(retrieval)

    def test_result_structure(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            _seed_note(config.ledger_notes_dir, "02_facts", "fact__example.md", "Example for structure test")

            retrieval_mod = importlib.reload(retrieval)
            results = retrieval_mod.related_to_text("example structure")

            if results:
                r = results[0]
                assert "path" in r
                assert "title" in r
                assert "score" in r
                assert "snippet" in r
                assert "tags" in r
                assert "updated" in r
                assert isinstance(r["score"], float)
        finally:
            reset_config()
            retrieval_mod.clear_candidate_cache()
            importlib.reload(retrieval)


class TestScanSources:
    """Tests for scan_sources."""

    def test_scans_markdown_files(self, tmp_path):
        source_root = tmp_path / "sources"
        source_root.mkdir()
        (source_root / "note1.md").write_text("# Note 1")
        (source_root / "note2.md").write_text("# Note 2")
        (source_root / "ignore.txt").write_text("not markdown")

        results = scan_sources(source_root)
        assert len(results) == 2
        paths = {r["path"] for r in results}
        assert "note1.md" in paths
        assert "note2.md" in paths

    def test_skips_hidden_dirs(self, tmp_path):
        source_root = tmp_path / "sources"
        source_root.mkdir()
        hidden = source_root / ".hidden"
        hidden.mkdir()
        (hidden / "secret.md").write_text("# Hidden")
        (source_root / "visible.md").write_text("# Visible")

        results = scan_sources(source_root)
        assert len(results) == 1
        assert results[0]["path"] == "visible.md"

    def test_returns_empty_for_missing_dir(self, tmp_path):
        results = scan_sources(tmp_path / "nonexistent")
        assert results == []

    def test_result_has_required_fields(self, tmp_path):
        source_root = tmp_path / "sources"
        source_root.mkdir()
        (source_root / "note.md").write_text("# Note")

        results = scan_sources(source_root)
        assert len(results) == 1
        r = results[0]
        assert "path" in r
        assert "sha256" in r
        assert "modified" in r
        assert "size" in r


class TestDiffManifest:
    """Tests for diff_manifest."""

    def test_detects_new_files(self):
        manifest = []
        scan = [{"path": "new.md", "sha256": "abc"}]
        diff = diff_manifest(manifest, scan)
        assert len(diff["new"]) == 1
        assert diff["new"][0]["path"] == "new.md"

    def test_detects_deleted_files(self):
        manifest = [{"path": "old.md", "sha256": "abc"}]
        scan = []
        diff = diff_manifest(manifest, scan)
        assert len(diff["deleted"]) == 1
        assert diff["deleted"][0]["path"] == "old.md"

    def test_detects_modified_files(self):
        manifest = [{"path": "note.md", "sha256": "old_hash"}]
        scan = [{"path": "note.md", "sha256": "new_hash"}]
        diff = diff_manifest(manifest, scan)
        assert len(diff["modified"]) == 1

    def test_no_changes(self):
        entries = [{"path": "note.md", "sha256": "same"}]
        diff = diff_manifest(entries, entries)
        assert diff["new"] == []
        assert diff["modified"] == []
        assert diff["deleted"] == []


class TestManifest:
    """Tests for load_manifest and save_manifest."""

    def test_roundtrip(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            entries = [{"path": "test.md", "sha256": "abc123"}]
            save_manifest(entries)
            loaded = load_manifest()
            assert loaded == entries
        finally:
            reset_config()

    def test_load_missing_returns_empty(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            result = load_manifest()
            assert result == []
        finally:
            reset_config()


class TestPrepareIngestContext:
    """Tests for prepare_ingest_context."""

    def test_returns_context_dict(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            source_root = tmp_path / "sources"
            source_root.mkdir()
            (source_root / "note.md").write_text("# Test note about programming")

            retrieval_mod = importlib.reload(retrieval)
            result = prepare_ingest_context("note.md", source_root=source_root)
            assert "source_content" in result
            assert "related_notes" in result
            assert "ingest_prompt" in result
            assert result["source_path"] == "note.md"
        finally:
            reset_config()
            retrieval_mod.clear_candidate_cache()
            importlib.reload(retrieval)

    def test_raises_for_missing_file(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            import pytest
            with pytest.raises(FileNotFoundError):
                prepare_ingest_context("nonexistent.md", source_root=tmp_path)
        finally:
            reset_config()


class TestRecordIngest:
    """Tests for record_ingest."""

    def test_records_to_manifest(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            source_root = tmp_path / "sources"
            source_root.mkdir()
            (source_root / "note.md").write_text("# Content")
            # Ensure timeline file exists
            config.timeline_path.parent.mkdir(parents=True, exist_ok=True)
            config.timeline_path.write_text("")

            record_ingest(
                "note.md",
                ["notes/02_facts/fact__derived.md"],
                source_root=source_root,
            )

            manifest = load_manifest()
            assert len(manifest) == 1
            assert manifest[0]["path"] == "note.md"
            assert manifest[0]["derived_notes"] == ["notes/02_facts/fact__derived.md"]
        finally:
            reset_config()

    def test_updates_existing_entry(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)
        try:
            source_root = tmp_path / "sources"
            source_root.mkdir()
            (source_root / "note.md").write_text("# Content")
            config.timeline_path.parent.mkdir(parents=True, exist_ok=True)
            config.timeline_path.write_text("")

            record_ingest("note.md", ["first.md"], source_root=source_root)
            record_ingest("note.md", ["first.md", "second.md"], source_root=source_root)

            manifest = load_manifest()
            assert len(manifest) == 1
            assert len(manifest[0]["derived_notes"]) == 2
        finally:
            reset_config()

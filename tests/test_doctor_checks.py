"""Tests for new run_doctor() checks and fix mode."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.conventions import DoctorFinding, DoctorPayload
from ledger.doctor import run_doctor


def _make_config(tmp: Path, *, create_notes_dir: bool = True) -> tuple[Path, Path]:
    """Set up a minimal ledger config for tests."""
    root = tmp / "ledger"
    notes = tmp / "notes"
    root.mkdir(parents=True, exist_ok=True)
    if create_notes_dir:
        notes.mkdir(parents=True, exist_ok=True)
        # Create 08_indices subdirectory so path checks don't fire spuriously.
        (notes / "08_indices").mkdir(parents=True, exist_ok=True)
    xdg = tmp / "xdg"
    xdg.mkdir(parents=True, exist_ok=True)
    os.environ["XDG_CONFIG_HOME"] = str(xdg)
    cfg = LedgerConfig(ledger_root=root, ledger_notes_dir=notes)
    set_config(cfg)
    return root, notes


class TestNewDoctorChecks:
    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()
        os.environ.pop("XDG_CONFIG_HOME", None)

    def test_notes_subdir_missing_fires(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=False)
        # notes dir doesn't exist
        payload = run_doctor()
        ids = [f.id for f in payload.findings]
        assert "notes_subdir_missing" in ids

    def test_notes_subdir_missing_fix_creates_dir(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=False)
        assert not notes.exists()
        payload = run_doctor(fix=True)
        # After fix the notes dir should exist.
        assert notes.exists()
        # The finding should be marked fixed.
        fixed_ids = [f.id for f in payload.findings if getattr(f, "fixed", False)]
        assert "notes_subdir_missing" in fixed_ids

    def test_timeline_missing_fires(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=True)
        # timeline.md absent
        tl_path = notes / "08_indices" / "timeline.md"
        assert not tl_path.exists()
        payload = run_doctor()
        ids = [f.id for f in payload.findings]
        assert "timeline_missing" in ids

    def test_timeline_missing_fix_creates_file(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=True)
        tl_path = notes / "08_indices" / "timeline.md"
        assert not tl_path.exists()
        payload = run_doctor(fix=True)
        assert tl_path.is_file()
        fixed_ids = [f.id for f in payload.findings if getattr(f, "fixed", False)]
        assert "timeline_missing" in fixed_ids

    def test_stale_lock_files_fires(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=True)
        lock = notes / "02_facts" / "fact__test.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.touch()
        payload = run_doctor()
        ids = [f.id for f in payload.findings]
        assert "stale_lock_files" in ids

    def test_stale_lock_files_fix_removes_locks(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=True)
        lock = notes / "02_facts" / "fact__test.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.touch()
        assert lock.exists()
        payload = run_doctor(fix=True)
        assert not lock.exists()
        fixed_ids = [f.id for f in payload.findings if getattr(f, "fixed", False)]
        assert "stale_lock_files" in fixed_ids

    def test_held_lock_survives_doctor_fix_and_inbox_cleanup(self, tmp_path):
        import fcntl
        import os

        from ledger.inbox import cleanup_inbox

        root, notes = _make_config(tmp_path, create_notes_dir=True)
        inbox = notes / "00_inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        held = inbox / "orphan_held.md.lock"      # orphan: no .md sibling
        unheld = inbox / "orphan_free.md.lock"
        held.touch()
        unheld.touch()
        fd = os.open(str(held), os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            run_doctor(fix=True)
            assert held.exists(), "doctor --fix removed a held lock"
            assert not unheld.exists()

            unheld.touch()
            result = cleanup_inbox(notes_dir=notes, apply=True)
            assert held.exists(), "inbox cleanup removed a held lock"
            assert not unheld.exists()
            assert "orphan_held.md.lock" in result["orphaned_locks"]
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_semantic_index_found_where_embed_build_writes_it(self, tmp_path):
        from ledger.embeddings import (
            configured_model_for_backend,
            semantic_index_path,
            semantic_vectors_path,
        )

        root, notes = _make_config(tmp_path, create_notes_dir=True)
        assert "semantic_index_missing" in [f.id for f in run_doctor().findings]
        model = configured_model_for_backend("local")
        index_file = semantic_index_path("ledger", "local", model)
        index_file.parent.mkdir(parents=True, exist_ok=True)
        index_file.write_text("{}", encoding="utf-8")
        semantic_vectors_path("ledger", "local", model).write_bytes(b"\0")
        assert "semantic_index_missing" not in [f.id for f in run_doctor().findings]

    def test_malformed_timeline_jsonl_is_an_error_with_line_number(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=True)
        jsonl = notes / "08_indices" / "timeline.jsonl"
        jsonl.write_text(
            '{"ts": "2026-01-01T00:00:00Z", "action": "created", "path": "a", "desc": "x"}\n'
            "not json at all\n",
            encoding="utf-8",
        )
        findings = {f.id: f for f in run_doctor().findings}
        assert findings["timeline_jsonl_malformed"].severity == "error"
        assert "timeline.jsonl:2" in findings["timeline_jsonl_malformed"].message

    def test_stale_timeline_md_is_regenerated_from_jsonl(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=True)
        idx = notes / "08_indices"
        (idx / "timeline.jsonl").write_text(
            '{"ts": "2026-01-01T00:00:00Z", "action": "created", "path": "notes/02_facts/fact__a.md", "desc": "x"}\n',
            encoding="utf-8",
        )
        (idx / "timeline.md").write_text("# Timeline\n\nhand-edited\n", encoding="utf-8")
        assert "timeline_md_stale" in [f.id for f in run_doctor().findings]
        run_doctor(fix=True)
        assert "fact__a.md" in (idx / "timeline.md").read_text(encoding="utf-8")
        assert "timeline_md_stale" not in [f.id for f in run_doctor().findings]

    def test_private_fence_in_index_fires(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=True)
        index_json = notes / "08_indices" / "note_index.json"
        index_json.write_text('{"notes":["```private\\nsecret\\n```"]}', encoding="utf-8")
        payload = run_doctor()
        ids = [f.id for f in payload.findings]
        assert "private_fence_in_index" in ids

    def test_no_false_positive_on_clean_ledger(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=True)
        # Create timeline so that check doesn't fire.
        from ledger.timeline import TIMELINE_MARKDOWN_HEADER
        from ledger.io.safe_write import safe_write_text
        tl = notes / "08_indices" / "timeline.md"
        safe_write_text(tl, TIMELINE_MARKDOWN_HEADER)
        payload = run_doctor()
        # On a clean ledger with timeline present, only info-level checks should
        # fire (semantic_index_missing, note_index_missing) — no errors or warnings.
        high_findings = [f for f in payload.findings if f.severity in ("error", "warning")]
        ids = [f.id for f in high_findings]
        # redact_sentinel_leak and redact_unavailable must not fire.
        assert "redact_sentinel_leak" not in ids
        assert "redact_unavailable" not in ids
        # notes_subdir_missing and timeline_missing must not fire.
        assert "notes_subdir_missing" not in ids
        assert "timeline_missing" not in ids


class TestDoctorFixable:
    def setup_method(self):
        reset_config()

    def teardown_method(self):
        reset_config()
        os.environ.pop("XDG_CONFIG_HOME", None)

    def test_finding_fixable_field_in_json(self, tmp_path):
        root, notes = _make_config(tmp_path, create_notes_dir=False)
        payload = run_doctor()
        finding = next((f for f in payload.findings if f.id == "notes_subdir_missing"), None)
        assert finding is not None
        assert finding.fixable is True
        d = finding.to_dict()
        assert d.get("fixable") is True

    def test_apply_fixes_returns_descriptions(self, tmp_path):
        from ledger.doctor import apply_fixes
        root, notes = _make_config(tmp_path, create_notes_dir=False)
        payload = run_doctor(fix=True)
        fixed = apply_fixes(payload)
        assert any("notes_subdir_missing" in item or str(notes) in item for item in fixed)

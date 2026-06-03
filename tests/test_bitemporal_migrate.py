"""Tests for ledger.bitemporal.migrate_bitemporal and cmd_migrate_bitemporal.

Covers:
- Fresh tree: back-fills valid_from = created date; archive notes also get valid_to.
- Re-run is a no-op (idempotent).
- --check (apply=False) writes nothing.
- 00_inbox notes are left untouched.
- 05_open_loops notes are left untouched.
- Per-folder touched counts are accurate.
- Timeline entry appended on --apply when notes were written.
- CLI integration: ledger migrate bitemporal --check / --apply.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from ledger.bitemporal import (
    MigrationResult,
    cmd_migrate_bitemporal,
    migrate_bitemporal,
)
from ledger.config import LedgerConfig, set_config, reset_config
from ledger.parsing.frontmatter import parse_frontmatter_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_FM = """\
created: {created}
updated: {updated}
tags: [test]
confidence: 0.9
source: user
scope: personal
lang: en
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_config(tmp_path: Path) -> LedgerConfig:
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    # Ensure timeline exists so timeline helper does not error
    tl = config.timeline_path
    tl.parent.mkdir(parents=True, exist_ok=True)
    tl.write_text("# Timeline\n", encoding="utf-8")
    return config


def _note(
    *,
    created: str = "2025-03-01T00:00:00Z",
    updated: str = "2025-03-01T00:00:00Z",
    extra: str = "",
    body: str = "## Statement\n\nBody.\n",
) -> str:
    fm = _BASE_FM.format(created=created, updated=updated)
    return f"---\n{fm}{extra}---\n\n# Note\n\n{body}"


def _read_fm(path: Path) -> dict:
    fm, _ = parse_frontmatter_text(path.read_text(encoding="utf-8"))
    return fm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_config(tmp_path):
    config = _make_config(tmp_path)
    yield config
    reset_config()


# ---------------------------------------------------------------------------
# migrate_bitemporal — core logic
# ---------------------------------------------------------------------------

class TestMigrateBitemporal:
    def test_backfills_valid_from_from_created(self, tmp_config):
        """A note without valid_from gets valid_from = created date midnight UTC."""
        notes_dir = tmp_config.ledger_notes_dir
        note = notes_dir / "02_facts" / "fact__test.md"
        _write(note, _note(created="2025-06-15T10:30:00Z"))

        result = migrate_bitemporal(apply=True)

        assert result.applied is True
        assert result.touched.get("facts", 0) == 1
        fm = _read_fm(note)
        assert fm["valid_from"] == "2025-06-15T00:00:00Z"
        # valid_to should NOT be set on a non-archive note
        vt = str(fm.get("valid_to", "")).strip()
        assert vt in ("", "null", "~", "None") or not vt

    def test_archive_note_gets_valid_to_from_updated(self, tmp_config):
        """Archive notes get both valid_from = created AND valid_to = updated."""
        notes_dir = tmp_config.ledger_notes_dir
        note = notes_dir / "09_archive" / "fact__old.md"
        _write(note, _note(created="2024-01-01T00:00:00Z", updated="2025-01-01T12:00:00Z"))

        result = migrate_bitemporal(apply=True)

        assert result.touched.get("archive", 0) == 1
        fm = _read_fm(note)
        assert fm["valid_from"] == "2024-01-01T00:00:00Z"
        assert fm["valid_to"] == "2025-01-01T00:00:00Z"

    def test_rerun_is_noop(self, tmp_config):
        """Running migration twice leaves notes unchanged the second time."""
        notes_dir = tmp_config.ledger_notes_dir
        note = notes_dir / "02_facts" / "fact__idempotent.md"
        _write(note, _note(created="2025-01-01T00:00:00Z"))

        # First run
        r1 = migrate_bitemporal(apply=True)
        assert r1.touched.get("facts", 0) == 1
        mtime_after_first = note.stat().st_mtime

        # Second run
        r2 = migrate_bitemporal(apply=True)
        assert r2.touched.get("facts", 0) == 0
        # File must not be rewritten
        assert note.stat().st_mtime == mtime_after_first

    def test_check_mode_writes_nothing(self, tmp_config, tmp_path):
        """--check reports intent but does not write any files."""
        notes_dir = tmp_config.ledger_notes_dir
        note = notes_dir / "03_preferences" / "pref__check.md"
        original = _note(created="2025-02-01T00:00:00Z")
        _write(note, original)
        mtime_before = note.stat().st_mtime

        result = migrate_bitemporal(apply=False)

        assert result.applied is False
        assert result.touched.get("preferences", 0) == 1
        # File content unchanged
        assert note.read_text(encoding="utf-8") == original
        assert note.stat().st_mtime == mtime_before

    def test_inbox_untouched(self, tmp_config):
        """00_inbox notes must never be modified."""
        notes_dir = tmp_config.ledger_notes_dir
        inbox_note = notes_dir / "00_inbox" / "draft__thing.md"
        original = _note(created="2025-01-01T00:00:00Z")
        _write(inbox_note, original)

        result = migrate_bitemporal(apply=True)

        # Inbox is not an eligible folder
        assert "inbox" not in result.touched
        assert inbox_note.read_text(encoding="utf-8") == original

    def test_open_loops_untouched(self, tmp_config):
        """05_open_loops notes are not eligible and must not be modified."""
        notes_dir = tmp_config.ledger_notes_dir
        loop_note = notes_dir / "05_open_loops" / "loop__task.md"
        content = "---\ncreated: 2025-01-01T00:00:00Z\nupdated: 2025-01-01T00:00:00Z\ntags: [test]\nconfidence: 0.8\nsource: user\nscope: personal\nlang: en\nstatus: open\n---\n\n# Loop\n\n## Next Action\n\n- [ ] Do something\n"
        _write(loop_note, content)

        migrate_bitemporal(apply=True)

        assert loop_note.read_text(encoding="utf-8") == content

    def test_per_folder_counts_multiple_folders(self, tmp_config):
        """touched counts should reflect the actual per-folder distribution."""
        notes_dir = tmp_config.ledger_notes_dir
        # Two facts, one preference, one archive
        _write(notes_dir / "02_facts" / "fact__a.md", _note(created="2025-01-01T00:00:00Z"))
        _write(notes_dir / "02_facts" / "fact__b.md", _note(created="2025-02-01T00:00:00Z"))
        _write(notes_dir / "03_preferences" / "pref__x.md", _note(created="2025-03-01T00:00:00Z"))
        _write(
            notes_dir / "09_archive" / "fact__old.md",
            _note(created="2024-01-01T00:00:00Z", updated="2024-06-01T00:00:00Z"),
        )

        result = migrate_bitemporal(apply=False)  # dry run

        assert result.touched.get("facts", 0) == 2
        assert result.touched.get("preferences", 0) == 1
        assert result.touched.get("archive", 0) == 1
        assert result.total_eligible >= 4

    def test_already_has_valid_from_skipped(self, tmp_config):
        """A note that already has valid_from must be counted as skipped, not touched."""
        notes_dir = tmp_config.ledger_notes_dir
        note = notes_dir / "02_facts" / "fact__already.md"
        _write(
            note,
            _note(
                created="2025-01-01T00:00:00Z",
                extra="valid_from: 2025-01-01T00:00:00Z\n",
            ),
        )
        original_content = note.read_text(encoding="utf-8")

        result = migrate_bitemporal(apply=True)

        assert result.touched.get("facts", 0) == 0
        # File was not rewritten
        assert note.read_text(encoding="utf-8") == original_content

    def test_timeline_entry_appended_on_apply(self, tmp_config, tmp_path):
        """A timeline entry is written when --apply causes note writes."""
        notes_dir = tmp_config.ledger_notes_dir
        _write(notes_dir / "02_facts" / "fact__tl.md", _note(created="2025-01-01T00:00:00Z"))

        migrate_bitemporal(apply=True)

        # Check timeline.md and/or timeline.jsonl for the migration event.
        # The JSONL field is "desc"; the markdown line contains the description verbatim.
        tl_jsonl = tmp_config.timeline_path.with_suffix(".jsonl")
        tl_md = tmp_config.timeline_path
        found_in_jsonl = False
        if tl_jsonl.exists():
            lines = [
                json.loads(l)
                for l in tl_jsonl.read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            # JSONL uses "desc" key (not "description")
            descs = [l.get("desc", "") for l in lines]
            found_in_jsonl = any("migrate" in d for d in descs)
        found_in_md = tl_md.exists() and "migrate" in tl_md.read_text(encoding="utf-8")
        assert found_in_jsonl or found_in_md, (
            "Expected a 'migrate bitemporal' timeline entry in timeline.jsonl or timeline.md"
        )

    def test_no_timeline_entry_when_nothing_written(self, tmp_config):
        """No timeline entry should be appended when all notes are already migrated."""
        notes_dir = tmp_config.ledger_notes_dir
        # Note already has valid_from
        _write(
            notes_dir / "02_facts" / "fact__already.md",
            _note(extra="valid_from: 2025-01-01T00:00:00Z\n"),
        )
        tl_before = tmp_config.timeline_path.read_text(encoding="utf-8")

        migrate_bitemporal(apply=True)

        tl_after = tmp_config.timeline_path.read_text(encoding="utf-8")
        # Timeline should be unchanged (no new entries)
        assert tl_after == tl_before

    def test_all_eligible_folders_covered(self, tmp_config):
        """Notes in all six eligible subdirs are processed."""
        notes_dir = tmp_config.ledger_notes_dir
        eligible = [
            ("01_identity", "id__me.md"),
            ("02_facts", "fact__x.md"),
            ("03_preferences", "pref__y.md"),
            ("04_goals", "goal__z.md"),
            ("06_concepts", "concept__c.md"),
            ("09_archive", "fact__old.md"),
        ]
        for subdir, fname in eligible:
            _write(
                notes_dir / subdir / fname,
                _note(created="2025-01-01T00:00:00Z", updated="2025-06-01T00:00:00Z"),
            )

        result = migrate_bitemporal(apply=True)

        assert result.total_eligible == 6
        assert result.total_touched == 6


# ---------------------------------------------------------------------------
# cmd_migrate_bitemporal — CLI entry point
# ---------------------------------------------------------------------------

class TestCmdMigrateBitemporal:
    def test_check_prints_summary(self, tmp_config, capsys):
        """--check mode prints a dry-run summary."""
        notes_dir = tmp_config.ledger_notes_dir
        _write(notes_dir / "02_facts" / "fact__a.md", _note(created="2025-01-01T00:00:00Z"))

        rc = cmd_migrate_bitemporal(apply=False)

        out = capsys.readouterr().out
        assert rc == 0
        assert "dry run" in out.lower() or "--check" in out
        assert "Would touch" in out

    def test_apply_prints_applied(self, tmp_config, capsys):
        """--apply mode prints confirmation."""
        notes_dir = tmp_config.ledger_notes_dir
        _write(notes_dir / "02_facts" / "fact__b.md", _note(created="2025-02-01T00:00:00Z"))

        rc = cmd_migrate_bitemporal(apply=True)

        out = capsys.readouterr().out
        assert rc == 0
        assert "Applied" in out

    def test_check_does_not_write_files(self, tmp_config):
        """Calling cmd_migrate_bitemporal(apply=False) must not write any files."""
        notes_dir = tmp_config.ledger_notes_dir
        note = notes_dir / "04_goals" / "goal__big.md"
        original = _note(created="2025-01-01T00:00:00Z")
        _write(note, original)
        mtime_before = note.stat().st_mtime

        cmd_migrate_bitemporal(apply=False)

        assert note.read_text(encoding="utf-8") == original
        assert note.stat().st_mtime == mtime_before

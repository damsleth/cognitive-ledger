"""Tests for ledger.bitemporal — supersession primitive and validity helpers.

Covers:
- parse_validity: full timestamp, date-only, null, missing.
- is_valid_at: open left bound, open right bound, both bounds, outside bounds.
- supersede: fields set correctly; archive move; timeline.jsonl + signal written;
  idempotent re-call; error cases (old==new, missing notes, already-archived).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from ledger.bitemporal import (
    SupersessionResult,
    is_valid_at,
    parse_validity,
    supersede,
)
from ledger.config import LedgerConfig, set_config, reset_config
from ledger.parsing.frontmatter import parse_frontmatter_text


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_BASE_FM = """\
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_config(tmp_path: Path) -> LedgerConfig:
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    return config


def _note_content(extra_fm: str = "", body: str = "## Statement\n\nBody.\n") -> str:
    return f"---\n{_BASE_FM}{extra_fm}---\n\n# Note\n\n{body}"


@pytest.fixture()
def tmp_config(tmp_path):
    config = _make_config(tmp_path)
    # Ensure archive dir exists
    (config.ledger_notes_dir / "09_archive").mkdir(parents=True, exist_ok=True)
    (config.ledger_notes_dir / "02_facts").mkdir(parents=True, exist_ok=True)
    # Write a minimal timeline so timeline helper does not fail
    timeline_path = config.timeline_path
    timeline_path.parent.mkdir(parents=True, exist_ok=True)
    timeline_path.write_text("# Timeline\n", encoding="utf-8")
    yield config
    reset_config()


# ---------------------------------------------------------------------------
# parse_validity
# ---------------------------------------------------------------------------


class TestParseValidity:
    def test_both_full_timestamps(self):
        fm = {
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2026-06-01T00:00:00Z",
        }
        vf, vt = parse_validity(fm)
        assert vf == dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        assert vt == dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc)

    def test_date_only_promoted_to_midnight_utc(self):
        fm = {"valid_from": "2026-03-15"}
        vf, vt = parse_validity(fm)
        assert vf == dt.datetime(2026, 3, 15, 0, 0, 0, tzinfo=dt.timezone.utc)
        assert vt is None

    def test_null_strings_return_none(self):
        for sentinel in ("null", "~", "None", ""):
            fm = {"valid_from": sentinel, "valid_to": sentinel}
            vf, vt = parse_validity(fm)
            assert vf is None, f"expected None for valid_from={sentinel!r}"
            assert vt is None, f"expected None for valid_to={sentinel!r}"

    def test_missing_keys_return_none(self):
        vf, vt = parse_validity({})
        assert vf is None
        assert vt is None

    def test_invalid_string_returns_none(self):
        fm = {"valid_from": "not-a-date"}
        vf, _ = parse_validity(fm)
        assert vf is None


# ---------------------------------------------------------------------------
# is_valid_at
# ---------------------------------------------------------------------------


class TestIsValidAt:
    _T = dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=dt.timezone.utc)

    def test_open_both_bounds_always_valid(self):
        assert is_valid_at({}, self._T) is True

    def test_open_right_valid_after_from(self):
        fm = {"valid_from": "2026-01-01T00:00:00Z"}
        assert is_valid_at(fm, self._T) is True

    def test_open_right_invalid_before_from(self):
        fm = {"valid_from": "2027-01-01T00:00:00Z"}
        assert is_valid_at(fm, self._T) is False

    def test_closed_interval_inside(self):
        fm = {
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2026-12-31T23:59:59Z",
        }
        assert is_valid_at(fm, self._T) is True

    def test_closed_interval_before(self):
        fm = {
            "valid_from": "2025-01-01T00:00:00Z",
            "valid_to": "2025-12-31T23:59:59Z",
        }
        assert is_valid_at(fm, self._T) is False

    def test_closed_interval_after(self):
        fm = {
            "valid_from": "2027-01-01T00:00:00Z",
            "valid_to": "2027-12-31T23:59:59Z",
        }
        assert is_valid_at(fm, self._T) is False

    def test_exact_boundary_valid_from(self):
        fm = {"valid_from": "2026-06-01T12:00:00Z"}
        assert is_valid_at(fm, self._T) is True

    def test_exact_boundary_valid_to(self):
        fm = {
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2026-06-01T12:00:00Z",
        }
        assert is_valid_at(fm, self._T) is True

    def test_just_after_valid_to_invalid(self):
        fm = {
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2026-06-01T11:59:59Z",
        }
        assert is_valid_at(fm, self._T) is False


# ---------------------------------------------------------------------------
# supersede() — happy path
# ---------------------------------------------------------------------------


class TestSupersede:
    def _make_pair(self, config: LedgerConfig):
        """Write old note (in facts) and new note (in facts); return their refs."""
        facts = config.ledger_notes_dir / "02_facts"
        old = facts / "fact__old.md"
        new = facts / "fact__new.md"
        _write(old, _note_content("valid_from: 2025-01-01T00:00:00Z\n"))
        _write(new, _note_content("valid_from: 2026-01-01T00:00:00Z\n"))
        return "notes/02_facts/fact__old.md", "notes/02_facts/fact__new.md"

    def test_old_note_moved_to_archive(self, tmp_config):
        old_ref, new_ref = self._make_pair(tmp_config)
        result = supersede(old_ref, new_ref)
        archive = tmp_config.ledger_notes_dir / "09_archive" / "fact__old.md"
        assert archive.exists(), "old note must be in 09_archive"
        old_orig = tmp_config.ledger_notes_dir / "02_facts" / "fact__old.md"
        assert not old_orig.exists(), "old note must no longer exist at original path"
        assert result.archive_path == archive

    def test_old_note_frontmatter_fields_set(self, tmp_config):
        old_ref, new_ref = self._make_pair(tmp_config)
        result = supersede(old_ref, new_ref)
        archive = tmp_config.ledger_notes_dir / "09_archive" / "fact__old.md"
        fm, _ = parse_frontmatter_text(archive.read_text(encoding="utf-8"))
        assert fm.get("superseded_by") == new_ref
        assert fm.get("valid_to") == result.valid_to_set
        assert fm.get("valid_to") == "2026-01-01T00:00:00Z"  # matches new.valid_from

    def test_new_note_supersedes_list_updated(self, tmp_config):
        old_ref, new_ref = self._make_pair(tmp_config)
        supersede(old_ref, new_ref)
        new_abs = tmp_config.ledger_notes_dir / "02_facts" / "fact__new.md"
        fm, _ = parse_frontmatter_text(new_abs.read_text(encoding="utf-8"))
        supersedes = fm.get("supersedes", [])
        assert old_ref in supersedes

    def test_valid_to_uses_as_of_arg(self, tmp_config):
        old_ref, new_ref = self._make_pair(tmp_config)
        explicit_dt = dt.datetime(2025, 7, 4, 0, 0, 0, tzinfo=dt.timezone.utc)
        result = supersede(old_ref, new_ref, as_of=explicit_dt)
        assert result.valid_to_set == "2025-07-04T00:00:00Z"

    def test_valid_to_uses_as_of_string(self, tmp_config):
        old_ref, new_ref = self._make_pair(tmp_config)
        result = supersede(old_ref, new_ref, as_of="2025-09-01T00:00:00Z")
        assert result.valid_to_set == "2025-09-01T00:00:00Z"

    def test_valid_to_defaults_to_new_valid_from(self, tmp_config):
        old_ref, new_ref = self._make_pair(tmp_config)
        result = supersede(old_ref, new_ref)
        assert result.valid_to_set == "2026-01-01T00:00:00Z"

    def test_valid_to_defaults_to_today_when_new_has_no_valid_from(self, tmp_config):
        facts = tmp_config.ledger_notes_dir / "02_facts"
        old = facts / "fact__old2.md"
        new = facts / "fact__new2.md"
        _write(old, _note_content("valid_from: 2025-01-01T00:00:00Z\n"))
        _write(new, _note_content())  # no valid_from
        today = dt.datetime.now(tz=dt.timezone.utc).date()
        result = supersede(
            "notes/02_facts/fact__old2.md",
            "notes/02_facts/fact__new2.md",
        )
        expected_prefix = dt.datetime(today.year, today.month, today.day, 0, 0, 0).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        assert result.valid_to_set == expected_prefix

    def test_timeline_entry_written(self, tmp_config):
        old_ref, new_ref = self._make_pair(tmp_config)
        supersede(old_ref, new_ref)
        timeline_jsonl = (
            tmp_config.ledger_notes_dir / "08_indices" / "timeline.jsonl"
        )
        assert timeline_jsonl.exists(), "timeline.jsonl should be written"
        entries = [
            json.loads(line)
            for line in timeline_jsonl.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(
            e.get("action") == "archived" and "fact__old" in e.get("path", "")
            for e in entries
        ), f"No archived timeline entry found in: {entries}"

    def test_signal_written(self, tmp_config):
        old_ref, new_ref = self._make_pair(tmp_config)
        supersede(old_ref, new_ref, reason="unit test reason")
        signals_path = tmp_config.ledger_notes_dir / "08_indices" / "signals.jsonl"
        assert signals_path.exists(), "signals.jsonl should be written"
        signals = [
            json.loads(line)
            for line in signals_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        supersession_signals = [s for s in signals if s.get("type") == "supersession"]
        assert supersession_signals, "no supersession signal found"
        entry = supersession_signals[0]
        assert entry.get("note") == old_ref
        detail = json.loads(entry.get("detail", "{}"))
        assert detail.get("old") == old_ref
        assert detail.get("new") == new_ref
        assert detail.get("reason") == "unit test reason"

    def test_result_fields(self, tmp_config):
        old_ref, new_ref = self._make_pair(tmp_config)
        result = supersede(old_ref, new_ref)
        assert isinstance(result, SupersessionResult)
        assert result.old_ref == old_ref
        assert result.new_ref == new_ref
        assert result.idempotent is False
        assert result.archive_path.name == "fact__old.md"


# ---------------------------------------------------------------------------
# supersede() — idempotency
# ---------------------------------------------------------------------------


class TestSupersededIdempotency:
    def test_idempotent_on_already_superseded(self, tmp_config):
        """Calling supersede twice should return idempotent=True on second call."""
        facts = tmp_config.ledger_notes_dir / "02_facts"
        old = facts / "fact__idem_old.md"
        new = facts / "fact__idem_new.md"
        _write(old, _note_content("valid_from: 2025-01-01T00:00:00Z\n"))
        _write(new, _note_content("valid_from: 2026-01-01T00:00:00Z\n"))
        old_ref = "notes/02_facts/fact__idem_old.md"
        new_ref = "notes/02_facts/fact__idem_new.md"

        first = supersede(old_ref, new_ref)
        assert first.idempotent is False

        # Second call: old now lives in 09_archive with superseded_by set
        archive_old_ref = "notes/09_archive/fact__idem_old.md"
        second = supersede(archive_old_ref, new_ref)
        assert second.idempotent is True


# ---------------------------------------------------------------------------
# supersede() — error cases
# ---------------------------------------------------------------------------


class TestSupersededErrors:
    def test_old_equals_new_raises(self, tmp_config):
        facts = tmp_config.ledger_notes_dir / "02_facts"
        note = facts / "fact__same.md"
        _write(note, _note_content())
        ref = "notes/02_facts/fact__same.md"
        with pytest.raises(Exception, match="same file"):
            supersede(ref, ref)

    def test_old_not_found_raises(self, tmp_config):
        facts = tmp_config.ledger_notes_dir / "02_facts"
        new = facts / "fact__existing.md"
        _write(new, _note_content())
        from ledger.errors import NoteNotFoundError
        with pytest.raises(NoteNotFoundError):
            supersede("notes/02_facts/fact__ghost.md", "notes/02_facts/fact__existing.md")

    def test_new_not_found_raises(self, tmp_config):
        facts = tmp_config.ledger_notes_dir / "02_facts"
        old = facts / "fact__real_old.md"
        _write(old, _note_content())
        from ledger.errors import NoteNotFoundError
        with pytest.raises(NoteNotFoundError):
            supersede("notes/02_facts/fact__real_old.md", "notes/02_facts/fact__ghost_new.md")

    def test_invalid_as_of_string_raises(self, tmp_config):
        facts = tmp_config.ledger_notes_dir / "02_facts"
        old = facts / "fact__inv_old.md"
        new = facts / "fact__inv_new.md"
        _write(old, _note_content())
        _write(new, _note_content())
        from ledger.errors import NoteError
        with pytest.raises(NoteError, match="as_of"):
            supersede(
                "notes/02_facts/fact__inv_old.md",
                "notes/02_facts/fact__inv_new.md",
                as_of="not-a-date",
            )


# ---------------------------------------------------------------------------
# supersede() — existing supersedes list is extended, not replaced
# ---------------------------------------------------------------------------


def test_supersedes_list_accumulates(tmp_config):
    """supersedes on the new note accumulates rather than overwrites."""
    facts = tmp_config.ledger_notes_dir / "02_facts"
    old_a = facts / "fact__acc_a.md"
    old_b = facts / "fact__acc_b.md"
    new = facts / "fact__acc_new.md"
    _write(old_a, _note_content("valid_from: 2024-01-01T00:00:00Z\n"))
    _write(old_b, _note_content("valid_from: 2025-01-01T00:00:00Z\n"))
    _write(new, _note_content("valid_from: 2026-01-01T00:00:00Z\n"))

    supersede("notes/02_facts/fact__acc_a.md", "notes/02_facts/fact__acc_new.md")
    # old_b must be written fresh after old_a's supersede mutated the new note
    # (old_b still exists in facts at this point)
    supersede("notes/02_facts/fact__acc_b.md", "notes/02_facts/fact__acc_new.md")

    new_abs = facts / "fact__acc_new.md"
    fm, _ = parse_frontmatter_text(new_abs.read_text(encoding="utf-8"))
    supersedes = fm.get("supersedes", [])
    assert "notes/09_archive/fact__acc_a.md" in supersedes or "notes/02_facts/fact__acc_a.md" in supersedes
    assert "notes/09_archive/fact__acc_b.md" in supersedes or "notes/02_facts/fact__acc_b.md" in supersedes

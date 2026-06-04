"""Tests for bitemporal (valid-time) frontmatter lint rules.

Covers: valid_from > valid_to fails; dangling superseded_by fails;
superseded_by without valid_to fails; null valid_to passes; note with NO
new fields passes unchanged; null valid_from warns (not fails); bad date
format fails; 00_inbox notes are exempt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ledger.config import LedgerConfig, set_config, reset_config
from ledger import maintenance


# ---------------------------------------------------------------------------
# Helpers (mirrors style from test_maintenance.py)
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_temp_config(tmp_path: Path) -> LedgerConfig:
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    return config


_BASE_FM = """\
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
"""

_EMPTY_TIMELINE = "# Timeline\n"


# ---------------------------------------------------------------------------
# Backward-compatibility: note WITHOUT any bitemporal fields lints clean
# ---------------------------------------------------------------------------


def test_note_without_bitemporal_fields_passes(tmp_path):
    """A legacy note with no valid_from/valid_to/superseded_by must lint clean."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__no_bitemporal.md"
        _write(
            note,
            f"---\n{_BASE_FM}---\n\n# A fact\n\n## Statement\n\nSome fact.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0


# ---------------------------------------------------------------------------
# valid_from and valid_to both set — ordering rules
# ---------------------------------------------------------------------------


def test_valid_from_after_valid_to_fails(tmp_path):
    """valid_from > valid_to is an error."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__ordering.md"
        _write(
            note,
            f"---\n{_BASE_FM}"
            "valid_from: 2026-06-01T00:00:00Z\n"
            "valid_to: 2026-01-01T00:00:00Z\n"
            "---\n\n# Fact\n\n## Statement\n\nFact body.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 1


def test_valid_from_before_valid_to_passes(tmp_path):
    """valid_from < valid_to is valid."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__good_range.md"
        _write(
            note,
            f"---\n{_BASE_FM}"
            "valid_from: 2026-01-01T00:00:00Z\n"
            "valid_to: 2026-06-01T00:00:00Z\n"
            "---\n\n# Fact\n\n## Statement\n\nFact body.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0


def test_valid_from_equal_to_valid_to_passes(tmp_path):
    """valid_from == valid_to is valid (instantaneous fact)."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__instant.md"
        _write(
            note,
            f"---\n{_BASE_FM}"
            "valid_from: 2026-03-15T12:00:00Z\n"
            "valid_to: 2026-03-15T12:00:00Z\n"
            "---\n\n# Fact\n\n## Statement\n\nInstantaneous.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0


# ---------------------------------------------------------------------------
# null valid_to is OK (open interval)
# ---------------------------------------------------------------------------


def test_null_valid_to_passes(tmp_path):
    """A note with valid_from but no valid_to (open interval) must lint clean."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__open_interval.md"
        _write(
            note,
            f"---\n{_BASE_FM}"
            "valid_from: 2026-01-01T00:00:00Z\n"
            "---\n\n# Fact\n\n## Statement\n\nFact body.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0


# ---------------------------------------------------------------------------
# superseded_by requires valid_to
# ---------------------------------------------------------------------------


def test_superseded_by_without_valid_to_fails(tmp_path):
    """superseded_by set without valid_to is an error."""
    config = _make_temp_config(tmp_path)
    try:
        # Create the referenced (replacement) note so the dangling check passes.
        new_note = config.ledger_notes_dir / "02_facts" / "fact__new.md"
        _write(
            new_note,
            f"---\n{_BASE_FM}---\n\n# New fact\n\n## Statement\n\nReplacement.\n",
        )
        old_note = config.ledger_notes_dir / "09_archive" / "fact__old.md"
        _write(
            old_note,
            f"---\n{_BASE_FM}"
            "valid_from: 2026-01-01T00:00:00Z\n"
            "superseded_by: notes/02_facts/fact__new.md\n"
            "---\n\n# Old fact\n\n## Statement\n\nOld body.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 1


def test_superseded_by_with_valid_to_passes(tmp_path):
    """superseded_by with valid_to set is valid."""
    config = _make_temp_config(tmp_path)
    try:
        new_note = config.ledger_notes_dir / "02_facts" / "fact__new.md"
        _write(
            new_note,
            f"---\n{_BASE_FM}---\n\n# New fact\n\n## Statement\n\nReplacement.\n",
        )
        old_note = config.ledger_notes_dir / "09_archive" / "fact__old.md"
        _write(
            old_note,
            f"---\n{_BASE_FM}"
            "valid_from: 2026-01-01T00:00:00Z\n"
            "valid_to: 2026-06-01T00:00:00Z\n"
            "superseded_by: notes/02_facts/fact__new.md\n"
            "---\n\n# Old fact\n\n## Statement\n\nOld body.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0


# ---------------------------------------------------------------------------
# superseded_by dangling reference
# ---------------------------------------------------------------------------


def test_dangling_superseded_by_fails(tmp_path):
    """superseded_by pointing to a non-existent note is an error."""
    config = _make_temp_config(tmp_path)
    try:
        old_note = config.ledger_notes_dir / "09_archive" / "fact__dangling.md"
        _write(
            old_note,
            f"---\n{_BASE_FM}"
            "valid_from: 2026-01-01T00:00:00Z\n"
            "valid_to: 2026-06-01T00:00:00Z\n"
            "superseded_by: notes/02_facts/fact__nonexistent.md\n"
            "---\n\n# Old fact\n\n## Statement\n\nOld body.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 1


# ---------------------------------------------------------------------------
# null valid_from warns (not fails)
# ---------------------------------------------------------------------------


def test_null_valid_from_is_warning_not_error(tmp_path, capsys):
    """A note with valid_to but no valid_from should warn but not error."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__no_valid_from.md"
        _write(
            note,
            f"---\n{_BASE_FM}"
            # valid_to set but valid_from absent — triggers the warning path.
            "valid_to: 2026-06-01T00:00:00Z\n"
            "---\n\n# Fact\n\n## Statement\n\nFact body.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
        out = capsys.readouterr().out
    finally:
        reset_config()

    # Must not be an error.
    assert rc == 0
    # Must emit a warning mentioning valid_from.
    assert "valid_from" in out
    assert "ledger migrate bitemporal --apply" in out


# ---------------------------------------------------------------------------
# Bad date format
# ---------------------------------------------------------------------------


def test_bad_valid_from_format_fails(tmp_path):
    """A valid_from value that does not match ISO-8601 UTC is an error."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__bad_date.md"
        _write(
            note,
            f"---\n{_BASE_FM}"
            "valid_from: 2026-01-01\n"  # date-only, not datetime
            "---\n\n# Fact\n\n## Statement\n\nFact body.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 1


def test_bad_valid_to_format_fails(tmp_path):
    """A valid_to value that does not match ISO-8601 UTC is an error."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__bad_valid_to.md"
        _write(
            note,
            f"---\n{_BASE_FM}"
            "valid_from: 2026-01-01T00:00:00Z\n"
            "valid_to: not-a-date\n"
            "---\n\n# Fact\n\n## Statement\n\nFact body.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 1


# ---------------------------------------------------------------------------
# 00_inbox is exempt from bitemporal rules
# ---------------------------------------------------------------------------


def test_inbox_note_exempt_from_bitemporal(tmp_path):
    """00_inbox notes with no valid_from/valid_to must not trigger warnings."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "00_inbox" / "inbox_note.md"
        _write(
            note,
            f"---\n{_BASE_FM}---\n\n# Inbox note\n\nSome content.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0


def test_inbox_note_with_bad_bitemporal_dates_exempt(tmp_path, capsys):
    """Even bad bitemporal dates in 00_inbox should not cause lint errors."""
    config = _make_temp_config(tmp_path)
    try:
        note = config.ledger_notes_dir / "00_inbox" / "inbox_bad_dates.md"
        _write(
            note,
            f"---\n{_BASE_FM}"
            # These would normally error on a fact note but inbox is exempt.
            "valid_from: not-a-date\n"
            "---\n\n# Inbox note\n\nSome content.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0


# ---------------------------------------------------------------------------
# 09_archive notes with full bitemporal fields lint clean
# ---------------------------------------------------------------------------


def test_archive_note_with_valid_bitemporal_passes(tmp_path):
    """A 09_archive note with valid bitemporal fields should lint clean."""
    config = _make_temp_config(tmp_path)
    try:
        new_note = config.ledger_notes_dir / "02_facts" / "fact__current.md"
        _write(
            new_note,
            f"---\n{_BASE_FM}---\n\n# Current fact\n\n## Statement\n\nCurrent.\n",
        )
        archive_note = config.ledger_notes_dir / "09_archive" / "fact__archived.md"
        _write(
            archive_note,
            f"---\n{_BASE_FM}"
            "valid_from: 2025-01-01T00:00:00Z\n"
            "valid_to: 2026-01-01T00:00:00Z\n"
            "superseded_by: notes/02_facts/fact__current.md\n"
            "---\n\n# Archived fact\n\n## Statement\n\nOld.\n",
        )
        _write(config.timeline_path, _EMPTY_TIMELINE)

        rc = maintenance.cmd_lint()
    finally:
        reset_config()

    assert rc == 0

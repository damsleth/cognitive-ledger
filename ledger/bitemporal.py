"""Bitemporal (valid-time) primitives for Cognitive Ledger.

Implements the valid-time axis alongside transaction time (created/updated).
This module is THE single source of truth for bitemporal interval semantics.

Key concepts:
- valid_from: when the fact became true in the world (optional).
- valid_to:   when the fact stopped being true (null = still true).
- superseded_by: logical path of the replacement note (null or set).
- supersedes:    list of logical paths this note replaces (may be empty).

Null valid_from on a legacy note means "valid for all time" — it is never
silently dropped, but a one-time migration warning is emitted by the linter.

All date fields use the TIMESTAMP_PATTERN format: YYYY-MM-DDTHH:MM:SSZ (UTC).
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.errors import NoteNotFoundError, NoteError
from ledger.io import safe_write_text
from ledger.layout import logical_path, logical_note_path
from ledger.parsing.frontmatter import (
    parse_frontmatter_text,
    serialize_frontmatter,
)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now_utc() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


def _to_iso(d: dt.datetime) -> str:
    """Format a datetime as YYYY-MM-DDTHH:MM:SSZ (UTC, second precision)."""
    return d.astimezone(dt.timezone.utc).strftime(_TIMESTAMP_FMT)


def _from_iso(s: str) -> dt.datetime | None:
    """Parse an ISO 8601 UTC timestamp string; return None on failure."""
    if not s:
        return None
    try:
        return dt.datetime.strptime(s, _TIMESTAMP_FMT).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def _date_to_datetime(d: dt.date) -> dt.datetime:
    """Promote a date to a midnight UTC datetime."""
    return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# Pure helpers — interval semantics (single source of truth for Gap A/B)
# ---------------------------------------------------------------------------


def parse_validity(frontmatter: dict[str, Any]) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Extract (valid_from, valid_to) as timezone-aware datetimes or None.

    Accepts both YYYY-MM-DDTHH:MM:SSZ (full timestamp) and YYYY-MM-DD (date
    only) strings, promoting the latter to midnight UTC.

    Args:
        frontmatter: Raw frontmatter dict from parse_frontmatter_text.

    Returns:
        Tuple (valid_from, valid_to); either element may be None.
        None means the bound is absent (open at that end).
    """
    def _parse_field(key: str) -> dt.datetime | None:
        raw = str(frontmatter.get(key, "")).strip()
        for sentinel in ("null", "~", "None", ""):
            if raw == sentinel:
                return None
        # Try full timestamp first.
        parsed = _from_iso(raw)
        if parsed is not None:
            return parsed
        # Try date-only.
        try:
            d = dt.date.fromisoformat(raw)
            return _date_to_datetime(d)
        except ValueError:
            return None

    return _parse_field("valid_from"), _parse_field("valid_to")


def is_valid_at(frontmatter: dict[str, Any], instant: dt.datetime) -> bool:
    """Return True if the note is valid at *instant* given its valid-time interval.

    Semantics:
    - null valid_from  → valid from the beginning of time (open left bound).
    - null valid_to    → currently valid (open right bound).
    - instant is inclusive on both ends: valid_from <= instant <= valid_to.

    Args:
        frontmatter: Raw frontmatter dict.
        instant:     A timezone-aware datetime to test against.

    Returns:
        True if the note is valid at *instant*.
    """
    valid_from, valid_to = parse_validity(frontmatter)
    instant_utc = instant.astimezone(dt.timezone.utc)
    if valid_from is not None and instant_utc < valid_from:
        return False
    if valid_to is not None and instant_utc > valid_to:
        return False
    return True


# ---------------------------------------------------------------------------
# Supersession result
# ---------------------------------------------------------------------------


@dataclass
class SupersessionResult:
    """Structured result returned by supersede().

    Attributes:
        old_ref:        Logical notes/... path of the archived note.
        new_ref:        Logical notes/... path of the replacement note.
        archive_path:   Absolute path where the old note now lives.
        valid_to_set:   ISO timestamp written to old.valid_to.
        idempotent:     True when this call was a no-op (already superseded).
    """

    old_ref: str
    new_ref: str
    archive_path: Path
    valid_to_set: str
    idempotent: bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_note(
    ref: str,
    *,
    ledger_notes_dir: Path,
) -> Path:
    """Resolve a logical notes/... path to an absolute path.

    Raises NoteNotFoundError if the file does not exist.
    """
    if ref.startswith("notes/"):
        abs_path = ledger_notes_dir / ref[len("notes/"):]
    else:
        abs_path = ledger_notes_dir / ref
    abs_path = abs_path.resolve()
    if not abs_path.exists():
        raise NoteNotFoundError(ref)
    return abs_path


def _logical_ref(abs_path: Path, ledger_notes_dir: Path) -> str:
    """Return the canonical logical notes/... string for *abs_path*."""
    rel = logical_note_path(abs_path, ledger_notes_dir)
    if rel is not None:
        return str(rel)
    return str(abs_path)


# ---------------------------------------------------------------------------
# supersede()
# ---------------------------------------------------------------------------


def supersede(
    old_ref: str,
    new_ref: str,
    *,
    as_of: dt.datetime | dt.date | str | None = None,
    reason: str | None = None,
) -> SupersessionResult:
    """Supersede *old_ref* with *new_ref* and archive the old note.

    Steps performed (all atomic, with timeline + signal events):
    1. Resolve and validate both note paths.
    2. Guard: old == new is an error.
    3. Idempotency: if old already has superseded_by == new_ref and lives in
       09_archive, return a SupersessionResult with idempotent=True immediately.
    4. Determine valid_to: as_of arg → new.valid_from → today midnight UTC.
    5. Write old note: valid_to = determined timestamp, superseded_by = new_ref.
    6. Write new note: prepend old_ref to the supersedes list.
    7. Move old note to notes/09_archive/ (keeping original filename).
    8. Append timeline entry with action="archived".
    9. Append signal event (type="supersession").

    Args:
        old_ref:  Logical notes/... path of the note being replaced.
        new_ref:  Logical notes/... path of the replacement note.
        as_of:    When the old note stopped being valid. Accepts a timezone-aware
                  datetime, a date (promoted to midnight UTC), or an ISO string.
                  Defaults to new.valid_from if set, else today midnight UTC.
        reason:   Optional free-text reason stored in the signal event.

    Returns:
        SupersessionResult with details of what was done.

    Raises:
        NoteNotFoundError:  If old_ref or new_ref do not resolve to existing files.
        NoteError:          If old_ref == new_ref.
        NoteError:          If the archive directory cannot be created.
    """
    config = get_config()
    ledger_notes_dir = config.ledger_notes_dir

    # --- 1. Resolve paths ---
    old_abs = _resolve_note(old_ref, ledger_notes_dir=ledger_notes_dir)
    new_abs = _resolve_note(new_ref, ledger_notes_dir=ledger_notes_dir)

    # Canonical logical refs (always notes/... format)
    old_logical = _logical_ref(old_abs, ledger_notes_dir)
    new_logical = _logical_ref(new_abs, ledger_notes_dir)

    # --- 2. Guard: old == new ---
    if old_abs.resolve() == new_abs.resolve():
        raise NoteError(
            f"supersede: old_ref and new_ref resolve to the same file: {old_logical}"
        )

    # --- 3. Read old frontmatter ---
    old_text = old_abs.read_text(encoding="utf-8")
    old_fm, old_body = parse_frontmatter_text(old_text)

    # Normalise existing superseded_by
    existing_superseded_by = str(old_fm.get("superseded_by", "")).strip()
    for sentinel in ("null", "~", "None", ""):
        if existing_superseded_by == sentinel:
            existing_superseded_by = ""
            break

    archive_dir = ledger_notes_dir / "09_archive"
    archive_path = archive_dir / old_abs.name

    # --- 3b. Idempotency check ---
    already_in_archive = "/09_archive/" in str(old_abs)
    already_superseded = existing_superseded_by == new_logical
    if already_in_archive and already_superseded:
        return SupersessionResult(
            old_ref=old_logical,
            new_ref=new_logical,
            archive_path=archive_path,
            valid_to_set=str(old_fm.get("valid_to", "")),
            idempotent=True,
        )

    # --- 4. Determine valid_to ---
    if as_of is not None:
        if isinstance(as_of, str):
            parsed_as_of = _from_iso(as_of)
            if parsed_as_of is None:
                # Try date-only
                try:
                    parsed_as_of = _date_to_datetime(dt.date.fromisoformat(as_of))
                except ValueError:
                    raise NoteError(f"supersede: invalid as_of value: {as_of!r}")
            valid_to_dt = parsed_as_of
        elif isinstance(as_of, dt.datetime):
            valid_to_dt = as_of.astimezone(dt.timezone.utc)
        else:
            # dt.date
            valid_to_dt = _date_to_datetime(as_of)
    else:
        # Use new note's valid_from if available
        new_text = new_abs.read_text(encoding="utf-8")
        new_fm_for_valid, _ = parse_frontmatter_text(new_text)
        new_valid_from, _ = parse_validity(new_fm_for_valid)
        if new_valid_from is not None:
            valid_to_dt = new_valid_from
        else:
            today = _now_utc().date()
            valid_to_dt = _date_to_datetime(today)

    valid_to_iso = _to_iso(valid_to_dt)

    # --- 5. Write old note: set valid_to, superseded_by, updated ---
    old_fm["valid_to"] = valid_to_iso
    old_fm["superseded_by"] = new_logical
    old_fm["updated"] = _to_iso(_now_utc())
    old_content = serialize_frontmatter(old_fm) + "\n" + old_body.lstrip("\n")
    safe_write_text(old_abs, old_content)

    # --- 6. Write new note: prepend old to supersedes list ---
    new_text = new_abs.read_text(encoding="utf-8")
    new_fm, new_body = parse_frontmatter_text(new_text)

    existing_supersedes = new_fm.get("supersedes", [])
    if isinstance(existing_supersedes, str):
        existing_supersedes = [s.strip() for s in existing_supersedes.split(",") if s.strip()]
    elif not isinstance(existing_supersedes, list):
        existing_supersedes = []
    # Remove empty/null sentinels
    existing_supersedes = [
        s for s in existing_supersedes
        if s and s not in ("null", "~", "None")
    ]
    if old_logical not in existing_supersedes:
        existing_supersedes.insert(0, old_logical)
    new_fm["supersedes"] = existing_supersedes
    new_fm["updated"] = _to_iso(_now_utc())
    new_content = serialize_frontmatter(new_fm) + "\n" + new_body.lstrip("\n")
    safe_write_text(new_abs, new_content)

    # --- 7. Move old note to 09_archive ---
    archive_dir.mkdir(parents=True, exist_ok=True)
    if archive_path.exists() and archive_path.resolve() != old_abs.resolve():
        # Target already occupied (edge case: file with same name in archive)
        raise NoteError(
            f"supersede: archive target already exists: {archive_path}. "
            "Rename one of the notes before superseding."
        )
    if old_abs.resolve() != archive_path.resolve():
        shutil.move(str(old_abs), str(archive_path))

    # --- 8. Append timeline entry ---
    from ledger.embeddings import append_timeline_entry  # deferred to avoid circular

    append_timeline_entry(
        action="archived",
        rel_path=old_logical,
        description=f"superseded by {new_logical}" + (f" ({reason})" if reason else ""),
    )

    # --- 9. Append signal event ---
    from ledger.signals import append_signal  # deferred

    signal_detail: dict[str, Any] = {
        "old": old_logical,
        "new": new_logical,
    }
    if reason:
        signal_detail["reason"] = reason
    append_signal(
        "supersession",
        note=old_logical,
        detail=json.dumps(signal_detail, ensure_ascii=False),
    )

    return SupersessionResult(
        old_ref=old_logical,
        new_ref=new_logical,
        archive_path=archive_path,
        valid_to_set=valid_to_iso,
        idempotent=False,
    )

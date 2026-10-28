"""Note and timeline validation."""
from __future__ import annotations

import json
from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any
from ledger.layout import ARCHIVE_SUBDIR, BITEMPORAL_SUBDIRS, INBOX_SUBDIR, NOTE_LAYOUTS, NOTE_PREFIXES
from ledger.config import get_config
from ledger.parsing import parse_frontmatter_text
from .inventory import _config_paths, _iter_note_files, _relative
from .sync import TIMELINE_LINE_PATTERN, _timeline_entries


@dataclass
class LintCounters:
    errors: int = 0
    warnings: int = 0
    warn_large_files: int = 0
    warn_inferred_confidence: int = 0
    warn_loop_missing_next_action: int = 0
    warn_loop_missing_checkbox: int = 0
    warn_placeholder_links: int = 0
    warn_timeline_wildcard: int = 0
    warn_bitemporal_null_valid_from: int = 0


TAG_PATTERN = re.compile(r"^[a-zæøå][a-zæøå0-9_-]*$")


TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


LARGE_FILE_WORD_THRESHOLD = 400


_ATTRIBUTE_SLOT_PATTERN = re.compile(r"^[a-z0-9æøå][a-z0-9æøå_.-]*$")


def _lint_error(path: Path, message: str) -> None:
    print(f"ERROR: {_relative(path)} - {message}")


def _lint_warn(path: Path, message: str) -> None:
    print(f"WARN: {_relative(path)} - {message}")


_BITEMPORAL_ELIGIBLE_FOLDERS = frozenset(f"/{subdir}/" for subdir in BITEMPORAL_SUBDIRS)


def _lint_bitemporal(
    path: Path,
    frontmatter: dict[str, Any],
    fm_raw_value: Any,
    counters: LintCounters,
) -> None:
    """Validate bitemporal (valid-time) frontmatter fields.

    Rules (from GAP A spec):
    - valid_from and valid_to must match TIMESTAMP_PATTERN when present.
    - valid_from <= valid_to when both set (error).
    - null valid_to is OK (open interval, currently valid).
    - null valid_from on a note that has other bitemporal fields, or on a
      fact-like note, emits a WARN (not an error) suggesting migration.
    - superseded_by must resolve to an existing note (dangling = error).
    - superseded_by set requires non-null valid_to (error).
    - 00_inbox notes are exempt (no rules applied).
    """
    rel = _relative(path)
    # 00_inbox is exempt from bitemporal validation entirely.
    if f"/{INBOX_SUBDIR}/" in f"/{rel}":
        return

    is_eligible = any(folder in f"/{rel}" for folder in _BITEMPORAL_ELIGIBLE_FOLDERS)

    valid_from_raw = fm_raw_value("valid_from") or str(frontmatter.get("valid_from", "")).strip()
    valid_to_raw = fm_raw_value("valid_to") or str(frontmatter.get("valid_to", "")).strip()
    superseded_by_raw = (
        fm_raw_value("superseded_by") or str(frontmatter.get("superseded_by", "")).strip()
    )

    # Normalise YAML null representations to empty string.
    for sentinel in ("null", "~", "None"):
        if valid_from_raw == sentinel:
            valid_from_raw = ""
        if valid_to_raw == sentinel:
            valid_to_raw = ""
        if superseded_by_raw == sentinel:
            superseded_by_raw = ""

    has_any_bitemporal = bool(valid_from_raw or valid_to_raw or superseded_by_raw)

    # Validate valid_from format.
    if valid_from_raw:
        if not TIMESTAMP_PATTERN.match(valid_from_raw):
            _lint_error(path, f"invalid valid_from timestamp: {valid_from_raw}")
            counters.errors += 1

    # Validate valid_to format.
    if valid_to_raw:
        if not TIMESTAMP_PATTERN.match(valid_to_raw):
            _lint_error(path, f"invalid valid_to timestamp: {valid_to_raw}")
            counters.errors += 1

    # valid_from <= valid_to when both present and valid.
    if valid_from_raw and valid_to_raw:
        if (
            TIMESTAMP_PATTERN.match(valid_from_raw)
            and TIMESTAMP_PATTERN.match(valid_to_raw)
            and valid_from_raw > valid_to_raw
        ):
            _lint_error(
                path,
                f"valid_from ({valid_from_raw}) is after valid_to ({valid_to_raw})",
            )
            counters.errors += 1

    # superseded_by requires valid_to.
    if superseded_by_raw and not valid_to_raw:
        _lint_error(path, "superseded_by is set but valid_to is missing")
        counters.errors += 1

    # superseded_by must resolve to an existing note.
    if superseded_by_raw:
        config = get_config()
        # superseded_by should be a logical notes/... path.
        if superseded_by_raw.startswith("notes/"):
            target_abs = (
                config.ledger_notes_dir / superseded_by_raw[len("notes/"):]
            )
        else:
            target_abs = config.ledger_notes_dir / superseded_by_raw
        if not target_abs.exists():
            _lint_error(path, f"superseded_by references non-existent note: {superseded_by_raw}")
            counters.errors += 1

    # A typed archive note is embedded for --as-of reads; without valid_to it
    # reads as valid for all time, so a closed loop or retired fact comes back
    # as current history. (Untyped note__ ingest summaries are not embedded.)
    if f"/{ARCHIVE_SUBDIR}/" in f"/{rel}" and not valid_to_raw:
        if path.name.startswith(NOTE_PREFIXES):
            _lint_warn(path, "archived note has no valid_to; run `ledger migrate bitemporal --check`")
            counters.warnings += 1

    # Warn when valid_from is absent on eligible notes that carry other
    # bitemporal fields, or on any eligible note (migration suggestion).
    if is_eligible and not valid_from_raw and has_any_bitemporal:
        _lint_warn(path, "valid_from is null; consider running `ledger migrate bitemporal --apply` to back-fill")
        counters.warnings += 1
        counters.warn_bitemporal_null_valid_from += 1


def _lint_note(path: Path, counters: LintCounters) -> None:
    from ledger.validation import validate_frontmatter_fields, validate_note_body

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        _lint_error(path, "missing frontmatter")
        counters.errors += 1
        return

    frontmatter, body = parse_frontmatter_text(text)
    if not frontmatter:
        _lint_error(path, "missing or invalid frontmatter")
        counters.errors += 1
        return

    # Preserve scalar spellings from source frontmatter (e.g. lang: no)
    # because canonical parse_scalar maps YAML "no" -> False.
    try:
        fm_end = lines[1:].index("---") + 1
    except ValueError:
        fm_end = min(len(lines), 40)
    fm_lines = lines[1:fm_end]

    def fm_raw_value(key: str) -> str:
        prefix = f"{key}:"
        for raw_line in fm_lines:
            stripped = raw_line.strip()
            if not stripped.startswith(prefix):
                continue
            value = stripped.split(":", 1)[1].split("#", 1)[0].strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            if value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            return value
        return ""

    # Build a raw-preferred frontmatter view for the pure validation helpers.
    # Scalar fields that YAML may coerce (e.g. lang: no -> False) are overridden
    # with the verbatim string read from the source lines.
    _scalar_fields = ["created", "updated", "source", "scope", "lang", "status", "via", "confidence"]
    fm_for_validation: dict[str, Any] = dict(frontmatter)
    for _f in _scalar_fields:
        raw = fm_raw_value(_f)
        if raw:
            fm_for_validation[_f] = raw

    rel = _relative(path)

    # --- Delegate field validation to validation.py ---
    for issue in validate_frontmatter_fields(fm_for_validation, rel):
        if "[warning]" in issue:
            _lint_warn(path, issue.replace(" [warning]", ""))
            counters.warnings += 1
            if "inferred" in issue and "confidence" in issue:
                counters.warn_inferred_confidence += 1
        else:
            _lint_error(path, issue)
            counters.errors += 1

    # --- Delegate body validation to validation.py ---
    for issue in validate_note_body(body, fm_for_validation, rel, LARGE_FILE_WORD_THRESHOLD):
        _lint_warn(path, issue.replace(" [warning]", ""))
        counters.warnings += 1
        if "large file" in issue:
            counters.warn_large_files += 1
        elif "Next action" in issue and "missing" in issue:
            counters.warn_loop_missing_next_action += 1
        elif "checkbox" in issue:
            counters.warn_loop_missing_checkbox += 1
        elif "placeholder" in issue:
            counters.warn_placeholder_links += 1

    # --- Bitemporal (valid-time) field validation ---
    # Applicable to fact-like types (01_identity, 02_facts, 03_preferences,
    # 04_goals, 06_concepts, 09_archive). 00_inbox notes are exempt.
    _lint_bitemporal(path, frontmatter, fm_raw_value, counters)

    # --- Attribute slot (plan 09): optional; when present, a lowercase slug
    # so two notes about the same slot collide on an exact string match.
    slot = str(frontmatter.get("attribute", "") or "").strip()
    if slot and not _ATTRIBUTE_SLOT_PATTERN.match(slot):
        _lint_error(path, f"invalid attribute slot (want a lowercase slug like `employer` or `kim.residence`): {slot}")
        counters.errors += 1


def _lint_timeline(timeline: Path, counters: LintCounters) -> None:
    from ledger.layout import TIMELINE_JSONL_FILENAME
    from ledger.timeline import check_timeline_jsonl, timeline_markdown_is_current

    # timeline.jsonl is the source of truth; timeline.md is generated from it.
    print("\nValidating timeline.jsonl...")
    jsonl = timeline.with_name(TIMELINE_JSONL_FILENAME)
    if not jsonl.is_file():
        print("WARN: timeline.jsonl not found")
        counters.warnings += 1
    else:
        jsonl_errors, jsonl_warnings = check_timeline_jsonl(jsonl)
        for issue in jsonl_errors:
            print(f"ERROR: {issue}")
        for issue in jsonl_warnings:
            print(f"WARN: {issue}")
        counters.errors += len(jsonl_errors)
        counters.warnings += len(jsonl_warnings)
        if not timeline_markdown_is_current(jsonl, timeline):
            print("WARN: timeline.md is missing or out of date (run: ledger --doctor --fix)")
            counters.warnings += 1

    print("\nValidating timeline...")
    if not timeline.is_file():
        print("WARN: timeline.md not found")
        counters.warnings += 1
        return

    lines = timeline.read_text(encoding="utf-8").splitlines()
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("Format:"):
            continue
        if stripped.startswith("Append-only"):
            continue
        if stripped == "---":
            continue
        if not stripped[0].isdigit():
            continue

        match = TIMELINE_LINE_PATTERN.match(stripped)
        if not match:
            print(f"ERROR: timeline:{lineno} - invalid format")
            counters.errors += 1
            continue

        _ts, _action, path_field, _desc = match.groups()
        if "*" in path_field:
            print(f"WARN: timeline:{lineno} - wildcard path detected: {path_field}")
            counters.warnings += 1
            counters.warn_timeline_wildcard += 1

    print("\nValidating timeline order...")
    prev_ts = ""
    for _lineno, ts, _action, _path, _desc in _timeline_entries(timeline):
        if prev_ts and ts < prev_ts:
            print(f"ERROR: timeline out of order: {ts} comes after {prev_ts}")
            counters.errors += 1
        prev_ts = ts


def _lint_identity_count(notes_dir: Path, counters: LintCounters) -> None:
    """Identity notes load at every boot, so their number is capped (types may repeat)."""
    from ledger.schema_values import MAX_IDENTITY_NOTES

    identity = sorted((Path(notes_dir) / NOTE_LAYOUTS['identity'].subdir).glob("*.md"))
    if len(identity) > MAX_IDENTITY_NOTES:
        print(f"WARN: {NOTE_LAYOUTS['identity'].subdir} - {len(identity)} identity notes (max {MAX_IDENTITY_NOTES}); merge or demote some")
        counters.warnings += 1


def cmd_lint(as_json: bool = False) -> int:
    import io
    import contextlib

    notes_dir, _indices_dir, timeline = _config_paths()

    if as_json:
        buf = io.StringIO()
        counters = LintCounters()
        with contextlib.redirect_stdout(buf):
            for path in _iter_note_files(include_indices=False):
                _lint_note(path, counters)
            _lint_timeline(timeline, counters)
            _lint_identity_count(notes_dir, counters)

        issues: list[dict[str, str]] = []
        for line in buf.getvalue().splitlines():
            line = line.strip()
            if line.startswith("ERROR:"):
                rest = line[len("ERROR:"):].strip()
                parts = rest.split(" - ", 1)
                issues.append({
                    "level": "error",
                    "path": parts[0].strip() if len(parts) > 1 else "",
                    "message": parts[1].strip() if len(parts) > 1 else rest,
                })
            elif line.startswith("WARN:"):
                rest = line[len("WARN:"):].strip()
                parts = rest.split(" - ", 1)
                issues.append({
                    "level": "warn",
                    "path": parts[0].strip() if len(parts) > 1 else "",
                    "message": parts[1].strip() if len(parts) > 1 else rest,
                })

        payload = {
            "issues": issues,
            "errors": counters.errors,
            "warnings": counters.warnings,
            "warning_summary": {
                "large_files": counters.warn_large_files,
                "inferred_high_confidence": counters.warn_inferred_confidence,
                "open_loop_missing_next_action_section": counters.warn_loop_missing_next_action,
                "open_loop_missing_next_action_checkbox": counters.warn_loop_missing_checkbox,
                "placeholder_links": counters.warn_placeholder_links,
                "timeline_wildcard_paths": counters.warn_timeline_wildcard,
                "bitemporal_null_valid_from": counters.warn_bitemporal_null_valid_from,
            },
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 1 if counters.errors > 0 else 0

    print("=== Lint ===")
    counters = LintCounters()
    for path in _iter_note_files(include_indices=False):
        _lint_note(path, counters)

    _lint_timeline(timeline, counters)
    _lint_identity_count(notes_dir, counters)

    print("\nErrors:", counters.errors)
    print("Warnings:", counters.warnings)
    print("\nWarning summary:")
    print(f"  large_files: {counters.warn_large_files}")
    print(f"  inferred_high_confidence: {counters.warn_inferred_confidence}")
    print(f"  open_loop_missing_next_action_section: {counters.warn_loop_missing_next_action}")
    print(f"  open_loop_missing_next_action_checkbox: {counters.warn_loop_missing_checkbox}")
    print(f"  placeholder_links: {counters.warn_placeholder_links}")
    print(f"  timeline_wildcard_paths: {counters.warn_timeline_wildcard}")
    print(f"  bitemporal_null_valid_from: {counters.warn_bitemporal_null_valid_from}")

    return 1 if counters.errors > 0 else 0

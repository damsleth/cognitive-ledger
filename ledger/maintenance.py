"""Python maintenance CLI for Electric Sheep workflows.

This module replaces the legacy bash `scripts/sheep` implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger import context as context_lib
from ledger.layout import indices_dir as layout_indices_dir, logical_path
from ledger.parsing import (
    parse_frontmatter_text,
    parse_sections,
    parse_timestamp,
    normalize_tags,
    extract_title,
    strip_private_tags,
)
from ledger import retrieval as retrieval_lib
from ledger import timeline as timeline_lib


TAG_PATTERN = re.compile(r"^[a-zæøå][a-zæøå0-9_-]*$")
TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
TIMELINE_LINE_PATTERN = re.compile(
    r"^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z) \| (created|updated|archived|deleted|closed|sleep) \| (.+?) \| (.*)$"
)

from ledger.schema_values import (
    LANG_VALUES,
    SCOPE_VALUES,
    SOURCE_VALUES,
    STATUS_VALUES,
    VIA_VALUES,
)

LARGE_FILE_WORD_THRESHOLD = 400
SYNC_STATE_VERSION = 1


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


def _config_paths() -> tuple[Path, Path, Path]:
    config = get_config()
    notes_dir = config.ledger_notes_dir
    indices_dir = layout_indices_dir(notes_dir)
    timeline = config.timeline_path
    return notes_dir, indices_dir, timeline


def _relative(path: Path) -> str:
    config = get_config()
    return logical_path(
        path,
        ledger_root=config.ledger_root,
        ledger_notes_dir=config.ledger_notes_dir,
    ).as_posix()


def _iter_note_files(include_indices: bool = False):
    notes_dir, _indices_dir, _timeline = _config_paths()
    for path in sorted(notes_dir.rglob("*.md")):
        rel = _relative(path)
        if not include_indices and "/08_indices/" in f"/{rel}":
            continue
        if "/templates/" in f"/{rel}":
            continue
        if path.name == ".gitkeep":
            continue
        yield path


def _timeline_entries(timeline_path: Path) -> list[tuple[int, str, str, str, str]]:
    entries: list[tuple[int, str, str, str, str]] = []
    if not timeline_path.is_file():
        return entries

    for lineno, line in enumerate(timeline_path.read_text(encoding="utf-8").splitlines(), start=1):
        match = TIMELINE_LINE_PATTERN.match(line.strip())
        if not match:
            continue
        ts, action, path, desc = match.groups()
        entries.append((lineno, ts, action, path, desc))
    return entries


def _sync_state_path() -> Path:
    _notes_dir, indices_dir, _timeline = _config_paths()
    return indices_dir / "sync_state.json"


def _sha256_path(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _tracked_note_snapshot() -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in _iter_note_files(include_indices=False):
        snapshot[_relative(path)] = _sha256_path(path)
    return snapshot


def _load_sync_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"_invalid": True}
    if not isinstance(raw, dict):
        return {"_invalid": True}
    return raw


def _is_tracked_path(rel_path: str) -> bool:
    normalized = rel_path.strip().lstrip("./")
    if not normalized.startswith("notes/"):
        return False
    if not normalized.endswith(".md"):
        return False
    wrapped = f"/{normalized}"
    if "/08_indices/" in wrapped:
        return False
    if "/templates/" in wrapped:
        return False
    if normalized.endswith("/.gitkeep") or normalized.endswith(".gitkeep"):
        return False
    return True


def _compute_sync_report() -> dict[str, Any]:
    _notes_dir, _indices_dir, timeline_path = _config_paths()
    timeline_entries = _timeline_entries(timeline_path)
    current_snapshot = _tracked_note_snapshot()
    state_path = _sync_state_path()
    state = _load_sync_state(state_path)

    report: dict[str, Any] = {
        "state_path": _relative(state_path),
        "state_exists": state is not None and not state.get("_invalid", False) if isinstance(state, dict) else False,
        "state_invalid": isinstance(state, dict) and bool(state.get("_invalid", False)),
        "last_synced_at": "",
        "state_version": None,
        "tracked_count": len(current_snapshot),
        "timeline_total": len(timeline_entries),
        "timeline_since_count": 0,
        "timeline_rewound": False,
        "added": [],
        "removed": [],
        "modified": [],
        "drift_paths": [],
        "logged_paths": [],
        "unlogged_paths": [],
        "timeline_only_paths": [],
    }

    if not report["state_exists"]:
        return report

    report["state_version"] = state.get("version")
    report["last_synced_at"] = str(state.get("last_synced_at", "")).strip()

    prev_files_raw = state.get("files", {})
    prev_files: dict[str, str] = {}
    if isinstance(prev_files_raw, dict):
        for raw_key, raw_hash in prev_files_raw.items():
            key = str(raw_key).strip()
            value = str(raw_hash).strip()
            if not key:
                continue
            prev_files[key] = value

    previous_paths = set(prev_files.keys())
    current_paths = set(current_snapshot.keys())
    added = sorted(current_paths - previous_paths)
    removed = sorted(previous_paths - current_paths)
    modified = sorted(
        path
        for path in (current_paths & previous_paths)
        if current_snapshot[path] != prev_files.get(path, "")
    )
    drift_paths = sorted(set(added) | set(removed) | set(modified))

    baseline_count_raw = state.get("timeline_event_count", 0)
    try:
        baseline_count = max(0, int(baseline_count_raw))
    except (TypeError, ValueError):
        baseline_count = 0

    if baseline_count > len(timeline_entries):
        report["timeline_rewound"] = True
        timeline_since = timeline_entries
    else:
        timeline_since = timeline_entries[baseline_count:]

    logged_paths = sorted(
        {
            path_field.strip()
            for _lineno, _ts, _action, path_field, _desc in timeline_since
            if _is_tracked_path(path_field)
        }
    )
    unlogged_paths = sorted(path for path in drift_paths if path not in logged_paths)
    timeline_only_paths = sorted(path for path in logged_paths if path not in drift_paths)

    report["timeline_since_count"] = len(timeline_since)
    report["added"] = added
    report["removed"] = removed
    report["modified"] = modified
    report["drift_paths"] = drift_paths
    report["logged_paths"] = logged_paths
    report["unlogged_paths"] = unlogged_paths
    report["timeline_only_paths"] = timeline_only_paths
    return report


def _write_sync_state() -> dict[str, Any]:
    _notes_dir, indices_dir, timeline_path = _config_paths()
    indices_dir.mkdir(parents=True, exist_ok=True)

    timeline_entries = _timeline_entries(timeline_path)
    snapshot = _tracked_note_snapshot()
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload: dict[str, Any] = {
        "version": SYNC_STATE_VERSION,
        "last_synced_at": now_ts,
        "timeline_event_count": len(timeline_entries),
        "last_timeline_timestamp": timeline_entries[-1][1] if timeline_entries else "",
        "tracked_file_count": len(snapshot),
        "files": snapshot,
    }
    path = _sync_state_path()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _status_payload() -> dict:
    """Build the structured status report shared by human and --json paths."""
    _notes_dir, _indices_dir, timeline = _config_paths()
    payload = {
        "timeline_path": str(timeline),
        "timeline_exists": timeline.is_file(),
        "last_sleep": None,
        "entries_total": 0,
        "changes_since": 0,
        "days_since": 0,
        "sync_drift": "unknown",
        "sleep_recommended": False,
    }
    if not timeline.is_file():
        payload["sleep_recommended"] = True
        payload["sleep_recommendation_reason"] = "timeline_missing"
        return payload
    entries = _timeline_entries(timeline)
    payload["entries_total"] = len(entries)
    if not entries:
        payload["sleep_recommended"] = True
        payload["sleep_recommendation_reason"] = "first_run"
        return payload
    sleep_positions = [idx for idx, entry in enumerate(entries) if entry[2] == "sleep"]
    if not sleep_positions:
        payload["sleep_recommended"] = True
        payload["sleep_recommendation_reason"] = "first_run"
        return payload
    sleep_idx = sleep_positions[-1]
    last_sleep = entries[sleep_idx]
    last_sleep_ts = last_sleep[1]
    payload["last_sleep"] = last_sleep_ts
    payload["changes_since"] = max(0, len(entries) - sleep_idx - 1)
    last_dt = parse_timestamp(last_sleep_ts)
    now_dt = datetime.now(timezone.utc)
    days_since = 0
    if last_dt is not None:
        days_since = max(0, (now_dt.date() - last_dt.date()).days)
    payload["days_since"] = days_since
    sync_report = _compute_sync_report()
    if sync_report["state_invalid"]:
        payload["sync_drift"] = "state_invalid"
    elif not sync_report["state_exists"]:
        payload["sync_drift"] = "unknown"
    elif sync_report["timeline_rewound"]:
        payload["sync_drift"] = "timeline_rewound"
    elif sync_report["unlogged_paths"]:
        payload["sync_drift"] = "unlogged_changes"
        payload["unlogged_change_count"] = len(sync_report["unlogged_paths"])
    else:
        payload["sync_drift"] = "clean"
    payload["sleep_recommended"] = (days_since >= 7 or payload["changes_since"] >= 25)
    return payload


def cmd_status(as_json: bool = False) -> int:
    payload = _status_payload()
    if as_json:
        import json as _json
        # Data class: raw doc, no top-level `ok`.
        print(_json.dumps(payload, ensure_ascii=False))
        return 0

    print("=== Sleep Status ===")
    if not payload["timeline_exists"]:
        print(f"Timeline not found at {_relative(Path(payload['timeline_path']))}")
        print("-> Run 'sheep index' first")
        return 0
    if payload["entries_total"] == 0 or payload["last_sleep"] is None:
        print("Last sleep: never")
        print(f"Total timeline entries: {payload['entries_total']}")
        print("-> Sleep recommended (first run)")
        return 0
    print(f"Last sleep: {payload['last_sleep']}")
    print(f"Changes since: {payload['changes_since']}")
    print(f"Days since: {payload['days_since']}")
    drift = payload["sync_drift"]
    if drift == "state_invalid":
        print("Sync drift: state invalid (run `sheep sync --apply`)")
    elif drift == "unknown":
        print("Sync drift: unknown (run `sheep sync --apply`)")
    elif drift == "timeline_rewound":
        print("Sync drift: timeline rewound (run `sheep sync --check`)")
    elif drift == "unlogged_changes":
        print(f"Sync drift: {payload['unlogged_change_count']} unlogged note change(s)")
    else:
        print("Sync drift: clean")
    if payload["sleep_recommended"]:
        print("-> Sleep recommended")
    else:
        print("-> No sleep needed")
    return 0


def cmd_sync(apply: bool = False) -> int:
    if apply:
        payload = _write_sync_state()
        print("=== Sync State Updated ===")
        print(f"State: {_relative(_sync_state_path())}")
        print(f"Last synced: {payload['last_synced_at']}")
        print(f"Tracked notes: {payload['tracked_file_count']}")
        print(f"Timeline event count: {payload['timeline_event_count']}")
        return 0

    report = _compute_sync_report()
    print("=== Sync Status ===")
    print(f"State: {report['state_path']}")

    if report["state_invalid"]:
        print("State is invalid JSON.")
        print("-> Run `sheep sync --apply` to reset baseline")
        return 1
    if not report["state_exists"]:
        print("State not found.")
        print("-> Run `sheep sync --apply` to create baseline")
        return 1

    print(f"Last synced: {report['last_synced_at'] or 'unknown'}")
    print(f"Tracked notes: {report['tracked_count']}")
    print(f"Timeline entries since sync: {report['timeline_since_count']}")
    if report["timeline_rewound"]:
        print("Timeline appears to have been truncated or rewritten since sync.")
    print(
        "Drift (added/modified/removed): "
        f"{len(report['added'])}/{len(report['modified'])}/{len(report['removed'])}"
    )
    print(f"Unlogged note changes: {len(report['unlogged_paths'])}")
    print(f"Timeline-only tracked events: {len(report['timeline_only_paths'])}")
    if report["unlogged_paths"]:
        print("Unlogged paths:")
        for path in report["unlogged_paths"][:20]:
            print(f"  - {path}")
    elif report["timeline_rewound"]:
        print("-> Sync inconsistent")
    else:
        print("-> Sync healthy")

    if report["timeline_rewound"] or report["unlogged_paths"]:
        return 1
    return 0


def _lint_error(path: Path, message: str) -> None:
    print(f"ERROR: {_relative(path)} - {message}")


def _lint_warn(path: Path, message: str) -> None:
    print(f"WARN: {_relative(path)} - {message}")


def _is_open_loop(path: Path, frontmatter: dict[str, Any]) -> bool:
    rel = _relative(path)
    if "/05_open_loops/" not in f"/{rel}":
        return False
    return str(frontmatter.get("status", "open")).strip().lower() == "open"


# Folders eligible for bitemporal field validation (00_inbox is exempt).
_BITEMPORAL_ELIGIBLE_FOLDERS = frozenset(
    {
        "/01_identity/",
        "/02_facts/",
        "/03_preferences/",
        "/04_goals/",
        "/06_concepts/",
        "/09_archive/",
    }
)


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
    if "/00_inbox/" in f"/{rel}":
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


def _lint_timeline(timeline: Path, counters: LintCounters) -> None:
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


def cmd_lint(as_json: bool = False) -> int:
    import io
    import contextlib

    _notes_dir, _indices_dir, timeline = _config_paths()

    if as_json:
        buf = io.StringIO()
        counters = LintCounters()
        with contextlib.redirect_stdout(buf):
            for path in _iter_note_files(include_indices=False):
                _lint_note(path, counters)
            _lint_timeline(timeline, counters)

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


def _word_count(text: str) -> int:
    return len(text.split())


def _note_word_counts() -> list[int]:
    counts: list[int] = []
    for path in _iter_note_files(include_indices=False):
        counts.append(_word_count(path.read_text(encoding="utf-8")))
    counts.sort()
    return counts


def _generate_sizes(indices_dir: Path) -> None:
    rows: list[tuple[int, str]] = []
    for path in _iter_note_files(include_indices=False):
        words = _word_count(path.read_text(encoding="utf-8"))
        rows.append((words, _relative(path)))
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)

    lines = [
        "# Note Sizes",
        "",
        "Word counts for all notes, sorted by size (largest first).",
        "Regenerated by `sheep index`.",
        "",
        "| Words | Path |",
        "|------:|:-----|",
    ]
    for words, rel in rows:
        lines.append(f"| {words} | `{rel}` |")
    from ledger.io import safe_write_text

    safe_write_text(indices_dir / "sizes.md", "\n".join(lines) + "\n", use_lock=False)

    payload = [{"words": words, "path": rel} for words, rel in rows]
    safe_write_text(indices_dir / "sizes.json", json.dumps(payload, indent=2) + "\n", use_lock=False)


def _generate_tags(indices_dir: Path) -> None:
    by_tag: dict[str, set[str]] = {}
    title_by_path: dict[str, str] = {}

    for path in _iter_note_files(include_indices=False):
        rel = _relative(path)
        frontmatter, body = parse_frontmatter_text(path.read_text(encoding="utf-8"))
        body = strip_private_tags(body)
        tags = normalize_tags(frontmatter.get("tags"))
        for tag in tags:
            by_tag.setdefault(tag, set()).add(rel)
        title_by_path[rel] = extract_title(body) or path.stem

    tags_sorted = sorted(by_tag.keys())

    md_lines = [
        "# Tag Index",
        "",
        "Notes grouped by tag. Regenerated by `sheep index`.",
        "",
    ]
    for tag in tags_sorted:
        md_lines.append(f"## {tag}")
        md_lines.append("")
        for rel in sorted(by_tag[tag]):
            title = title_by_path.get(rel, Path(rel).stem)
            md_lines.append(f"- [`{rel}`]({rel}) – {title}")
        md_lines.append("")
    from ledger.io import safe_write_text

    safe_write_text(indices_dir / "tags.md", "\n".join(md_lines) + "\n", use_lock=False)

    json_payload = {tag: sorted(paths) for tag, paths in sorted(by_tag.items())}
    safe_write_text(indices_dir / "tags.json", json.dumps(json_payload, indent=2) + "\n", use_lock=False)


def _tokenize_phrase(text: str, stopwords: set[str]) -> list[str]:
    tokens = [tok.lower() for tok in re.findall(r"[A-Za-z0-9_\\-]+", text)]
    cleaned = []
    for token in tokens:
        normalized = token.replace("_", " ").replace("-", " ").strip()
        for piece in normalized.split():
            if len(piece) < 3:
                continue
            if piece in stopwords:
                continue
            cleaned.append(piece)
    return cleaned


def _generate_alias_suggestions(indices_dir: Path) -> None:
    config = get_config()
    stopwords = set(config.stopwords)
    existing_aliases_path = indices_dir / "aliases.json"
    existing_aliases: dict[str, set[str]] = {}
    if existing_aliases_path.is_file():
        try:
            existing_raw = json.loads(existing_aliases_path.read_text(encoding="utf-8"))
            if isinstance(existing_raw, dict):
                for alias, values in existing_raw.items():
                    if not isinstance(values, list):
                        continue
                    existing_aliases[str(alias).strip().lower()] = {
                        str(value).strip().lower()
                        for value in values
                        if str(value).strip()
                    }
        except json.JSONDecodeError:
            existing_aliases = {}

    tag_cooc: dict[str, dict[str, int]] = {}
    phrase_freq: dict[str, int] = {}

    for path in _iter_note_files(include_indices=False):
        frontmatter, body = parse_frontmatter_text(path.read_text(encoding="utf-8"))
        body = strip_private_tags(body)
        tags = normalize_tags(frontmatter.get("tags"))
        unique_tags = sorted(set(tags))
        for tag_a, tag_b in combinations(unique_tags, 2):
            tag_cooc.setdefault(tag_a, {}).setdefault(tag_b, 0)
            tag_cooc.setdefault(tag_b, {}).setdefault(tag_a, 0)
            tag_cooc[tag_a][tag_b] += 1
            tag_cooc[tag_b][tag_a] += 1

        sections = parse_sections(body)
        title = extract_title(body) or path.stem
        statement = " ".join(sections.get("statement", [])[:2]).strip()
        phrase_source = f"{title} {statement}".strip()
        phrase_tokens = _tokenize_phrase(phrase_source, stopwords)
        for n in (2, 3):
            if len(phrase_tokens) < n:
                continue
            for idx in range(0, len(phrase_tokens) - n + 1):
                phrase = " ".join(phrase_tokens[idx : idx + n])
                phrase_freq[phrase] = phrase_freq.get(phrase, 0) + 1

    suggestions: dict[str, set[str]] = {}

    for tag, neighbors in sorted(tag_cooc.items()):
        for neighbor, count in sorted(neighbors.items(), key=lambda item: (-item[1], item[0])):
            if count < 2:
                continue
            phrase = neighbor.replace("_", " ").replace("-", " ").strip().lower()
            if not phrase:
                continue
            suggestions.setdefault(tag, set()).add(phrase)

    for phrase, count in sorted(phrase_freq.items(), key=lambda item: (-item[1], item[0])):
        if count < 2:
            continue
        alias = phrase.split()[0]
        if len(alias) < 3:
            continue
        suggestions.setdefault(alias, set()).add(phrase)

    payload: dict[str, list[str]] = {}
    for alias, phrases in sorted(suggestions.items()):
        deduped = sorted(phrase for phrase in phrases if phrase not in existing_aliases.get(alias, set()))
        if not deduped:
            continue
        payload[alias] = deduped[:8]

    out_path = indices_dir / "aliases_suggested.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _generate_links_index(indices_dir: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    """Build a link graph index for cross-reference maintenance.

    Returns:
        Tuple of (links_data, orphan_warnings, broken_link_errors).
    """
    from ledger.parsing.links import extract_links

    links_data: dict[str, dict[str, Any]] = {}
    all_paths: set[str] = set()

    for path in _iter_note_files(include_indices=False):
        rel = _relative(path)
        all_paths.add(rel)
        text = path.read_text(encoding="utf-8")
        _, body = parse_frontmatter_text(text)
        note_links = extract_links(body)

        outgoing = []
        for link in note_links:
            target = link.target
            # Normalize relative paths
            if target.startswith("../") or target.startswith("./"):
                resolved = (path.parent / target).resolve()
                config = get_config()
                target = logical_path(
                    resolved,
                    ledger_root=config.ledger_root,
                    ledger_notes_dir=config.ledger_notes_dir,
                ).as_posix()
            outgoing.append(target)

        links_data[rel] = {
            "outgoing": outgoing,
            "incoming": [],
        }

    # Build incoming links
    for source, data in links_data.items():
        for target in data["outgoing"]:
            if target in links_data:
                links_data[target]["incoming"].append(source)

    # Detect orphans and broken links
    orphan_warnings: list[str] = []
    broken_errors: list[str] = []

    for rel, data in links_data.items():
        total_links = len(data["outgoing"]) + len(data["incoming"])
        if total_links == 0:
            orphan_warnings.append(f"WARN: {rel} - orphan note (0 links)")
        for target in data["outgoing"]:
            # Check if target exists (as relative path or as filename match)
            target_exists = target in all_paths
            if not target_exists:
                # Try matching just the filename
                target_name = target.split("/")[-1] if "/" in target else target
                target_exists = any(p.endswith(f"/{target_name}") or p == target_name for p in all_paths)
            if not target_exists and target.endswith(".md"):
                broken_errors.append(f"ERROR: {rel} - broken link to {target}")

    # Write links.json
    from ledger.io import safe_write_text as _sw
    _sw(indices_dir / "links.json", json.dumps(links_data, indent=2, ensure_ascii=False) + "\n", use_lock=False)

    return links_data, orphan_warnings, broken_errors


def _generate_content_index(indices_dir: Path) -> None:
    """Generate a browseable content-oriented catalog.

    Produces index.md (human-readable) and index.json (machine-consumable)
    grouped by note type with title, summary, tags, confidence, updated, path.
    """
    from ledger.io import safe_write_text as _sw

    entries_by_type: dict[str, list[dict[str, Any]]] = {}

    for path in _iter_note_files(include_indices=False):
        rel = _relative(path)
        text = path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter_text(text)
        title = extract_title(body) or path.stem.replace("_", " ")

        # First meaningful sentence as summary
        lines = [l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith("#")]
        summary = lines[0][:200] if lines else ""

        tags = normalize_tags(frontmatter.get("tags"))
        confidence = frontmatter.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        updated = str(frontmatter.get("updated", "")).strip()

        # Determine note type from path
        note_type = "other"
        for nt_name, nt_cfg in get_config().note_types.items():
            if nt_cfg["dir"] in rel:
                note_type = nt_name
                break

        entry = {
            "path": rel,
            "title": title,
            "summary": summary,
            "tags": tags,
            "note_type": note_type,
            "confidence": confidence,
            "updated": updated,
        }
        entries_by_type.setdefault(note_type, []).append(entry)

    # Sort each type by updated descending
    for entries in entries_by_type.values():
        entries.sort(key=lambda e: e["updated"], reverse=True)

    # Build markdown
    type_labels = {
        "identity": "Identity",
        "facts": "Facts",
        "preferences": "Preferences",
        "goals": "Goals",
        "loops": "Open Loops",
        "concepts": "Concepts",
        "other": "Other",
    }
    md_lines = [
        "# Content Index",
        "",
        "Browseable catalog of all ledger notes. Regenerated by `sheep index`.",
        "Use as a lookup table - do not load into context at boot.",
        "",
    ]
    for nt in list(get_config().note_types.keys()) + ["other"]:
        entries = entries_by_type.get(nt, [])
        if not entries:
            continue
        label = type_labels.get(nt, nt.title())
        md_lines.append(f"## {label} ({len(entries)})")
        md_lines.append("")
        for e in entries:
            tags_str = ", ".join(e["tags"][:5]) if e["tags"] else ""
            md_lines.append(
                f"- **{e['title']}** - {e['summary'][:100]}"
                + (f" `[{tags_str}]`" if tags_str else "")
                + f" ({e['updated'][:10]})"
            )
            md_lines.append(f"  `{e['path']}`")
        md_lines.append("")

    _sw(indices_dir / "index.md", "\n".join(md_lines) + "\n", use_lock=False)

    # Build JSON (flat array)
    all_entries = []
    for entries in entries_by_type.values():
        all_entries.extend(entries)
    all_entries.sort(key=lambda e: e["updated"], reverse=True)
    _sw(indices_dir / "index.json", json.dumps(all_entries, indent=2, ensure_ascii=False) + "\n", use_lock=False)


def _generate_recent(indices_dir: Path) -> None:
    rows: list[tuple[float, str, str]] = []
    for path in _iter_note_files(include_indices=False):
        stat = path.stat()
        frontmatter, _body = parse_frontmatter_text(path.read_text(encoding="utf-8"))
        updated = str(frontmatter.get("updated", "")).strip() or "unknown"
        rows.append((stat.st_mtime, _relative(path), updated))

    rows.sort(key=lambda item: item[0], reverse=True)
    rows = rows[:30]

    md_lines = [
        "# Recently Updated",
        "",
        "Last 30 modified notes. Regenerated by `sheep index`.",
        "",
        "| Updated | Path |",
        "|:--------|:-----|",
    ]
    for _mtime, rel, updated in rows:
        md_lines.append(f"| {updated} | `{rel}` |")
    from ledger.io import safe_write_text

    safe_write_text(indices_dir / "recent.md", "\n".join(md_lines) + "\n", use_lock=False)

    payload = [{"updated": updated, "path": rel} for _mtime, rel, updated in rows]
    safe_write_text(indices_dir / "recent.json", json.dumps(payload, indent=2) + "\n", use_lock=False)


def _run_subprocess(command: list[str], required: bool = True) -> tuple[int, str]:
    proc = subprocess.run(command, cwd=str(get_config().ledger_root), capture_output=True, text=True)
    output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part)
    if required and proc.returncode != 0:
        raise RuntimeError(output or f"command failed: {' '.join(command)}")
    return proc.returncode, output


def _generate_context(indices_dir: Path) -> None:
    context_lib.write_context(indices_dir / "context.md", get_config().ledger_notes_dir)


def _generate_context_profiles(indices_dir: Path) -> None:
    context_lib.write_context_profiles(indices_dir, get_config().ledger_notes_dir)


def _write_context_metrics(indices_dir: Path) -> dict[str, Any]:
    context_path = indices_dir / "context.md"
    context_text = context_path.read_text(encoding="utf-8") if context_path.is_file() else ""

    profile_tokens: dict[str, int] = {}
    for profile_path in sorted(indices_dir.glob("context_profile_*.md")):
        scope = profile_path.stem.removeprefix("context_profile_")
        profile_tokens[scope] = _word_count(profile_path.read_text(encoding="utf-8"))

    note_word_counts = _note_word_counts()
    note_p95 = 0.0
    if note_word_counts:
        note_p95 = float(note_word_counts[max(0, math.ceil(0.95 * len(note_word_counts)) - 1)])

    payload = {
        "boot_context_tokens": _word_count(context_text),
        "boot_context_bytes": len(context_text.encode("utf-8")),
        "profile_tokens": profile_tokens,
        "notes_total_tokens": int(sum(note_word_counts)),
        "avg_note_words": float(statistics.mean(note_word_counts)) if note_word_counts else 0.0,
        "p95_note_words": note_p95,
        "note_count": len(note_word_counts),
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (indices_dir / "context_metrics.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload


def _generate_semantic_index() -> None:
    # Honor the configured embedding backend/model. Hardcoding a model here
    # meant `sheep index` rebuilt (and re-registered) the default model on every
    # run, resurrecting it after `embed clean` and ignoring config.embed_model.
    from ledger.embeddings import configured_model_for_backend, sanitize_model_key

    config = get_config()
    backend = str(config.embed_backend or "local").strip() or "local"
    model = configured_model_for_backend(backend)
    cmd = [
        "ledger",
        "embed",
        "build",
        "--target",
        "ledger",
        "--backend",
        backend,
        "--model",
        model,
    ]
    code, output = _run_subprocess(cmd, required=False)
    if code == 0:
        model_key = f"{backend}__{sanitize_model_key(model)}"
        print(f"  -> .smart-env/semantic/ledger/{model_key}/{{index.json,vectors.npy}}")
        print("  -> notes/08_indices/semantic_manifest.json")
        return

    print("WARN: semantic index build skipped (optional deps missing or build failed)")
    if output:
        for line in output.splitlines()[:3]:
            print(line)


def cmd_index(as_json: bool = False) -> int:
    import io as _io
    import time as _t
    from contextlib import redirect_stdout

    t0 = _t.monotonic()
    if as_json:
        # Capture human output so the envelope is the only thing on
        # stdout. Sub-helpers print verbosely; we don't want to weave
        # --json plumbing through every one.
        buf = _io.StringIO()
        try:
            with redirect_stdout(buf):
                rc = _cmd_index_impl()
        except Exception as exc:
            from ledger.conventions import (
                EXIT_USER_ERROR, action_envelope, emit_action,
            )
            emit_action(action_envelope(
                command="sheep index", ok=False,
                tool="sheep",
                error={"code": "index_failed", "message": str(exc)},
                duration_ms=(_t.monotonic() - t0) * 1000.0,
            ))
            return EXIT_USER_ERROR
        from ledger.conventions import action_envelope, emit_action
        emit_action(action_envelope(
            command="sheep index", ok=(rc == 0),
            tool="sheep",
            stats={"indices_dir": str(_config_paths()[1])},
            duration_ms=(_t.monotonic() - t0) * 1000.0,
        ))
        return rc
    return _cmd_index_impl()


def _cmd_index_impl() -> int:
    _notes_dir, indices_dir, timeline_md = _config_paths()
    indices_dir.mkdir(parents=True, exist_ok=True)

    print("=== Regenerating indices ===")

    print("Generating sizes...")
    _generate_sizes(indices_dir)
    print("  -> sizes.md, sizes.json")

    print("Generating tags...")
    _generate_tags(indices_dir)
    print("  -> tags.md, tags.json")

    print("Generating alias suggestions...")
    _generate_alias_suggestions(indices_dir)
    print("  -> aliases_suggested.json")

    print("Generating content index...")
    _generate_content_index(indices_dir)
    print("  -> index.md, index.json")

    print("Generating links index...")
    _links_data, orphans, broken = _generate_links_index(indices_dir)
    print(f"  -> links.json ({len(_links_data)} notes)")
    if orphans:
        for w in orphans[:5]:
            print(f"  {w}")
    if broken:
        for e in broken[:5]:
            print(f"  {e}")

    print("Generating recent...")
    _generate_recent(indices_dir)
    print("  -> recent.md, recent.json")

    print("Generating persistent note index...")
    started = time.perf_counter_ns()
    note_index_payload = retrieval_lib.rebuild_note_index()
    measured_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    build_ms = float(note_index_payload.get("build_ms", measured_ms) or measured_ms)
    print(f"  -> note_index.json ({build_ms:.2f} ms)")

    print("Regenerating structured timeline...")
    timeline_jsonl = timeline_md.with_name("timeline.jsonl")
    timeline_lib.ensure_timeline_jsonl(timeline_md, timeline_jsonl)
    timeline_lib.regenerate_timeline_markdown(timeline_jsonl, timeline_md)
    print("  -> timeline.jsonl, timeline.md")

    print("Generating context...")
    _generate_context(indices_dir)
    context_path = indices_dir / "context.md"
    context_text = context_path.read_text(encoding="utf-8") if context_path.exists() else ""
    print(
        "  -> context.md "
        f"({_word_count(context_text)} tokens, {len(context_text.encode('utf-8'))} bytes)"
    )

    print("Generating context profiles...")
    _generate_context_profiles(indices_dir)
    print("  -> context_profile_personal.{md,json}")
    print("  -> context_profile_work.{md,json}")
    print("  -> context_profile_dev.{md,json}")
    for profile_path in sorted(indices_dir.glob("context_profile_*.md")):
        word_count = len(profile_path.read_text(encoding="utf-8").split())
        print(f"  -> {profile_path.name} ({word_count} words)")
    context_metrics = _write_context_metrics(indices_dir)
    print("  -> context_metrics.json")
    print(
        "  -> corpus "
        f"({int(context_metrics['notes_total_tokens'])} tokens across {int(context_metrics['note_count'])} notes)"
    )

    print("Generating semantic index (ledger/local)...")
    _generate_semantic_index()

    print("Done.")
    return 0


def cmd_sleep(as_json: bool = False) -> int:
    _notes_dir, _indices_dir, _timeline = _config_paths()

    _SLEEP_ITEMS = [
        {
            "step": 1,
            "title": "Triage inbox",
            "command": "fd . notes/00_inbox",
            "guidance": "Promote, archive, or delete each item",
        },
        {
            "step": 2,
            "title": "Review open loops",
            "command": "fd loop__ notes/05_open_loops",
            "guidance": "Close resolved, snooze stale, ensure next actions exist",
        },
        {
            "step": 3,
            "title": "Check for duplicates",
            "command": "sheep index && head -20 notes/08_indices/sizes.md",
            "guidance": "Merge similar notes, archive duplicates",
        },
        {
            "step": 4,
            "title": "Regenerate indices",
            "command": "sheep index",
            "guidance": "",
        },
        {
            "step": 5,
            "title": "Lint",
            "command": "sheep lint",
            "guidance": "Fix any errors",
        },
        {
            "step": "5b",
            "title": "Contradiction scan",
            "command": "ledger sleep contradictions --check",
            "guidance": "Requires contradiction_enabled=true in config. Use --apply to execute.",
        },
        {
            "step": "5c",
            "title": "Provenance stamp",
            "command": "ledger sleep provenance --check",
            "guidance": "Stamp provenance:corrected on corrected notes. Use --apply to write.",
        },
        {
            "step": 6,
            "title": "Mark sleep complete",
            "command": (
                f"{sys.executable} -c "
                "\"from ledger.embeddings import append_timeline_entry; "
                "append_timeline_entry('sleep', '-', 'consolidation complete')\""
            ),
            "guidance": "timeline.jsonl is the source of truth",
        },
        {
            "step": 7,
            "title": "Commit",
            "command": 'git add -A && git commit -m "sleep: weekly consolidation"',
            "guidance": "",
        },
    ]

    if as_json:
        payload = {"items": _SLEEP_ITEMS}
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print("=== Electric Sheep - Sleep Checklist ===")
    print("")
    print("Follow these steps to consolidate the ledger.")
    print("Use judgment; this is a guide, not a script.")
    print("")
    print("1. Triage inbox")
    print("   fd . notes/00_inbox")
    print("   -> Promote, archive, or delete each item")
    print("")
    print("2. Review open loops")
    print("   fd loop__ notes/05_open_loops")
    print("   -> Close resolved, snooze stale, ensure next actions exist")
    print("")
    print("3. Check for duplicates")
    print("   sheep index && head -20 notes/08_indices/sizes.md")
    print("   -> Merge similar notes, archive duplicates")
    print("")
    print("4. Regenerate indices")
    print("   sheep index")
    print("")
    print("5. Lint")
    print("   sheep lint")
    print("   -> Fix any errors")
    print("")
    print("5b. Contradiction scan (requires contradiction_enabled=true in config)")
    print("    ledger sleep contradictions --check   # dry run, see what would be done")
    print("    ledger sleep contradictions --apply   # execute supersessions + inbox flags")
    print("")
    print("5c. Provenance stamp (mark corrected notes so confidence weighting can use it)")
    print("    ledger sleep provenance --check   # dry run, see what would be stamped")
    print("    ledger sleep provenance --apply   # write provenance:corrected")
    print("")
    print("6. Mark sleep complete")
    print("   # timeline.jsonl is the source of truth; appending to timeline.md is")
    print("   # silently wiped on the next `sheep index`. Use the canonical helper:")
    print(
        f"   {sys.executable} -c "
        "\"from ledger.embeddings import append_timeline_entry; "
        "append_timeline_entry('sleep', '-', 'consolidation complete')\""
    )
    print("")
    print("7. Commit")
    print('   git add -A && git commit -m "sleep: weekly consolidation"')
    print("")
    return 0


def cmd_provenance(apply: bool = False, as_json: bool = False) -> int:
    """Stamp ``provenance: corrected`` on notes with real correction signals (plan 42).

    A note that has been corrected (an affirmed correction signal) should carry
    the ``corrected`` provenance class so provenance-weighted confidence values
    it at 0.90 rather than its derived default. Only notes whose ``provenance``
    is currently absent are stamped — an explicit value the user set is left
    alone. Dry-run by default; pass ``apply=True`` to write.
    """
    import json as _json

    from ledger.config import get_config
    from ledger.layout import resolve_path
    from ledger.parsing.frontmatter import parse_frontmatter_text, serialize_frontmatter
    from ledger.io import safe_write_text
    from ledger.signals import summarize_signals

    cfg = get_config()
    summary = summarize_signals(signals_path=cfg.signals_path)
    notes = summary.get("notes", {})

    changed: list[str] = []
    skipped_explicit: list[str] = []
    for rel_path, stats in notes.items():
        # Real (non-synthetic) corrections only.
        real_corrections = float(stats.get("corrections", 0.0)) - float(
            stats.get("synthetic_corrections", 0.0)
        )
        if real_corrections <= 0:
            continue
        abs_path = Path(
            resolve_path(rel_path, ledger_root=cfg.ledger_root, ledger_notes_dir=cfg.ledger_notes_dir)
        )
        if not abs_path.is_file():
            continue
        text = abs_path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter_text(text)
        current = str(fm.get("provenance", "") or "").strip().lower()
        if current == "corrected":
            continue
        if current:
            skipped_explicit.append(rel_path)
            continue
        if apply:
            fm["provenance"] = "corrected"
            safe_write_text(abs_path, serialize_frontmatter(fm) + "\n" + body.lstrip("\n"))
        changed.append(rel_path)

    if as_json:
        print(_json.dumps(
            {"apply": apply, "changed": changed, "skipped_explicit": skipped_explicit,
             "count": len(changed)}, ensure_ascii=False))
        return 0

    verb = "Stamped" if apply else "Would stamp"
    print(f"provenance: {verb} {len(changed)} note(s) as 'corrected'.")
    for rel_path in changed:
        print(f"  {rel_path}")
    if skipped_explicit:
        print(f"  (left {len(skipped_explicit)} note(s) with explicit provenance unchanged)")
    if not apply and changed:
        print("\nRun with --apply to write these changes.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Electric Sheep maintenance helper")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Emit JSON / action envelope on stdout (machine mode).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show sleep and sync status")
    subparsers.add_parser("lint", help="Validate note and timeline conventions")
    subparsers.add_parser("index", help="Regenerate derived indices")
    subparsers.add_parser("sleep", help="Show consolidation checklist")

    duplicates_parser = subparsers.add_parser(
        "duplicates",
        help="Scan for duplicate or near-duplicate notes",
    )
    duplicates_parser.add_argument(
        "--threshold",
        type=float,
        default=0.65,
        dest="threshold",
        help="Jaccard similarity threshold for content duplicates (default: 0.65)",
    )
    duplicates_parser.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Emit JSON output",
    )

    sync_parser = subparsers.add_parser("sync", help="Compare notes against sync baseline")
    mode_group = sync_parser.add_mutually_exclusive_group()
    mode_group.add_argument("--check", action="store_true", help="Check drift (default)")
    mode_group.add_argument("--apply", action="store_true", help="Write current notes snapshot as baseline")

    contradictions_parser = subparsers.add_parser(
        "contradictions",
        help="NLI-based contradiction scan (check | apply)",
    )
    contra_mode = contradictions_parser.add_mutually_exclusive_group()
    contra_mode.add_argument(
        "--check",
        action="store_true",
        help="Dry run: report what would be done without writing (default)",
    )
    contra_mode.add_argument(
        "--apply",
        action="store_true",
        help="Execute supersessions and write conflict inbox notes",
    )

    provenance_parser = subparsers.add_parser(
        "provenance",
        help="Stamp provenance:corrected on notes with correction signals (check | apply)",
    )
    prov_mode = provenance_parser.add_mutually_exclusive_group()
    prov_mode.add_argument("--check", action="store_true",
                           help="Dry run: report what would be stamped (default)")
    prov_mode.add_argument("--apply", action="store_true",
                           help="Write provenance:corrected to the affected notes")

    return parser


def main(argv: list[str] | None = None) -> int:
    import sys as _sys

    raw = list(_sys.argv[1:] if argv is None else argv)
    # Top-level --doctor per the CLI contract. Handle before
    # argparse because the maintenance parser requires a subcommand.
    if "--doctor" in raw:
        from ledger.doctor import emit_doctor
        # sheep uses ledger's doctor; identity-of-tool override
        # happens via the JSON shape (tool field).
        as_json = "--json" in raw
        return _emit_sheep_doctor(as_json)

    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(getattr(args, "json", False))

    if args.command == "status":
        return cmd_status(as_json=as_json)
    if args.command == "lint":
        return cmd_lint(as_json=as_json)
    if args.command == "index":
        return cmd_index(as_json=as_json)
    if args.command == "sleep":
        return cmd_sleep(as_json=as_json)
    if args.command == "sync":
        return _wrap_sync(apply=bool(args.apply), as_json=as_json)
    if args.command == "contradictions":
        from ledger.contradiction import cmd_sleep_contradictions
        return cmd_sleep_contradictions(apply=bool(getattr(args, "apply", False)))
    if args.command == "provenance":
        return cmd_provenance(apply=bool(getattr(args, "apply", False)), as_json=as_json)
    if args.command == "duplicates":
        return _cmd_duplicates(
            threshold=float(getattr(args, "threshold", 0.65)),
            as_json=as_json,
        )

    parser.print_help()
    return 1


def _cmd_duplicates(threshold: float = 0.65, as_json: bool = False) -> int:
    """Scan for duplicate or near-duplicate notes."""
    import json as _json
    from ledger.config import get_config
    from ledger.duplicates import scan_duplicates

    cfg = get_config()
    notes_dir = getattr(cfg, "ledger_notes_dir", None)
    if not notes_dir:
        if as_json:
            print(_json.dumps({"error": "ledger_notes_dir not configured"}, ensure_ascii=False))
        else:
            print("error: ledger_notes_dir not configured", flush=True)
        return 1

    findings = scan_duplicates(notes_dir, jaccard_threshold=threshold)

    if as_json:
        out = {
            "notes_dir": str(notes_dir),
            "threshold": threshold,
            "count": len(findings),
            "findings": [f.to_dict() for f in findings],
        }
        print(_json.dumps(out, ensure_ascii=False))
        return 0

    print(f"duplicates scan: {len(findings)} finding(s) (threshold={threshold:.2f})")
    if not findings:
        print("  No duplicates found.")
        return 0

    for f in findings:
        print(f"  [{f.reason}] score={f.score:.3f}")
        print(f"    a: {f.path_a}")
        print(f"    b: {f.path_b}")
        if f.details:
            for k, v in f.details.items():
                print(f"    {k}: {v}")
    return 0


def _wrap_data_cmd(command: str, fn, as_json: bool) -> int:
    """Run a print-heavy data command, optionally capturing stdout into a
    structured envelope so --json consumers see a stable shape.

    The captured human text becomes ``stats.human_lines`` (a list of
    rstrip'd lines). The command's return code becomes the exit code.
    Reserved-key contract: success documents have no top-level `ok`.
    """
    if not as_json:
        return fn()
    import io as _io
    import json as _json
    import sys as _sys
    from contextlib import redirect_stdout
    buf = _io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = fn()
    except Exception as exc:
        _sys.stdout.write(_json.dumps({
            "tool": "sheep",
            "command": command,
            "ok": False,
            "error": {"code": "command_failed", "message": str(exc)},
        }, ensure_ascii=False) + "\n")
        _sys.stdout.flush()
        return 1
    payload = {
        "tool": "sheep",
        "command": command,
        "exit_code": int(rc or 0),
        "human_lines": [line.rstrip() for line in buf.getvalue().splitlines() if line.strip()],
    }
    _sys.stdout.write(_json.dumps(payload, ensure_ascii=False) + "\n")
    _sys.stdout.flush()
    return int(rc or 0)


def _wrap_sync(apply: bool, as_json: bool) -> int:
    """cmd_sync is action when --apply, data when --check."""
    if not as_json:
        return cmd_sync(apply=apply)
    import io as _io
    import time as _t
    from contextlib import redirect_stdout
    t0 = _t.monotonic()
    buf = _io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = cmd_sync(apply=apply)
    except Exception as exc:
        from ledger.conventions import (
            EXIT_USER_ERROR, action_envelope, emit_action,
        )
        emit_action(action_envelope(
            command="sheep sync", ok=False,
            tool="sheep",
            error={"code": "sync_failed", "message": str(exc)},
            duration_ms=(_t.monotonic() - t0) * 1000.0,
        ))
        return EXIT_USER_ERROR
    if apply:
        from ledger.conventions import action_envelope, emit_action
        emit_action(action_envelope(
            command="sheep sync", ok=(rc == 0),
            tool="sheep",
            stats={"applied": True, "human_lines": [
                line.rstrip() for line in buf.getvalue().splitlines() if line.strip()
            ]},
            duration_ms=(_t.monotonic() - t0) * 1000.0,
        ))
        return int(rc or 0)
    # Data class -> raw doc, no top-level `ok`.
    import json as _json
    import sys as _sys
    payload = {
        "tool": "sheep",
        "command": "sheep sync",
        "applied": False,
        "exit_code": int(rc or 0),
        "human_lines": [line.rstrip() for line in buf.getvalue().splitlines() if line.strip()],
    }
    _sys.stdout.write(_json.dumps(payload, ensure_ascii=False) + "\n")
    _sys.stdout.flush()
    return int(rc or 0)


def _emit_sheep_doctor(as_json: bool) -> int:
    """sheep's doctor reuses ledger's checks and re-tags the tool name."""
    import json as _json
    import sys as _sys

    from ledger.doctor import run_doctor

    payload = run_doctor()
    data = payload.to_dict()
    data["tool"] = "sheep"
    if as_json:
        _sys.stdout.write(_json.dumps(data, ensure_ascii=False) + "\n")
        _sys.stdout.flush()
    else:
        print(f"sheep doctor (v{data['version']})")
        if payload.config_path:
            print(f"  config: {payload.config_path}")
        if payload.data_path:
            print(f"  root:   {payload.data_path}")
        if not payload.findings:
            print("  status: ok")
        else:
            print(f"  findings: {len(payload.findings)}")
            for f in payload.findings:
                marker = {"error": "x", "warning": "!", "info": "."}.get(f.severity, ".")
                print(f"    {marker} [{f.severity}] {f.id}: {f.message}")
                if f.hint:
                    print(f"        hint: {f.hint}")
    return payload.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())

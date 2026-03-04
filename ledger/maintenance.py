"""Python maintenance CLI for Electric Sheep workflows.

This module replaces the legacy bash `scripts/sheep` implementation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.parsing import (
    parse_frontmatter_text,
    parse_sections,
    parse_timestamp,
    normalize_tags,
    extract_title,
)
from ledger import retrieval as retrieval_lib
from ledger import timeline as timeline_lib


TAG_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
TIMESTAMP_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
TIMELINE_LINE_PATTERN = re.compile(
    r"^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z) \| (created|updated|archived|deleted|closed|sleep) \| (.+?) \| (.*)$"
)

SOURCE_VALUES = {"user", "tool", "assistant", "inferred"}
SCOPE_VALUES = {"home", "work", "dev", "personal", "meta", "life"}
LANG_VALUES = {"en", "no", "mixed"}
STATUS_VALUES = {"open", "closed", "blocked", "snoozed"}

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


def _config_paths() -> tuple[Path, Path, Path]:
    config = get_config()
    notes_dir = config.notes_dir
    indices_dir = notes_dir / "08_indices"
    timeline = config.timeline_path
    return notes_dir, indices_dir, timeline


def _relative(path: Path) -> str:
    root = get_config().root_dir
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


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


def cmd_status() -> int:
    _notes_dir, _indices_dir, timeline = _config_paths()

    print("=== Sleep Status ===")
    if not timeline.is_file():
        print(f"Timeline not found at {_relative(timeline)}")
        print("-> Run 'sheep index' first")
        return 0

    entries = _timeline_entries(timeline)
    if not entries:
        print("Last sleep: never")
        print("Total timeline entries: 0")
        print("-> Sleep recommended (first run)")
        return 0

    sleep_positions = [idx for idx, entry in enumerate(entries) if entry[2] == "sleep"]
    if not sleep_positions:
        print("Last sleep: never")
        print(f"Total timeline entries: {len(entries)}")
        print("-> Sleep recommended (first run)")
        return 0

    sleep_idx = sleep_positions[-1]
    last_sleep = entries[sleep_idx]
    last_sleep_ts = last_sleep[1]
    changes_since = max(0, len(entries) - sleep_idx - 1)

    last_dt = parse_timestamp(last_sleep_ts)
    now_dt = datetime.now(timezone.utc)
    days_since = 0
    if last_dt is not None:
        days_since = max(0, (now_dt.date() - last_dt.date()).days)

    print(f"Last sleep: {last_sleep_ts}")
    print(f"Changes since: {changes_since}")
    print(f"Days since: {days_since}")
    sync_report = _compute_sync_report()
    if sync_report["state_invalid"]:
        print("Sync drift: state invalid (run `sheep sync --apply`)")
    elif not sync_report["state_exists"]:
        print("Sync drift: unknown (run `sheep sync --apply`)")
    elif sync_report["timeline_rewound"]:
        print("Sync drift: timeline rewound (run `sheep sync --check`)")
    elif sync_report["unlogged_paths"]:
        print(f"Sync drift: {len(sync_report['unlogged_paths'])} unlogged note change(s)")
    else:
        print("Sync drift: clean")
    if days_since >= 7 or changes_since >= 25:
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


def _lint_note(path: Path, counters: LintCounters) -> None:
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

    required_fields = ["created", "updated", "tags", "confidence", "source", "scope", "lang"]
    for field in required_fields:
        if not fm_raw_value(field) and field not in frontmatter:
            _lint_error(path, f"missing '{field}'")
            counters.errors += 1

    rel = _relative(path)
    is_loop = "/05_open_loops/" in f"/{rel}"
    if is_loop and "status" not in frontmatter:
        _lint_error(path, "open loop missing 'status'")
        counters.errors += 1

    created = fm_raw_value("created") or str(frontmatter.get("created", "")).strip()
    if created and not TIMESTAMP_PATTERN.match(created):
        _lint_error(path, f"invalid created timestamp: {created}")
        counters.errors += 1

    updated = fm_raw_value("updated") or str(frontmatter.get("updated", "")).strip()
    if updated and not TIMESTAMP_PATTERN.match(updated):
        _lint_error(path, f"invalid updated timestamp: {updated}")
        counters.errors += 1

    source = (fm_raw_value("source") or str(frontmatter.get("source", ""))).strip().lower()
    if source and source not in SOURCE_VALUES:
        _lint_error(path, f"invalid source: {source}")
        counters.errors += 1

    scope = (fm_raw_value("scope") or str(frontmatter.get("scope", ""))).strip().lower()
    if scope and scope not in SCOPE_VALUES:
        _lint_error(path, f"invalid scope: {scope}")
        counters.errors += 1

    lang = (fm_raw_value("lang") or str(frontmatter.get("lang", ""))).strip().lower()
    if lang and lang not in LANG_VALUES:
        _lint_error(path, f"invalid lang: {lang}")
        counters.errors += 1

    status = (fm_raw_value("status") or str(frontmatter.get("status", ""))).strip().lower()
    if is_loop and status and status not in STATUS_VALUES:
        _lint_error(path, f"invalid status: {status}")
        counters.errors += 1

    tags = normalize_tags(frontmatter.get("tags"))
    for tag in tags:
        if not TAG_PATTERN.match(tag):
            _lint_error(path, f"invalid tag: {tag}")
            counters.errors += 1

    confidence_raw = fm_raw_value("confidence") or frontmatter.get("confidence", "")
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None:
        if confidence < 0 or confidence > 1:
            _lint_error(path, f"confidence out of range: {confidence_raw}")
            counters.errors += 1
        if source == "inferred" and confidence > 0.8:
            _lint_warn(path, f"inferred note has high confidence ({confidence} > 0.8)")
            counters.warnings += 1
            counters.warn_inferred_confidence += 1

    words = len(text.split())
    if words > LARGE_FILE_WORD_THRESHOLD:
        _lint_warn(path, f"large file ({words} words)")
        counters.warnings += 1
        counters.warn_large_files += 1

    sections = parse_sections(body)
    if _is_open_loop(path, frontmatter):
        next_action_lines = sections.get("next action")
        if not next_action_lines:
            _lint_warn(path, "open loop missing '## Next action' section")
            counters.warnings += 1
            counters.warn_loop_missing_next_action += 1
        else:
            has_checkbox = any(
                re.match(r"^\s*-\s*\[[ xX]\]\s+", line or "") is not None
                for line in next_action_lines
            )
            if not has_checkbox:
                _lint_warn(path, "open loop has no checkbox action in Next action section")
                counters.warnings += 1
                counters.warn_loop_missing_checkbox += 1

    links_lines = sections.get("links", [])
    placeholder_link = any(re.match(r"^\s*-\s*$", line or "") for line in links_lines)
    if placeholder_link:
        _lint_warn(path, "placeholder bullet found in Links section")
        counters.warnings += 1
        counters.warn_placeholder_links += 1


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


def cmd_lint() -> int:
    _notes_dir, _indices_dir, timeline = _config_paths()
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

    return 1 if counters.errors > 0 else 0


def _word_count(text: str) -> int:
    return len(text.split())


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
    (indices_dir / "sizes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = [{"words": words, "path": rel} for words, rel in rows]
    (indices_dir / "sizes.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _generate_tags(indices_dir: Path) -> None:
    by_tag: dict[str, set[str]] = {}
    title_by_path: dict[str, str] = {}

    for path in _iter_note_files(include_indices=False):
        rel = _relative(path)
        frontmatter, body = parse_frontmatter_text(path.read_text(encoding="utf-8"))
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
    (indices_dir / "tags.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    json_payload = {tag: sorted(paths) for tag, paths in sorted(by_tag.items())}
    (indices_dir / "tags.json").write_text(json.dumps(json_payload, indent=2) + "\n", encoding="utf-8")


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
    (indices_dir / "recent.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    payload = [{"updated": updated, "path": rel} for _mtime, rel, updated in rows]
    (indices_dir / "recent.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _run_subprocess(command: list[str], required: bool = True) -> tuple[int, str]:
    proc = subprocess.run(command, cwd=str(get_config().root_dir), capture_output=True, text=True)
    output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part)
    if required and proc.returncode != 0:
        raise RuntimeError(output or f"command failed: {' '.join(command)}")
    return proc.returncode, output


def _generate_context(indices_dir: Path) -> None:
    root = get_config().root_dir
    notes_dir = get_config().notes_dir
    _run_subprocess(
        [
            sys.executable,
            str(root / "scripts" / "build_context.py"),
            "--notes-dir",
            _relative(notes_dir),
            "--output",
            _relative(indices_dir / "context.md"),
        ]
    )


def _generate_context_profiles(indices_dir: Path) -> None:
    root = get_config().root_dir
    notes_dir = get_config().notes_dir
    _run_subprocess(
        [
            sys.executable,
            str(root / "scripts" / "build_context_profiles.py"),
            "--notes-dir",
            _relative(notes_dir),
            "--output-dir",
            _relative(indices_dir),
        ]
    )


def _generate_semantic_index() -> None:
    root = get_config().root_dir
    cmd = [
        str(root / "scripts" / "ledger"),
        "embed",
        "build",
        "--target",
        "ledger",
        "--backend",
        "local",
        "--model",
        "TaylorAI/bge-micro-v2",
    ]
    code, output = _run_subprocess(cmd, required=False)
    if code == 0:
        print("  -> .smart-env/semantic/ledger/local__TaylorAI__bge-micro-v2/{index.json,vectors.npy}")
        print("  -> notes/08_indices/semantic_manifest.json")
        return

    print("WARN: semantic index build skipped (optional deps missing or build failed)")
    if output:
        for line in output.splitlines()[:3]:
            print(line)


def cmd_index() -> int:
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

    print("Generating recent...")
    _generate_recent(indices_dir)
    print("  -> recent.md, recent.json")

    print("Generating persistent note index...")
    retrieval_lib.rebuild_note_index()
    print("  -> note_index.json")

    print("Regenerating structured timeline...")
    timeline_jsonl = timeline_md.with_name("timeline.jsonl")
    timeline_lib.ensure_timeline_jsonl(timeline_md, timeline_jsonl)
    timeline_lib.regenerate_timeline_markdown(timeline_jsonl, timeline_md)
    print("  -> timeline.jsonl, timeline.md")

    print("Generating context...")
    _generate_context(indices_dir)
    print("  -> context.md")

    print("Generating context profiles...")
    _generate_context_profiles(indices_dir)
    print("  -> context_profile_personal.{md,json}")
    print("  -> context_profile_work.{md,json}")
    print("  -> context_profile_dev.{md,json}")

    print("Generating semantic index (ledger/local)...")
    _generate_semantic_index()

    print("Done.")
    return 0


def cmd_sleep() -> int:
    _notes_dir, _indices_dir, timeline = _config_paths()

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
    print("6. Mark sleep complete")
    print(f"   echo \"$(date -u +%Y-%m-%dT%H:%M:%SZ) | sleep | - | consolidation complete\" >> {_relative(timeline)}")
    print("")
    print("7. Commit")
    print('   git add -A && git commit -m "sleep: weekly consolidation"')
    print("")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Electric Sheep maintenance helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show sleep and sync status")
    subparsers.add_parser("lint", help="Validate note and timeline conventions")
    subparsers.add_parser("index", help="Regenerate derived indices")
    subparsers.add_parser("sleep", help="Show consolidation checklist")

    sync_parser = subparsers.add_parser("sync", help="Compare notes against sync baseline")
    mode_group = sync_parser.add_mutually_exclusive_group()
    mode_group.add_argument("--check", action="store_true", help="Check drift (default)")
    mode_group.add_argument("--apply", action="store_true", help="Write current notes snapshot as baseline")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return cmd_status()
    if args.command == "lint":
        return cmd_lint()
    if args.command == "index":
        return cmd_index()
    if args.command == "sleep":
        return cmd_sleep()
    if args.command == "sync":
        return cmd_sync(apply=bool(args.apply))

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

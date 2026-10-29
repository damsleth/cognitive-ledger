"""Sync snapshots and timeline reconciliation."""
from __future__ import annotations

from ledger.io.hashing import digest, file_digest
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from ledger.layout import INDICES_SUBDIR
from ledger.config import get_config
from ledger import timeline as timeline_lib
from .inventory import _config_paths, _iter_note_files, _relative


TIMELINE_LINE_PATTERN = re.compile(
    r"^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z) \| (created|updated|archived|deleted|closed|sleep|linked|reviewed) \| (.+?) \| (.*)$"
)


SYNC_STATE_VERSION = 2


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


def _timeline_jsonl_entries(timeline_jsonl_path: Path) -> list[tuple[int, str, str, str, str]]:
    """Return machine-source events in the tuple shape used by maintenance."""
    return [
        (
            lineno,
            str(event.get("ts", "")),
            str(event.get("action", "")),
            str(event.get("path", "")),
            str(event.get("desc", "")),
        )
        for lineno, event in enumerate(
            timeline_lib.load_timeline_jsonl(timeline_jsonl_path), start=1
        )
    ]


def _sync_state_path() -> Path:
    _notes_dir, indices_dir, _timeline = _config_paths()
    return indices_dir / "sync_state.json"


def _sync_acceptance_path() -> Path:
    _notes_dir, indices_dir, _timeline = _config_paths()
    return indices_dir / "sync_acceptance.jsonl"


def _sha256_path(path: Path) -> str:
    return file_digest(path)


def _timeline_prefix_sha256(events: list[dict[str, Any]], count: int | None = None) -> str:
    prefix = events if count is None else events[:count]
    canonical = "\n".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for event in prefix
    )
    return digest(canonical.encode("utf-8"))


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
    if f"/{INDICES_SUBDIR}/" in wrapped:
        return False
    if "/templates/" in wrapped:
        return False
    if normalized.endswith("/.gitkeep") or normalized.endswith(".gitkeep"):
        return False
    return True


def _compute_sync_report() -> dict[str, Any]:
    _notes_dir, _indices_dir, _timeline_path = _config_paths()
    timeline_events = timeline_lib.load_timeline_jsonl(get_config().timeline_jsonl_path)
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
        "timeline_total": len(timeline_events),
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

    expected_prefix_hash = str(state.get("timeline_prefix_sha256", "")).strip()
    prefix_changed = bool(
        expected_prefix_hash
        and baseline_count <= len(timeline_events)
        and _timeline_prefix_sha256(timeline_events, baseline_count)
        != expected_prefix_hash
    )
    if baseline_count > len(timeline_events) or prefix_changed:
        report["timeline_rewound"] = True
        timeline_since = timeline_events
    else:
        timeline_since = timeline_events[baseline_count:]

    logged_paths_set: set[str] = set()
    for event in timeline_since:
        path_field = str(event.get("path", "")).strip()
        if not _is_tracked_path(path_field) or path_field not in drift_paths:
            continue
        if path_field not in current_snapshot:
            if str(event.get("action", "")) in {"deleted", "archived"}:
                logged_paths_set.add(path_field)
            continue
        if str(event.get("content_hash", "")) == current_snapshot[path_field]:
            logged_paths_set.add(path_field)

    logged_paths = sorted(logged_paths_set)
    unlogged_paths = sorted(path for path in drift_paths if path not in logged_paths)
    timeline_only_paths = sorted(
        {
            str(event.get("path", "")).strip()
            for event in timeline_since
            if _is_tracked_path(str(event.get("path", "")))
            and str(event.get("path", "")).strip() not in drift_paths
        }
    )

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
    _notes_dir, indices_dir, _timeline_path = _config_paths()
    indices_dir.mkdir(parents=True, exist_ok=True)

    timeline_events = timeline_lib.load_timeline_jsonl(get_config().timeline_jsonl_path)
    snapshot = _tracked_note_snapshot()
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload: dict[str, Any] = {
        "version": SYNC_STATE_VERSION,
        "last_synced_at": now_ts,
        "timeline_event_count": len(timeline_events),
        "timeline_prefix_sha256": _timeline_prefix_sha256(timeline_events),
        "last_timeline_timestamp": str(timeline_events[-1].get("ts", "")) if timeline_events else "",
        "tracked_file_count": len(snapshot),
        "files": snapshot,
    }
    path = _sync_state_path()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _sync_apply_blockers(report: dict[str, Any]) -> list[str]:
    blockers = []
    if report["state_invalid"]:
        blockers.append("state_invalid")
    if report["timeline_rewound"]:
        blockers.append("timeline_rewound")
    if report["unlogged_paths"]:
        blockers.append("unlogged_changes")
    return blockers


def _append_sync_acceptance(report: dict[str, Any], blockers: list[str]) -> None:
    from ledger.io import safe_append_line

    paths = sorted(str(path) for path in report["unlogged_paths"])
    event = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": "accept_drift",
        "blockers": blockers,
        "unlogged_change_count": len(paths),
        "unlogged_paths_sha256": digest("\n".join(paths).encode()),
        "timeline_rewound": bool(report["timeline_rewound"]),
        "state_invalid": bool(report["state_invalid"]),
    }
    safe_append_line(_sync_acceptance_path(), json.dumps(event, sort_keys=True))


def cmd_sync(apply: bool = False, accept_drift: bool = False) -> int:
    if apply:
        report = _compute_sync_report()
        blockers = _sync_apply_blockers(report)
        if blockers and not accept_drift:
            print("Refusing to replace an existing sync baseline with unresolved drift.")
            print(f"Blockers: {', '.join(blockers)}")
            print("-> Review with `sheep sync --check`, then use `--accept-drift` explicitly.")
            return 2
        payload = _write_sync_state()
        if blockers:
            _append_sync_acceptance(report, blockers)
        print("=== Sync State Updated ===")
        print(f"State: {_relative(_sync_state_path())}")
        print(f"Last synced: {payload['last_synced_at']}")
        print(f"Tracked notes: {payload['tracked_file_count']}")
        print(f"Timeline event count: {payload['timeline_event_count']}")
        if blockers:
            print(f"Accepted drift audit: {_relative(_sync_acceptance_path())}")
        return 0

    report = _compute_sync_report()
    print("=== Sync Status ===")
    print(f"State: {report['state_path']}")

    if report["state_invalid"]:
        print("State is invalid JSON.")
        print("-> Run `sheep sync --apply --accept-drift` to reset baseline explicitly")
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

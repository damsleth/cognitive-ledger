"""Sleep status and recommendation gates."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from ledger.config import get_config
from ledger.parsing import parse_timestamp
from .inventory import _config_paths, _relative
from .sync import _compute_sync_report, _is_tracked_path, _timeline_jsonl_entries


SLEEP_DAYS_THRESHOLD = 7


SLEEP_UNIQUE_CHANGE_THRESHOLD = 25


SLEEP_UNLOGGED_CHANGE_THRESHOLD = 50


SLEEP_DRIFT_STATES = frozenset({"state_invalid", "timeline_rewound"})


def _status_payload() -> dict:
    """Build the structured status report shared by human and --json paths."""
    _notes_dir, _indices_dir, _timeline = _config_paths()
    timeline = get_config().timeline_jsonl_path
    payload = {
        "timeline_path": str(timeline),
        "timeline_exists": timeline.is_file(),
        "last_sleep": None,
        "entries_total": 0,
        "changes_since": 0,
        "unique_changes_since": 0,
        "days_since": 0,
        "sync_drift": "unknown",
        "unlogged_change_count": 0,
        "sleep_recommended": False,
        "sleep_recommendation_reasons": [],
        "sleep_gate_evaluations": [],
    }
    if not timeline.is_file():
        payload["sleep_recommended"] = True
        payload["sleep_recommendation_reason"] = "timeline_missing"
        payload["sleep_recommendation_reasons"] = ["timeline_missing"]
        return payload
    entries = _timeline_jsonl_entries(timeline)
    payload["entries_total"] = len(entries)
    if not entries:
        payload["sleep_recommended"] = True
        payload["sleep_recommendation_reason"] = "first_run"
        payload["sleep_recommendation_reasons"] = ["first_run"]
        return payload
    sleep_positions = [idx for idx, entry in enumerate(entries) if entry[2] == "sleep"]
    if not sleep_positions:
        payload["sleep_recommended"] = True
        payload["sleep_recommendation_reason"] = "first_run"
        payload["sleep_recommendation_reasons"] = ["first_run"]
        return payload
    sleep_idx = sleep_positions[-1]
    last_sleep = entries[sleep_idx]
    last_sleep_ts = last_sleep[1]
    payload["last_sleep"] = last_sleep_ts
    entries_since_sleep = entries[sleep_idx + 1 :]
    payload["changes_since"] = len(entries_since_sleep)
    payload["unique_changes_since"] = len(
        {
            path
            for _lineno, _ts, _action, path, _desc in entries_since_sleep
            # A review pass (`ledger unreviewed --accept`) changes no content.
            if _is_tracked_path(path) and _action != "reviewed"
        }
    )
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
    work_present = (
        payload["unique_changes_since"] > 0
        or payload["unlogged_change_count"] > 0
    )
    gate_evaluations = [
        {
            "code": "days_since",
            "observed": days_since,
            "operator": ">=",
            "threshold": SLEEP_DAYS_THRESHOLD,
            "requires_work": True,
            "work_present": work_present,
            "met": days_since >= SLEEP_DAYS_THRESHOLD and work_present,
        },
        {
            "code": "unique_changes_since",
            "observed": payload["unique_changes_since"],
            "operator": ">=",
            "threshold": SLEEP_UNIQUE_CHANGE_THRESHOLD,
            "met": payload["unique_changes_since"] >= SLEEP_UNIQUE_CHANGE_THRESHOLD,
        },
        {
            "code": "unlogged_change_count",
            "observed": payload["unlogged_change_count"],
            "operator": ">=",
            "threshold": SLEEP_UNLOGGED_CHANGE_THRESHOLD,
            "met": payload["unlogged_change_count"] >= SLEEP_UNLOGGED_CHANGE_THRESHOLD,
        },
        {
            "code": "sync_drift",
            "observed": payload["sync_drift"],
            "operator": "in",
            "threshold": sorted(SLEEP_DRIFT_STATES),
            "met": payload["sync_drift"] in SLEEP_DRIFT_STATES,
        },
    ]
    payload["sleep_gate_evaluations"] = gate_evaluations
    payload["sleep_recommendation_reasons"] = [
        gate["code"] for gate in gate_evaluations if gate["met"]
    ]
    payload["sleep_recommended"] = bool(payload["sleep_recommendation_reasons"])
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
    print(f"Unique notes changed: {payload['unique_changes_since']}")
    print(f"Days since: {payload['days_since']}")
    drift = payload["sync_drift"]
    if drift == "state_invalid":
        print("Sync drift: state invalid (run `sheep sync --apply --accept-drift`)")
    elif drift == "unknown":
        print("Sync drift: unknown (run `sheep sync --apply`)")
    elif drift == "timeline_rewound":
        print("Sync drift: timeline rewound (run `sheep sync --check`)")
    elif drift == "unlogged_changes":
        print(f"Sync drift: {payload['unlogged_change_count']} unlogged note change(s)")
    else:
        print("Sync drift: clean")
    evaluations = payload["sleep_gate_evaluations"]
    evaluation_parts = []
    for gate in evaluations:
        text = f"{gate['code']}={gate['observed']} {gate['operator']} {gate['threshold']}"
        if gate.get("requires_work"):
            text += f" with work_present={gate['work_present']}"
        evaluation_parts.append(text)
    evaluation_text = ", ".join(evaluation_parts)
    if payload["sleep_recommended"]:
        reasons = ", ".join(payload["sleep_recommendation_reasons"])
        print(f"-> Sleep recommended ({reasons}; {evaluation_text})")
    else:
        print(f"-> No sleep needed ({evaluation_text})")
    return 0

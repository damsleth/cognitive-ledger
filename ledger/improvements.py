"""Actionable pattern lifecycle. Reads are pure; only explicit actions append events."""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import os
from pathlib import Path

from ledger.conventions import now_iso
from ledger.io.safe_write import FileLock
from ledger.parsing.privacy import strip_private_tags
from ledger.patterns import mine_patterns
from ledger.signals import read_signals

LOG = "improvements.jsonl"
SIGNAL_TYPES = {
    "repeated_retrieval_miss": "retrieval_miss",
    "correction_prone_note": "correction",
    "stale_note": "stale_flag",
    "contradicted_note": "contradiction_flagged",
}


def stable_id(kind: str, subject: str) -> str:
    return hashlib.sha256(f"{kind}\0{subject}".encode()).hexdigest()[:20]


def _clean(value):
    if isinstance(value, str):
        return strip_private_tags(value)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def pattern_items(notes_dir: Path) -> list[dict]:
    signals = read_signals(signals_path=Path(notes_dir) / "08_indices/signals.jsonl")
    items = []
    for pattern in mine_patterns(signals)["patterns"]:
        if pattern["category"] != "failure":
            continue
        kind, subject = pattern["kind"], pattern["subject"]
        key = "query" if kind == "repeated_retrieval_miss" else "note"
        matching = [s for s in signals if s.get("type") == SIGNAL_TYPES[kind]
                    and s.get(key) == subject]
        # Multiset counts catch identical events and same-timestamp recurrences.
        evidence = dict(Counter(hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest()
                                for s in matching))
        items.append(_clean({**pattern, "id": stable_id(kind, subject),
                             "destination": "note", "evidence_checkpoint": evidence}))
    return items


def _events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    result = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if event.get("action") not in {"observe", "resolve", "dismiss", "defer", "reopen"}:
                raise ValueError("unknown action")
            if not isinstance(event.get("item"), dict) or not event["item"].get("id"):
                raise ValueError("missing item")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError(f"Invalid improvement event at line {number}") from exc
        result.append(event)
    return result


def _has_new(current: dict, previous: dict) -> bool:
    return any(count > previous.get(key, 0) for key, count in current.items())


def queue(notes_dir: Path, *, today: date | None = None) -> list[dict]:
    """Merge current signal patterns with durable observations/dispositions."""
    today = today or date.today()
    stored = {}
    for event in _events(Path(notes_dir) / "08_indices" / LOG):
        stored[event["item"]["id"]] = event["item"]
    current = {item["id"]: item for item in pattern_items(notes_dir)}
    result = []
    for ident in sorted(stored.keys() | current.keys()):
        old = stored.get(ident, {})
        live = current.get(ident, old)
        item = {**old, **live, "state": old.get("state", "open")}
        if item["state"] in {"resolved", "dismissed"} and _has_new(
                live.get("evidence_checkpoint", {}), old.get("disposition_checkpoint", {})):
            item.update(state="open", recurrence=True)
        if item["state"] == "deferred" and item["until"] <= today.isoformat():
            item.update(state="open", defer_expired=True)
        result.append(_clean(item))
    return sorted(result, key=lambda i: (i["state"] != "open", i["id"]))


def _append(path: Path, action: str, item: dict) -> None:
    event = _clean({"ts": now_iso(), "action": action, "item": item})
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def sync(notes_dir: Path, items: list[dict] | None = None) -> int:
    """Persist new/changed observations. No semantic notes or task writes."""
    path = Path(notes_dir) / "08_indices" / LOG
    with FileLock(path):
        previous = {e["item"]["id"]: e["item"] for e in _events(path)}
        candidates = queue(notes_dir) if items is None else items
        count = 0
        for raw in candidates:
            item = _clean(raw)
            item.setdefault("state", "open")
            if item["id"] in previous and items is not None:
                old = previous[item["id"]]
                item = {**old, **item, "state": old["state"]}
                if old["state"] in {"resolved", "dismissed"} and _has_new(item.get("evidence_checkpoint", {}),
                            old.get("disposition_checkpoint", {})):
                    item.update(state="open", recurrence=True)
            if item != previous.get(item["id"]):
                _append(path, "observe", item)
                count += 1
        return count


def transition(notes_dir: Path, ident: str, action: str, *, artifact: str = "",
               reason: str = "", until: str = "") -> dict:
    if action not in {"resolve", "dismiss", "defer", "reopen"}:
        raise ValueError("Unknown improvement action")
    artifact, reason = strip_private_tags(artifact).strip(), strip_private_tags(reason).strip()
    if action == "resolve" and not (artifact or reason):
        raise ValueError("Resolve requires an artifact or explanation")
    if action == "dismiss" and not reason:
        raise ValueError("Dismiss requires a reason")
    if action == "defer" and date.fromisoformat(until) <= date.today():
        raise ValueError("Defer date must be in the future")
    path = Path(notes_dir) / "08_indices" / LOG
    with FileLock(path):
        item = next((i for i in queue(notes_dir) if i["id"] == ident), None)
        if item is None:
            raise KeyError(ident)
        item.update(state={"resolve": "resolved", "dismiss": "dismissed",
                           "defer": "deferred", "reopen": "open"}[action],
                    artifact=artifact, reason=reason, until=until,
                    disposition_checkpoint=item.get("evidence_checkpoint", {}))
        item.pop("recurrence", None)
        item.pop("defer_expired", None)
        _append(path, action, item)
        return item


def summary(notes_dir: Path) -> dict:
    items = [i for i in queue(notes_dir) if i["state"] == "open"]
    return {"open": len(items), "items": [
        {k: i[k] for k in ("id", "kind", "subject", "suggested_action")}
        for i in items[:3]]}

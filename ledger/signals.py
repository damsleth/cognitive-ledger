"""Signal capture and feedback loop for Cognitive Ledger.

Captures performance signals (retrieval hits/misses, corrections,
affirmations, ratings) and produces summaries for retrieval scoring
and consolidation.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ledger.config import get_config


SIGNAL_TYPES = (
    "retrieval_hit",
    "retrieval_miss",
    "correction",
    "affirmation",
    "stale_flag",
    "preference_applied",
    "rating",
)


def _now_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_signal(
    signal_type: str,
    *,
    query: str = "",
    note: str = "",
    detail: str = "",
    rating: int | None = None,
    session: str = "",
) -> dict[str, Any]:
    """Append a signal entry to signals.jsonl.

    Args:
        signal_type: One of SIGNAL_TYPES.
        query: Query text (for retrieval_hit/miss).
        note: Note path (for hit/correction/affirmation/stale_flag).
        detail: Free-text detail.
        rating: Explicit 1-10 rating (for rating type).
        session: Optional session identifier.

    Returns:
        The signal entry dict that was appended.

    Raises:
        ValueError: If signal_type is not recognised.
    """
    if signal_type not in SIGNAL_TYPES:
        raise ValueError(
            f"Unknown signal type: {signal_type!r}. "
            f"Valid types: {', '.join(SIGNAL_TYPES)}"
        )

    entry: dict[str, Any] = {
        "ts": _now_iso(),
        "type": signal_type,
    }
    if query:
        entry["query"] = query
    if note:
        entry["note"] = note
    if detail:
        entry["detail"] = detail
    if rating is not None:
        entry["rating"] = max(1, min(10, int(rating)))
    if session:
        entry["session"] = session

    config = get_config()
    signals_path = config.signals_path
    signals_path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(entry, ensure_ascii=False)
    with open(signals_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

    return entry


def read_signals(
    since: str | None = None,
    type_filter: str | None = None,
    signals_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read signal entries from signals.jsonl.

    Args:
        since: ISO 8601 timestamp; only return signals after this time.
        type_filter: Only return signals of this type.
        signals_path: Override path (for testing).

    Returns:
        List of signal dicts, ordered by timestamp.
    """
    path = signals_path or get_config().signals_path
    if not path.is_file():
        return []

    signals: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if type_filter and entry.get("type") != type_filter:
            continue
        if since and entry.get("ts", "") <= since:
            continue
        signals.append(entry)

    return signals


def summarize_signals(
    signals_path: Path | None = None,
    signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a per-note signal summary from the full signal log.

    Args:
        signals_path: Override path (for testing).
        signals: Pre-loaded signal list (avoids re-reading JSONL).

    Returns:
        Dict mapping note paths to summary stats, plus a metadata key.
    """
    if signals is None:
        signals = read_signals(signals_path=signals_path)
    notes: dict[str, dict[str, Any]] = {}

    for entry in signals:
        note_path = entry.get("note", "")
        if not note_path:
            continue

        if note_path not in notes:
            notes[note_path] = {
                "hit_count": 0,
                "last_hit": "",
                "corrections": 0,
                "affirmations": 0,
                "rating_count": 0,
                "rating_sum": 0,
                "rating_min": None,
                "rating_max": None,
                "stale_flags": 0,
                "preference_applied": 0,
            }

        stats = notes[note_path]
        sig_type = entry.get("type", "")
        ts = entry.get("ts", "")

        if sig_type == "retrieval_hit":
            stats["hit_count"] += 1
            if ts > stats["last_hit"]:
                stats["last_hit"] = ts
        elif sig_type == "correction":
            stats["corrections"] += 1
        elif sig_type == "affirmation":
            stats["affirmations"] += 1
        elif sig_type == "stale_flag":
            stats["stale_flags"] += 1
        elif sig_type == "preference_applied":
            stats["preference_applied"] += 1
        elif sig_type == "rating":
            if "rating" in entry:
                r = entry["rating"]
                stats["rating_count"] += 1
                stats["rating_sum"] += r
                if stats["rating_min"] is None or r < stats["rating_min"]:
                    stats["rating_min"] = r
                if stats["rating_max"] is None or r > stats["rating_max"]:
                    stats["rating_max"] = r

    # Compute signal_score for each note
    for note_path, stats in notes.items():
        aff = stats["affirmations"]
        cor = stats["corrections"]
        stale = stats["stale_flags"]
        hits = stats["hit_count"]
        negative = cor + stale
        sentiment = (aff - negative) / (aff + negative + 1)
        # Usage factor: scales positive sentiment by hit frequency,
        # but negative sentiment passes through even with zero hits
        # so corrections/stale_flags always demote.
        usage = min(hits / 10.0, 1.0)
        if sentiment >= 0:
            stats["signal_score"] = round(sentiment * usage, 4)
        else:
            stats["signal_score"] = round(sentiment, 4)

    # Collect retrieval miss stats
    miss_queries: dict[str, int] = {}
    for entry in signals:
        if entry.get("type") == "retrieval_miss":
            q = entry.get("query", "")
            if q:
                miss_queries[q] = miss_queries.get(q, 0) + 1

    return {
        "_meta": {
            "total_signals": len(signals),
            "summarized_at": _now_iso(),
        },
        "notes": notes,
        "retrieval_misses": dict(
            sorted(miss_queries.items(), key=lambda kv: kv[1], reverse=True)[:20]
        ),
    }


def write_summary(signals_path: Path | None = None) -> Path:
    """Rebuild and write signal_summary.json.

    Returns:
        Path to the written summary file.
    """
    config = get_config()
    summary = summarize_signals(signals_path=signals_path)
    output_path = config.signal_summary_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return output_path


def load_signal_summary(summary_path: Path | None = None) -> dict[str, Any]:
    """Load the precomputed signal summary (for retrieval scoring).

    Returns:
        The summary dict, or empty structure if file doesn't exist.
    """
    path = summary_path or get_config().signal_summary_path
    if not path.is_file():
        return {"_meta": {"total_signals": 0}, "notes": {}, "retrieval_misses": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return {"_meta": {"total_signals": 0}, "notes": {}, "retrieval_misses": {}}


def get_signal_score(note_rel_path: str, summary: dict[str, Any] | None = None) -> float:
    """Get the signal score for a note from the summary.

    Args:
        note_rel_path: Relative path of the note.
        summary: Pre-loaded summary dict (avoids re-reading).

    Returns:
        Signal score (float, typically -1.0 to 1.0), or 0.0 if not found.
    """
    if summary is None:
        summary = load_signal_summary()
    notes = summary.get("notes", {})
    stats = notes.get(note_rel_path, {})
    return float(stats.get("signal_score", 0.0))


def signal_stats(signals_path: Path | None = None) -> dict[str, Any]:
    """Compute aggregate stats for display.

    Returns:
        Dict with counts by type, top notes, coverage gaps.
    """
    signals = read_signals(signals_path=signals_path)
    by_type: dict[str, int] = {}
    for entry in signals:
        sig_type = entry.get("type", "unknown")
        by_type[sig_type] = by_type.get(sig_type, 0) + 1

    summary = summarize_signals(signals=signals)
    notes_data = summary.get("notes", {})
    top_notes = sorted(
        notes_data.items(),
        key=lambda kv: kv[1].get("hit_count", 0),
        reverse=True,
    )[:10]

    corrections_pending = [
        (path, stats)
        for path, stats in notes_data.items()
        if stats.get("corrections", 0) > 0
    ]

    return {
        "total": len(signals),
        "by_type": by_type,
        "top_notes": [(path, stats.get("hit_count", 0)) for path, stats in top_notes],
        "corrections_pending": len(corrections_pending),
        "retrieval_misses": summary.get("retrieval_misses", {}),
    }

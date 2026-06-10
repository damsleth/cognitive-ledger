"""Signal capture and feedback loop for Cognitive Ledger.

Captures performance signals (retrieval hits/misses, corrections,
affirmations, ratings) and produces summaries for retrieval scoring
and consolidation.
"""

from __future__ import annotations

import datetime as dt
import enum
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.io.safe_write import safe_append_line, FileLock


class ActivationState(enum.Enum):
    ACTIVE = "active"
    READY = "ready"
    ACCRUING = "accruing"


@dataclass(frozen=True)
class ActivationStatus:
    state: ActivationState
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"state": self.state.value, "message": self.message}


SIGNAL_TYPES = (
    "retrieval_hit",
    "retrieval_miss",
    "correction",
    "affirmation",
    "stale_flag",
    "preference_applied",
    "rating",
    "supersession",
    "contradiction_flagged",
    "llm_judged",
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
    synthetic: bool = False,
    source: str = "",
    channel: str = "",
) -> dict[str, Any]:
    """Append a signal entry to signals.jsonl.

    Args:
        signal_type: One of SIGNAL_TYPES.
        query: Query text (for retrieval_hit/miss).
        note: Note path (for hit/correction/affirmation/stale_flag).
        detail: Free-text detail.
        rating: Explicit 1-10 rating (for rating type).
        session: Optional session identifier.
        synthetic: If True, mark event as LLM-seeded (not real user feedback).
            Synthetic events are down-weighted in ``summarize_signals`` by
            ``config.synthetic_weight`` (default 0.5).
        source: Source identifier for synthetic events, e.g. ``"llm_judge"``.

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
    if synthetic:
        entry["synthetic"] = True
    if source:
        entry["source"] = source
    if channel:
        entry["channel"] = channel

    config = get_config()
    signals_path = config.signals_path
    signals_path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(entry, ensure_ascii=False)
    safe_append_line(signals_path, line)

    return entry


def append_signal_raw(entry: dict[str, Any], signals_path: Path) -> None:
    """Write a pre-built signal dict directly to signals.jsonl.

    Used by the seeding workflow to bulk-write synthetic events without
    going through ``append_signal``'s config lookup.  The caller must
    ensure the entry is well-formed (has ``ts`` and ``type`` keys).

    Args:
        entry: Signal dict to write.
        signals_path: Absolute path to the signals.jsonl file.
    """
    signals_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False)
    safe_append_line(signals_path, line)


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

    Synthetic signals (``synthetic: true``) are down-weighted by
    ``config.synthetic_weight`` (default 0.5) relative to real signals.
    The 20-signal gate (``signal_min_entries``) counts only REAL
    (non-synthetic) signals so seeded events do not artificially
    activate scoring.

    Old JSONL files without the ``synthetic`` field parse identically to
    before — missing ``synthetic`` is treated as ``False``.

    Args:
        signals_path: Override path (for testing).
        signals: Pre-loaded signal list (avoids re-reading JSONL).

    Returns:
        Dict mapping note paths to summary stats, plus a metadata key.
    """
    if signals is None:
        signals = read_signals(signals_path=signals_path)

    config = get_config()
    synthetic_weight: float = getattr(config, "synthetic_weight", 0.5)

    notes: dict[str, dict[str, Any]] = {}
    real_signal_count: int = 0

    for entry in signals:
        is_synthetic = bool(entry.get("synthetic", False))
        if not is_synthetic:
            real_signal_count += 1

        # Weight multiplier: synthetic events count fractionally
        weight: float = synthetic_weight if is_synthetic else 1.0

        sig_type = entry.get("type", "")
        if sig_type == "llm_judged" and not bool(entry.get("relevant", False)):
            # A negative top-k judge verdict is useful audit data, but treating
            # it as a global note correction would demote notes for one query.
            # It also should not make the note look human-reviewed.
            continue

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
                # Synthetic-specific counters (not used in signal_score formula
                # directly, but available for diagnostics)
                "synthetic_hits": 0,
                "synthetic_corrections": 0,
                "synthetic_affirmations": 0,
            }

        stats = notes[note_path]
        ts = entry.get("ts", "")

        if sig_type == "retrieval_hit" or sig_type == "llm_judged":
            stats["hit_count"] += weight
            if ts > stats["last_hit"]:
                stats["last_hit"] = ts
            if is_synthetic:
                stats["synthetic_hits"] += 1
        elif sig_type == "correction":
            stats["corrections"] += weight
            if is_synthetic:
                stats["synthetic_corrections"] += 1
        elif sig_type == "affirmation":
            stats["affirmations"] += weight
            if is_synthetic:
                stats["synthetic_affirmations"] += 1
        elif sig_type == "stale_flag":
            stats["stale_flags"] += weight
        elif sig_type == "preference_applied":
            stats["preference_applied"] += weight
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

    # Collect retrieval miss stats (real and synthetic)
    miss_queries: dict[str, float] = {}
    for entry in signals:
        if entry.get("type") == "retrieval_miss":
            q = entry.get("query", "")
            if q:
                is_synthetic = bool(entry.get("synthetic", False))
                weight = synthetic_weight if is_synthetic else 1.0
                miss_queries[q] = miss_queries.get(q, 0.0) + weight

    return {
        "_meta": {
            "total_signals": len(signals),
            "real_signals": real_signal_count,
            "summarized_at": _now_iso(),
        },
        "notes": notes,
        "retrieval_misses": dict(
            sorted(miss_queries.items(), key=lambda kv: kv[1], reverse=True)[:20]
        ),
    }


def purge_synthetic_signals(
    signals_path: Path | None = None,
) -> int:
    """Remove all synthetic signal events from signals.jsonl.

    Reads the existing JSONL, drops every entry where ``synthetic`` is
    truthy, and rewrites the file in place.  Returns the number of
    entries removed.

    This is a full rollback of all LLM-seeded signals.  Run
    ``ledger signal summarize`` afterwards to refresh ``signal_summary.json``.

    Args:
        signals_path: Override path (for testing).

    Returns:
        Number of entries that were removed.
    """
    config = get_config()
    path = signals_path or config.signals_path
    if not path.is_file():
        return 0

    kept: list[str] = []
    removed = 0
    with FileLock(path):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # Preserve unparseable lines as-is
                kept.append(line)
                continue
            if entry.get("synthetic", False):
                removed += 1
            else:
                kept.append(line)

        # Rewrite atomically: write to .tmp then rename
        from ledger.io.safe_write import atomic_write
        tmp_path = path.with_suffix(".jsonl.tmp")
        with atomic_write(tmp_path) as f:
            f.write("\n".join(kept) + ("\n" if kept else ""))
        tmp_path.replace(path)
    return removed


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
        Dict with counts by type, top notes, coverage gaps, and
        ``real_total`` — the count of non-synthetic signals used for
        the activation gate.
    """
    signals = read_signals(signals_path=signals_path)
    by_type: dict[str, int] = {}
    real_total: int = 0
    for entry in signals:
        sig_type = entry.get("type", "unknown")
        by_type[sig_type] = by_type.get(sig_type, 0) + 1
        if not entry.get("synthetic", False):
            real_total += 1

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
        "real_total": real_total,
        "by_type": by_type,
        "top_notes": [(path, stats.get("hit_count", 0)) for path, stats in top_notes],
        "corrections_pending": len(corrections_pending),
        "retrieval_misses": summary.get("retrieval_misses", {}),
    }


def activation_status(
    total_signals: int,
    config=None,
    real_signals: int | None = None,
) -> ActivationStatus:
    """Describe whether signal feedback is influencing retrieval ranking.

    Three states:
      - ``active``: ``score_weight_signal > 0`` — signals affect ranking.
      - ``ready``: enough real signals accrued but the weight is still 0, so
        they are ignored — nudge the user to validate and turn it on.
      - ``accruing``: below ``signal_min_entries`` real signals — review more
        notes first.

    Args:
        total_signals: All signal events (including synthetic).
        config: LedgerConfig instance (uses global config if None).
        real_signals: Non-synthetic signal count.  When provided, the
            activation gate uses this count instead of ``total_signals``
            so that LLM-seeded events do not artificially trigger activation.
            Falls back to ``total_signals`` when absent (backward-compatible).
    """
    config = config or get_config()
    weight = config.score_weight_signal
    threshold = config.signal_min_entries

    # Gate uses real signals only; seeded events bootstrap ranking but do not
    # count towards the human-feedback threshold.
    gate_count = real_signals if real_signals is not None else total_signals

    if weight > 0:
        return ActivationStatus(
            state=ActivationState.ACTIVE,
            message=f"Signal-aware ranking is ON (score_weight_signal={weight:g}).",
        )
    if gate_count >= threshold:
        return ActivationStatus(
            state=ActivationState.READY,
            message=(
                f"{gate_count} real signals (≥ {threshold}) but score_weight_signal "
                "is 0.0, so ranking ignores them. Validate with `ledger ab run` "
                "then raise the weight in config.yaml to activate."
            ),
        )
    return ActivationStatus(
        state=ActivationState.ACCRUING,
        message=(
            f"{gate_count}/{threshold} real signals — review more notes "
            "(`ledger review`) to reach the activation threshold."
        ),
    )


def signal_summary_if_active(config=None) -> dict[str, Any] | None:
    """Return loaded signal summary when scoring is enabled and threshold met.

    Returns the summary dict when ``score_weight_signal > 0`` AND the
    ``_meta`` real-signal count is >= ``signal_min_entries``, else ``None``.
    """
    config = config or get_config()
    if config.score_weight_signal <= 0:
        return None
    summary = load_signal_summary()
    _meta = summary.get("_meta", {})
    real_total = _meta.get("real_signals", _meta.get("total_signals", 0))
    if real_total >= config.signal_min_entries:
        return summary
    return None

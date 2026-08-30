"""Pattern mining over the raw signal log (a WikiSkill-style wiki maintainer).

Plays the *wiki maintainer* role from WikiSkill (arXiv:2608.27454) for the
ledger's memory corpus: it analyses the append-only raw layer
(``signals.jsonl``) and distills it into a pattern directory -- recurring
failure modes and successful strategies, each with evidence counts and a
suggested action. Layer mapping:

  raw layer   ->  ``08_indices/signals.jsonl``   (immutable feedback traces)
  wiki layer  ->  ``08_indices/patterns.{json,md}``  (this module's output)
  skill layer ->  the notes + retrieval config the patterns suggest acting on

The pattern directory is *derived*: rebuilding it from the signal log is
always safe and is what ``ledger signal patterns`` does. It never mutates
notes itself -- it names the failure mode and hands the agent (or the review
flow) a concrete next action.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ledger.layout import patterns_json_path, patterns_md_path
from ledger.signals import read_signals, summarize_signals

# Failure-mode kinds
KIND_REPEATED_MISS = "repeated_retrieval_miss"
KIND_CORRECTION_PRONE = "correction_prone_note"
KIND_STALE = "stale_note"
KIND_CONTRADICTED = "contradicted_note"
# Strategy kinds
KIND_HIGH_VALUE = "high_value_note"

CATEGORY_FAILURE = "failure"
CATEGORY_STRATEGY = "strategy"

DEFAULT_MIN_MISSES = 3.0
DEFAULT_MIN_CORRECTIONS = 2.0
DEFAULT_MIN_STALE = 2.0
DEFAULT_MIN_CONTRADICTIONS = 1.0
DEFAULT_STRATEGY_MIN_HITS = 5.0
DEFAULT_STRATEGY_MIN_SCORE = 0.3

_ACTIONS = {
    KIND_REPEATED_MISS: (
        "Capture an atomic note that answers this query, or add its terms to "
        "an existing note's tags/aliases so retrieval can find it."
    ),
    KIND_CORRECTION_PRONE: (
        "Review the note; if it is wrong, supersede it with a corrected note "
        "(bitemporal supersede) instead of patching in place."
    ),
    KIND_STALE: "Refresh the note's content or archive it to 09_archive.",
    KIND_CONTRADICTED: (
        "Resolve the contradiction: keep the note that is true now and "
        "supersede the other."
    ),
    KIND_HIGH_VALUE: (
        "This note earns its retrieval slot -- keep it atomic, current, and "
        "well-linked; use it as a template for new notes on nearby topics."
    ),
}


def _now_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seen_window(
    signals: list[dict[str, Any]], *, note: str | None = None, query: str | None = None
) -> tuple[str, str]:
    """Return (first_seen, last_seen) timestamps for a subject's raw signals."""
    first, last = "", ""
    for entry in signals:
        if note is not None and entry.get("note") != note:
            continue
        if query is not None and (
            entry.get("type") != "retrieval_miss" or entry.get("query") != query
        ):
            continue
        ts = entry.get("ts", "")
        if not ts:
            continue
        if not first or ts < first:
            first = ts
        if ts > last:
            last = ts
    return first, last


def _pattern(
    kind: str,
    category: str,
    subject: str,
    evidence: dict[str, Any],
    window: tuple[str, str],
) -> dict[str, Any]:
    first_seen, last_seen = window
    return {
        "kind": kind,
        "category": category,
        "subject": subject,
        "evidence": evidence,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "suggested_action": _ACTIONS[kind],
    }


def mine_patterns(
    signals: list[dict[str, Any]] | None = None,
    *,
    signals_path: Path | None = None,
    min_misses: float = DEFAULT_MIN_MISSES,
    min_corrections: float = DEFAULT_MIN_CORRECTIONS,
    min_stale: float = DEFAULT_MIN_STALE,
    min_contradictions: float = DEFAULT_MIN_CONTRADICTIONS,
    strategy_min_hits: float = DEFAULT_STRATEGY_MIN_HITS,
    strategy_min_score: float = DEFAULT_STRATEGY_MIN_SCORE,
) -> dict[str, Any]:
    """Distill the raw signal log into failure-mode and strategy patterns.

    Thresholds compare against the *weighted* counts from
    ``summarize_signals`` (synthetic signals are already down-weighted there),
    so seeded events alone are unlikely to cross a pattern threshold.

    Returns a dict with ``_meta`` and a ``patterns`` list, failures first,
    each category sorted by evidence weight descending.
    """
    if signals is None:
        signals = read_signals(signals_path=signals_path)
    summary = summarize_signals(signals=signals)

    failures: list[tuple[float, dict[str, Any]]] = []
    strategies: list[tuple[float, dict[str, Any]]] = []

    for query, weight in summary.get("retrieval_misses", {}).items():
        if weight >= min_misses:
            failures.append((
                float(weight),
                _pattern(
                    KIND_REPEATED_MISS,
                    CATEGORY_FAILURE,
                    query,
                    {"miss_weight": float(weight)},
                    _seen_window(signals, query=query),
                ),
            ))

    for note_path, stats in summary.get("notes", {}).items():
        window = _seen_window(signals, note=note_path)
        corrections = float(stats.get("corrections", 0.0))
        stale = float(stats.get("stale_flags", 0.0))
        contradictions = float(stats.get("contradictions", 0.0))
        hits = float(stats.get("hit_count", 0.0))
        score = float(stats.get("signal_score", 0.0))

        if corrections >= min_corrections:
            failures.append((
                corrections,
                _pattern(
                    KIND_CORRECTION_PRONE,
                    CATEGORY_FAILURE,
                    note_path,
                    {"corrections": corrections, "signal_score": score},
                    window,
                ),
            ))
        if stale >= min_stale:
            failures.append((
                stale,
                _pattern(
                    KIND_STALE,
                    CATEGORY_FAILURE,
                    note_path,
                    {"stale_flags": stale},
                    window,
                ),
            ))
        if contradictions >= min_contradictions:
            failures.append((
                contradictions,
                _pattern(
                    KIND_CONTRADICTED,
                    CATEGORY_FAILURE,
                    note_path,
                    {"contradictions": contradictions},
                    window,
                ),
            ))
        if hits >= strategy_min_hits and score >= strategy_min_score:
            strategies.append((
                hits,
                _pattern(
                    KIND_HIGH_VALUE,
                    CATEGORY_STRATEGY,
                    note_path,
                    {"hit_count": hits, "signal_score": score},
                    window,
                ),
            ))

    failures.sort(key=lambda item: -item[0])
    strategies.sort(key=lambda item: -item[0])
    patterns = [p for _, p in failures] + [p for _, p in strategies]

    return {
        "_meta": {
            "generated_at": _now_iso(),
            "total_signals": len(signals),
            "failure_count": len(failures),
            "strategy_count": len(strategies),
            "thresholds": {
                "min_misses": min_misses,
                "min_corrections": min_corrections,
                "min_stale": min_stale,
                "min_contradictions": min_contradictions,
                "strategy_min_hits": strategy_min_hits,
                "strategy_min_score": strategy_min_score,
            },
        },
        "patterns": patterns,
    }


_KIND_HEADINGS = {
    KIND_REPEATED_MISS: "Repeated retrieval misses",
    KIND_CORRECTION_PRONE: "Correction-prone notes",
    KIND_STALE: "Stale notes",
    KIND_CONTRADICTED: "Contradicted notes",
    KIND_HIGH_VALUE: "High-value notes",
}


def render_patterns_md(result: dict[str, Any]) -> str:
    """Render the mined pattern directory as human-readable Markdown."""
    meta = result.get("_meta", {})
    patterns = result.get("patterns", [])
    lines = [
        "# Signal patterns",
        "",
        f"*Generated by `ledger signal patterns` on {meta.get('generated_at', '')} "
        f"from {meta.get('total_signals', 0)} signals. Derived from signals.jsonl "
        "-- safe to regenerate.*",
        "",
    ]
    for category, title in ((CATEGORY_FAILURE, "Failure modes"), (CATEGORY_STRATEGY, "Strategies")):
        entries = [p for p in patterns if p.get("category") == category]
        lines.append(f"## {title}")
        lines.append("")
        if not entries:
            lines.append("*None detected.*")
            lines.append("")
            continue
        for kind in _KIND_HEADINGS:
            kind_entries = [p for p in entries if p.get("kind") == kind]
            if not kind_entries:
                continue
            lines.append(f"### {_KIND_HEADINGS[kind]}")
            lines.append("")
            for p in kind_entries:
                evidence = ", ".join(
                    f"{key}={value:g}" if isinstance(value, float) else f"{key}={value}"
                    for key, value in p.get("evidence", {}).items()
                )
                window = ""
                if p.get("last_seen"):
                    window = f" (first {p.get('first_seen', '?')}, last {p['last_seen']})"
                lines.append(f"- `{p['subject']}` -- {evidence}{window}")
                lines.append(f"  - action: {p['suggested_action']}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_patterns(
    ledger_notes_dir: Path,
    *,
    signals_path: Path | None = None,
    **thresholds: float,
) -> tuple[Path, Path, dict[str, Any]]:
    """Mine patterns and write ``patterns.json`` + ``patterns.md`` to indices.

    Returns (json_path, md_path, mined_result).
    """
    result = mine_patterns(signals_path=signals_path, **thresholds)
    json_path = patterns_json_path(ledger_notes_dir)
    md_path = patterns_md_path(ledger_notes_dir)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    md_path.write_text(render_patterns_md(result), encoding="utf-8")
    return json_path, md_path, result

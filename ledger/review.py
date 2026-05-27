"""Scan-and-judge review loop for the signal feedback system.

Provides a prioritized review *queue* (the notes most worth judging),
a tight verdict vocabulary (keep / wrong / stale / rate / skip), and a
keyboard-driven curses TUI to walk the queue and emit signals.

All signal I/O routes through :mod:`ledger.signals`; this module never
touches ``signals.jsonl`` directly. The queue logic and verdict mapping
are pure and testable; the curses loop is a thin shell over them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ledger import browse, signals
from ledger.config import get_config
from ledger.layout import logical_path


# Keystroke -> signal type for the three review-time verdicts. Digits map
# to ``rating`` separately; everything else (space/enter) is a skip.
VERDICT_SIGNALS = {
    "k": "affirmation",
    "w": "correction",
    "s": "stale_flag",
}


@dataclass
class ReviewItem:
    """One note queued for review, with its priority and the reasons why."""

    stem: str
    note_type: str
    rel_path: str
    """Logical ``notes/<folder>/<stem>.md`` path — the signal ``note`` key."""
    title: str
    body: str
    frontmatter: dict[str, Any]
    priority: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        """Single-line 'why you're seeing this' summary for the card."""
        return " · ".join(self.reasons) if self.reasons else "routine review"


# ---------------------------------------------------------------------------
# Prioritization — the "insight back to the ledger"
# ---------------------------------------------------------------------------


def _days_since(updated: str) -> int | None:
    """Days since an ISO ``updated`` timestamp, or None if unparseable."""
    s = str(updated or "").strip()
    if not s:
        return None
    candidate = s.replace("Z", "+00:00")
    for parse in (
        lambda v: dt.datetime.fromisoformat(v),
        lambda v: dt.datetime.strptime(v, "%Y-%m-%d"),
    ):
        try:
            d = parse(candidate)
        except ValueError:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return (dt.datetime.now(tz=dt.timezone.utc) - d).days
    return None


def score_item(
    stats: dict[str, Any] | None,
    frontmatter: dict[str, Any],
    *,
    stale_days: int,
    min_confidence: float,
) -> tuple[float, list[str]]:
    """Score how much *judging this note* would help, with human reasons.

    Higher priority = more worth a verdict. Corrections pending and
    high-traffic-but-unaffirmed notes dominate; never-reviewed,
    stale-by-age, and low-confidence notes follow.
    """
    priority = 0.0
    reasons: list[str] = []

    if not stats:
        priority += 3.0
        reasons.append("never reviewed")
    else:
        corrections = int(stats.get("corrections", 0))
        hits = int(stats.get("hit_count", 0))
        affirmations = int(stats.get("affirmations", 0))
        if corrections > 0:
            priority += 5.0
            reasons.append(
                f"{corrections} correction{'s' if corrections != 1 else ''} pending"
            )
        if hits >= 3 and affirmations == 0:
            priority += 4.0
            reasons.append(f"{hits} hits, never affirmed")

    days = _days_since(frontmatter.get("updated", ""))
    if days is not None and days > stale_days:
        priority += 2.0
        reasons.append(f"{days}d since update")

    confidence = frontmatter.get("confidence")
    try:
        conf_val = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        conf_val = None
    source = str(frontmatter.get("source", "")).strip().lower()
    if conf_val is not None and conf_val < min_confidence:
        priority += 1.5
        reasons.append(f"low confidence ({conf_val:g})")
    elif source == "inferred":
        priority += 1.0
        reasons.append("inferred, unconfirmed")

    return priority, reasons


def build_review_queue(
    *,
    type_filter: str | None = None,
    scope: str | None = None,
    stale_days: int = 180,
    limit: int | None = None,
    unjudged_only: bool = False,
    summary: dict[str, Any] | None = None,
) -> list[ReviewItem]:
    """Build a priority-ordered review queue across the corpus.

    Args:
        type_filter: Restrict to one note type (e.g. ``"facts"``); None = all.
        scope: Restrict to notes whose frontmatter scope matches.
        stale_days: Age past which a note is flagged as a stale candidate.
        limit: Cap the queue length (after sorting by priority).
        unjudged_only: Only include notes that have never received a signal.
        summary: Pre-built signal summary (avoids re-reading); else computed.
    """
    cfg = get_config()
    if summary is None:
        summary = signals.summarize_signals()
    note_stats: dict[str, Any] = summary.get("notes", {})

    queue: list[ReviewItem] = []
    for item in browse.sorted_items(type_filter or "all"):
        if scope and scope != "all":
            note_scope = str(item.frontmatter.get("scope", "")).strip().lower()
            if note_scope and note_scope != scope:
                continue

        rel_path = str(
            logical_path(
                item.path,
                ledger_root=cfg.ledger_root,
                ledger_notes_dir=cfg.ledger_notes_dir,
            )
        )
        stats = note_stats.get(rel_path)
        if unjudged_only and stats:
            continue

        priority, reasons = score_item(
            stats,
            item.frontmatter,
            stale_days=stale_days,
            min_confidence=cfg.boot_min_confidence,
        )
        queue.append(
            ReviewItem(
                stem=Path(item.path).stem,
                note_type=item.type,
                rel_path=rel_path,
                title=item.title or Path(item.path).stem,
                body=item.body,
                frontmatter=item.frontmatter,
                priority=priority,
                reasons=reasons,
            )
        )

    queue.sort(
        key=lambda it: (it.priority, str(it.frontmatter.get("updated", ""))),
        reverse=True,
    )
    if limit:
        queue = queue[:limit]
    return queue


# ---------------------------------------------------------------------------
# Verdict mapping + session flush
# ---------------------------------------------------------------------------


def verdict_signal(
    item: ReviewItem,
    key: str,
    *,
    detail: str = "",
    session: str = "",
) -> dict[str, Any] | None:
    """Map a keystroke to ``append_signal`` kwargs, or None for skip.

    ``k``/``w``/``s`` -> affirmation/correction/stale_flag; ``1``-``9`` ->
    rating; anything else (space, enter, unknown) returns None.
    """
    kwargs: dict[str, Any] = {"note": item.rel_path}
    if session:
        kwargs["session"] = session

    if key in VERDICT_SIGNALS:
        kwargs["signal_type"] = VERDICT_SIGNALS[key]
        if key == "w" and detail:
            kwargs["detail"] = detail
        return kwargs
    if key in "123456789":
        kwargs["signal_type"] = "rating"
        kwargs["rating"] = int(key)
        return kwargs
    return None


def flush_session(entries: list[dict[str, Any]]) -> Path | None:
    """Append buffered verdict entries to signals.jsonl, then rebuild summary.

    Returns the signals.jsonl path, or None if there was nothing to flush.
    """
    if not entries:
        return None
    for kwargs in entries:
        signals.append_signal(**kwargs)
    signals.write_summary()
    return get_config().signals_path


# ---------------------------------------------------------------------------
# Dashboard — aggregate insight
# ---------------------------------------------------------------------------


def dashboard_data() -> dict[str, Any]:
    """Aggregate signal stats into a coverage + insight snapshot."""
    summary = signals.summarize_signals()
    stats = signals.signal_stats()
    notes: dict[str, Any] = summary.get("notes", {})

    total_notes = len(browse.sorted_items("all"))
    judged = len(notes)
    positive = neutral = negative = 0
    for note_stats in notes.values():
        score = float(note_stats.get("signal_score", 0.0))
        if score > 0:
            positive += 1
        elif score < 0:
            negative += 1
        else:
            neutral += 1

    return {
        "total_signals": stats["total"],
        "by_type": stats["by_type"],
        "total_notes": total_notes,
        "judged_notes": judged,
        "coverage": (judged / total_notes) if total_notes else 0.0,
        "score_dist": {
            "positive": positive,
            "neutral": neutral,
            "negative": negative,
        },
        "corrections_pending": stats["corrections_pending"],
        "retrieval_misses": stats["retrieval_misses"],
    }


def render_dashboard(data: dict[str, Any]) -> str:
    """Format dashboard_data() as a plain-text block."""
    lines: list[str] = []
    lines.append("Signal feedback dashboard")
    lines.append("=" * 40)
    lines.append(f"Total signals : {data['total_signals']}")
    coverage_pct = data["coverage"] * 100
    lines.append(
        f"Coverage      : {data['judged_notes']}/{data['total_notes']} "
        f"notes judged ({coverage_pct:.0f}%)"
    )

    by_type = data.get("by_type", {})
    if by_type:
        lines.append("")
        lines.append("By type:")
        for sig_type, count in sorted(by_type.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {count:4d}  {sig_type}")

    dist = data["score_dist"]
    lines.append("")
    lines.append(
        f"Score dist    : +{dist['positive']}  ·{dist['neutral']}  -{dist['negative']}"
    )
    if data["corrections_pending"]:
        lines.append(f"Corrections pending review: {data['corrections_pending']}")

    misses = data.get("retrieval_misses", {})
    if misses:
        lines.append("")
        lines.append("Top gaps (retrieval misses — what to write next):")
        for query, count in list(misses.items())[:5]:
            lines.append(f"  {count:3d}x  {query}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Curses TUI — thin shell over the logic above
# ---------------------------------------------------------------------------

_HELP_LINES = [
    "k  keep   (affirmation)      w  wrong (correction, asks reason)",
    "s  stale  (stale_flag)       1-9  rate",
    "space/enter  skip            u  undo last      q  quit & save",
    "up/down  scroll body         ?  toggle this help",
]


def run_review_tui(queue: list[ReviewItem], *, session_id: str = "") -> dict[str, Any]:
    """Drive the review queue interactively, flushing signals on exit.

    Returns a session summary: ``{judged, entries, signals_path}``.
    """
    if not queue:
        print("Nothing to review — the queue is empty.")
        return {"judged": 0, "entries": [], "signals_path": None}

    try:
        import curses
    except ImportError as exc:  # pragma: no cover - platform dependent
        raise RuntimeError(
            "The review TUI requires the stdlib 'curses' module, which is "
            "unavailable on this platform. Use `ledger review --queue` or "
            "`--stats` instead."
        ) from exc

    entries: list[dict[str, Any]] = []
    curses.wrapper(_review_loop, queue, entries, session_id)

    path = flush_session(entries)
    return {"judged": len(entries), "entries": entries, "signals_path": path}


def _review_loop(stdscr, queue, entries, session_id):  # pragma: no cover - curses UI
    import curses

    curses.curs_set(0)
    stdscr.keypad(True)
    idx = 0
    scroll = 0
    show_help = False
    # history of (idx_before, entry_or_None) so 'u' can step back + un-log.
    history: list[tuple[int, dict[str, Any] | None]] = []
    flash = ""

    while idx < len(queue):
        item = queue[idx]
        _draw_card(stdscr, item, idx, len(queue), len(entries), scroll, show_help, flash)
        flash = ""
        ch = stdscr.getch()

        if ch in (ord("q"),):
            break
        if ch in (ord("?"),):
            show_help = not show_help
            continue
        if ch in (curses.KEY_DOWN, ord("J")):
            scroll += 1
            continue
        if ch in (curses.KEY_UP, ord("K")):
            scroll = max(0, scroll - 1)
            continue
        if ch in (ord("u"),):
            if history:
                prev_idx, prev_entry = history.pop()
                if prev_entry is not None and entries:
                    entries.pop()
                idx = prev_idx
                scroll = 0
                flash = "undid last verdict"
            continue

        key = chr(ch) if 0 <= ch < 256 else ""
        if ch in (ord(" "), 10, 13):  # skip
            history.append((idx, None))
            idx += 1
            scroll = 0
            continue

        detail = ""
        if key == "w":
            detail = _prompt(stdscr, "correction reason (optional): ")
        entry = verdict_signal(item, key, detail=detail, session=session_id)
        if entry is None:
            flash = f"'{key}' — no verdict (k/w/s, 1-9, space=skip, ? help)"
            continue
        history.append((idx, entry))
        entries.append(entry)
        idx += 1
        scroll = 0


def _draw_card(stdscr, item, idx, total, judged, scroll, show_help, flash):  # pragma: no cover
    import curses
    import textwrap

    stdscr.erase()
    height, width = stdscr.getmaxyx()
    w = max(20, width - 2)

    def line(y, text, attr=0):
        if 0 <= y < height:
            try:
                stdscr.addnstr(y, 1, text, w, attr)
            except curses.error:
                pass

    line(0, f"ledger review   {total - idx} left · {judged} judged", curses.A_BOLD)
    line(1, "─" * w)
    line(2, f"{item.stem}", curses.A_BOLD)
    fm = item.frontmatter
    meta_bits = [item.note_type]
    if fm.get("confidence") is not None:
        meta_bits.append(f"conf {fm.get('confidence')}")
    if fm.get("source"):
        meta_bits.append(f"src {fm.get('source')}")
    if fm.get("updated"):
        meta_bits.append(str(fm.get("updated")))
    line(3, "  ·  ".join(str(b) for b in meta_bits))
    line(4, f"▸ {item.reason}", curses.A_DIM)
    line(5, "─" * w)

    body_top = 6
    footer_rows = 3 + (len(_HELP_LINES) if show_help else 0)
    body_height = max(1, height - body_top - footer_rows)
    wrapped: list[str] = []
    for raw in item.body.splitlines():
        wrapped.extend(textwrap.wrap(raw, w) or [""])
    visible = wrapped[scroll : scroll + body_height]
    for i, text in enumerate(visible):
        line(body_top + i, text)
    if scroll + body_height < len(wrapped):
        line(body_top + body_height - 1, "… (↓ for more)", curses.A_DIM)

    foot = height - footer_rows
    line(foot, "─" * w)
    line(
        foot + 1,
        "[k]eep  [w]rong  [s]tale  [1-9]rate  [space]skip  [u]ndo  [q]uit  [?]help",
        curses.A_BOLD,
    )
    if flash:
        line(foot + 2, flash, curses.A_REVERSE)
    if show_help:
        for i, htext in enumerate(_HELP_LINES):
            line(foot + 3 + i, htext, curses.A_DIM)
    stdscr.refresh()


def _prompt(stdscr, label) -> str:  # pragma: no cover - curses UI
    import curses

    height, width = stdscr.getmaxyx()
    curses.curs_set(1)
    curses.echo()
    try:
        try:
            stdscr.addnstr(height - 1, 1, label, max(1, width - 2), curses.A_REVERSE)
        except curses.error:
            pass
        stdscr.refresh()
        raw = stdscr.getstr(height - 1, min(len(label) + 1, width - 2), 200)
        text = raw.decode("utf-8", "replace").strip() if raw else ""
    finally:
        curses.noecho()
        curses.curs_set(0)
    return text

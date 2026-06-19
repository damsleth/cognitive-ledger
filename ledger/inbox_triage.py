"""Two-pane inbox triage TUI — a subtraction / exclusion model.

``ledger inbox triage`` launches this. Every candidate is ACCEPTED by default;
the user EXCLUDES the few that don't fit, optionally re-routes a note's target
type, then presses ``A`` to commit. Accepted notes are promoted to their type
folder; excluded notes are rejected (deleted + logged) after a confirm summary.

A banner pins the subtraction model to the top of the screen at all times so
"do nothing, then A" reads unmistakably as "accept everything".

Layout:  [ banner: SUBTRACTION MODE ]
         [ table (left) | preview (right) ]
         [ footer: keys ]

Data ops (load/apply) live in ``ledger.inbox``; this module is UI only.

Utility functions (_parse_command, _parse_range, _render_table, _handle_inspect,
_merge_target_for, _handle_unset) are also exported for use by tests and any
non-TUI callers.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from ledger.config import get_config
from ledger.inbox import (
    InboxCandidate,
    TriageAction,
    apply_actions,
    load_candidates_for_triage,
    triage_suggestions,
)
from ledger.layout import resolve_path
from ledger.validation import validate_note_type

# ---------------------------------------------------------------------------
# Command-line triage utilities (shared by tests and any non-TUI callers)
# ---------------------------------------------------------------------------

_ACTION_MARKERS: dict[str, str] = {
    "accept": "A",
    "reject": "R",
    "merge": "M",
    "defer": "D",
    "skip": "S",
}

_HELP_TEXT = """\
Commands:
  a <range>[:type]  accept (promote). Optional type override, e.g. a 1:preferences
  r <range>         reject (logs rejection signature + deletes)
  m <idx>           merge into the candidate's merge_with: target
  d <idx>           defer (leave in inbox)
  s <idx>           skip (no-op, leave in inbox)
  i <idx>           inspect: print candidate body; side-by-side diff for conflict rows
  p <idx>           inspect (alias for i)
  u <range>         unset a queued action
  ?                 show this help
  q                 quit, applying queued actions
  Q                 quit, discarding queued actions

Range syntax: 5-12 (inclusive), 5- (5 to end), comma-mixed (1,3-5,8).
Latest decision per row wins.
Note: range-accept skips contradict rows; accept a contradiction explicitly with 'a <n>'."""


def _parse_range(spec: str, count: int) -> list[int]:
    """Parse a 1-based index range/list spec into a sorted unique list.

    Raises ValueError on any invalid token (no silent partial application).
    """
    if not spec.strip():
        raise ValueError("missing index")

    indices: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            raise ValueError(f"empty index in {spec!r}")
        if "-" in token:
            lo_s, _, hi_s = token.partition("-")
            lo_s = lo_s.strip()
            hi_s = hi_s.strip()
            if not lo_s:
                raise ValueError(f"invalid range: {token!r}")
            try:
                lo = int(lo_s)
            except ValueError as exc:
                raise ValueError(f"invalid index: {lo_s!r}") from exc
            if not hi_s:
                hi = count
            else:
                try:
                    hi = int(hi_s)
                except ValueError as exc:
                    raise ValueError(f"invalid index: {hi_s!r}") from exc
            if lo > hi:
                raise ValueError(f"reversed range: {token!r}")
            for i in range(lo, hi + 1):
                indices.add(i)
        else:
            try:
                i = int(token)
            except ValueError as exc:
                raise ValueError(f"invalid index: {token!r}") from exc
            indices.add(i)

    for i in indices:
        if i < 1 or i > count:
            raise ValueError(f"index out of range: {i} (1-{count})")

    return sorted(indices)


def _merge_target_for(candidate: InboxCandidate) -> Path:
    """Resolve a candidate's ``merge_with:`` hint to an absolute path."""
    if not candidate.merge_with:
        raise ValueError("no merge target")
    config = get_config()
    return resolve_path(
        candidate.merge_with,
        ledger_root=config.ledger_root,
        ledger_notes_dir=config.ledger_notes_dir,
    )


def _parse_command(line: str, candidates: list[InboxCandidate]) -> list[TriageAction]:
    """Parse one command line into a list of TriageAction.

    Raises ValueError on any malformed command or invalid index/type.
    Inspect/help/quit verbs are handled by the loop, not here.

    E7 range-accept guard: when the ``a`` command covers MORE THAN ONE row,
    rows whose conflict_classification == "contradict" are silently dropped and
    a note is printed. A single-index ``a <n>`` still accepts a contradiction.
    """
    count = len(candidates)
    parts = line.split(None, 1)
    cmd = parts[0]
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "a":
        spec, _, type_override = arg.partition(":")
        target_type: str | None = None
        if type_override.strip():
            target_type = validate_note_type(type_override.strip())
        rows = _parse_range(spec, count)
        # Range-accept guard: multi-row accepts skip contradiction candidates.
        if len(rows) > 1:
            filtered = []
            for r in rows:
                c = candidates[r - 1]
                if c.conflict_classification == "contradict":
                    print(
                        f"note: #{r} is a contradiction — accept it explicitly with 'a {r}'"
                    )
                else:
                    filtered.append(r)
            rows = filtered
        return [
            TriageAction(row=r, action="accept", target_type=target_type)
            for r in rows
        ]

    if cmd == "r":
        rows = _parse_range(arg, count)
        return [TriageAction(row=r, action="reject") for r in rows]

    if cmd in ("d", "s"):
        rows = _parse_range(arg, count)
        action = "defer" if cmd == "d" else "skip"
        return [TriageAction(row=r, action=action) for r in rows]  # type: ignore[arg-type]

    if cmd == "m":
        rows = _parse_range(arg, count)
        actions: list[TriageAction] = []
        for r in rows:
            candidate = candidates[r - 1]
            if not candidate.merge_with:
                raise ValueError(f"#{r}: no merge target")
            actions.append(
                TriageAction(
                    row=r,
                    action="merge",
                    target_note=_merge_target_for(candidate),
                )
            )
        return actions

    raise ValueError(f"unknown command: {cmd!r}")


def _render_table(
    candidates: list[InboxCandidate],
    actions: dict[int, TriageAction],
) -> None:
    """Print a plain-text table of candidates with their queued actions.

    Includes a ``conflict?`` column (width 12) after ``conf`` for E8.
    Rows with ``merge_with`` show ``[merge?]`` appended to the title.
    """
    n_yaams = sum(1 for c in candidates if c.promoted_by == "yaams")
    n_manual = len(candidates) - n_yaams
    print(f"\nInbox ({n_yaams} candidates from yaams + {n_manual} manual):")

    width = shutil.get_terminal_size((100, 24)).columns
    # Fixed columns: idx(3) act(3) type(12) conf(4) conflict(12) sig(8) + separators.
    fixed = 3 + 3 + 12 + 4 + 12 + 8 + 6 * 3
    title_w = max(12, width - fixed)

    header = (
        f"{'#':>3} | {'act':<3} | {'type':<12} | "
        f"{'title':<{title_w}} | {'conf':<4} | {'conflict?':<12} | {'sig':<8}"
    )
    print(header)
    print("-" * min(width, len(header)))

    for idx, c in enumerate(candidates, 1):
        action = actions.get(idx)
        marker = _ACTION_MARKERS.get(action.action, "") if action else ""
        title = c.title
        if c.merge_with:
            title = f"{title} [merge?]"
        if len(title) > title_w:
            title = title[: title_w - 1] + "…"
        sig = (c.signature or "")[:8]
        conflict_col = (c.conflict_classification or "-").upper()[:12]
        print(
            f"{idx:>3} | {marker:<3} | {c.type:<12} | "
            f"{title:<{title_w}} | {c.confidence:<4.1f} | {conflict_col:<12} | {sig:<8}"
        )


def _render_side_by_side(candidate: InboxCandidate) -> None:
    """Print a side-by-side conflict diff for a candidate with merge_with + conflict metadata.

    Reads the target note's ## Statement section (fallback: first body paragraph).
    Unreadable target falls back to plain body print with a warning.
    """
    config = get_config()
    verdict = (
        f"verdict: {candidate.conflict_classification or '?'}"
        f" (confidence {candidate.conflict_confidence or '?'})"
    )
    if candidate.conflict_reason:
        verdict += f" — {candidate.conflict_reason}"

    target_statement: str | None = None
    try:
        target_path = resolve_path(
            candidate.merge_with,
            ledger_root=config.ledger_root,
            ledger_notes_dir=config.ledger_notes_dir,
        )
        target_text = target_path.read_text(encoding="utf-8")
        # Extract ## Statement section
        in_statement = False
        stmt_lines: list[str] = []
        for raw_line in target_text.splitlines():
            if raw_line.strip().startswith("## Statement"):
                in_statement = True
                continue
            if in_statement:
                if raw_line.startswith("## ") or raw_line.startswith("---"):
                    break
                stmt_lines.append(raw_line)
        if stmt_lines:
            target_statement = "\n".join(stmt_lines).strip()
        if not target_statement:
            # Fallback: first non-empty paragraph of body
            parts = target_text.split("---", 2)
            body_text = parts[2] if len(parts) >= 3 else target_text
            for para in body_text.split("\n\n"):
                stripped = para.strip()
                if stripped and not stripped.startswith("#"):
                    target_statement = stripped
                    break
    except Exception as exc:
        print(f"warning: could not read merge target ({exc}); showing candidate body only")
        print(candidate.body.strip())
        return

    # Extract candidate's ## Statement section similarly
    candidate_statement: str | None = None
    in_statement = False
    stmt_lines = []
    for raw_line in candidate.body.splitlines():
        if raw_line.strip().startswith("## Statement"):
            in_statement = True
            continue
        if in_statement:
            if raw_line.startswith("## ") or raw_line.startswith("---"):
                break
            stmt_lines.append(raw_line)
    if stmt_lines:
        candidate_statement = "\n".join(stmt_lines).strip()
    if not candidate_statement:
        candidate_statement = candidate.body.strip()

    print(f"\n--- existing ({candidate.merge_with}) ---")
    print(target_statement or "(no statement found)")
    print(f"\n--- candidate ({candidate.filename}) ---")
    print(candidate_statement or "(no statement found)")
    print(f"\n{verdict}")


def _handle_inspect(line: str, candidates: list[InboxCandidate]) -> None:
    """Print candidate body; shows side-by-side for conflict rows (E8)."""
    parts = line.split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    try:
        rows = _parse_range(arg, len(candidates))
    except ValueError as exc:
        print(f"error: {exc}")
        return
    for row in rows:
        candidate = candidates[row - 1]
        print(f"\n--- #{row}: {candidate.filename} ---")
        if candidate.merge_with and candidate.conflict_classification:
            _render_side_by_side(candidate)
        else:
            print(candidate.body.strip())


def _handle_unset(
    line: str,
    candidates: list[InboxCandidate],
    actions: dict[int, TriageAction],
) -> None:
    parts = line.split(None, 1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    try:
        rows = _parse_range(arg, len(candidates))
    except ValueError as exc:
        print(f"error: {exc}")
        return
    for row in rows:
        actions.pop(row, None)


# Order the `t` key cycles target types through.
_TYPE_CYCLE = ["facts", "concepts", "preferences", "goals", "loops", "identity"]

_BANNER = (
    "SUBTRACTION MODE — every note is ACCEPTED by default. "
    "Pick the few to EXCLUDE, then press [b]A[/b] to accept the rest. "
    "Do nothing + [b]A[/b] = accept ALL."
)


def _suggested_types(candidates: list[InboxCandidate], notes_dir: Path | None) -> dict[str, str]:
    """Map filename -> classifier-suggested type (falls back to candidate.type)."""
    out: dict[str, str] = {}
    try:
        for s in triage_suggestions(notes_dir):
            out[s["filename"]] = s.get("suggested_type", "facts")
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# Textual app
# ---------------------------------------------------------------------------

def _build_textual():
    """Import textual lazily so the module imports even if textual is absent."""
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, VerticalScroll
    from textual.coordinate import Coordinate
    from textual.screen import ModalScreen
    from textual.widgets import DataTable, Footer, Static

    class ConfirmScreen(ModalScreen[bool]):
        """Yes/No modal shown before applying."""

        BINDINGS = [
            Binding("y", "yes", "Yes"),
            Binding("n,escape,q", "no", "No"),
        ]

        def __init__(self, message: str) -> None:
            super().__init__()
            self._message = message

        def compose(self) -> ComposeResult:
            yield Static(
                self._message + "\n\n[b]y[/b] = proceed    [b]n/Esc[/b] = back",
                id="confirm-box",
            )

        def action_yes(self) -> None:
            self.dismiss(True)

        def action_no(self) -> None:
            self.dismiss(False)

    class TriageApp(App):
        CSS = """
        #banner {
            background: $warning;
            color: black;
            text-style: bold;
            padding: 0 1;
            height: auto;
        }
        #table { width: 45%; border-right: solid $accent; }
        #preview-wrap { width: 55%; padding: 0 1; }
        #preview { height: auto; }
        #confirm-box {
            padding: 1 2;
            border: thick $warning;
            background: $surface;
            width: auto;
        }
        ConfirmScreen { align: center middle; }
        """

        BINDINGS = [
            Binding("space", "toggle", "Exclude / keep"),
            Binding("t", "cycle_type", "Change type"),
            Binding("A", "commit", "Accept & apply"),
            Binding("j", "cursor_down", "Down", show=False),
            Binding("k", "cursor_up", "Up", show=False),
            Binding("q,escape", "cancel", "Quit (no changes)"),
        ]

        def __init__(self, candidates, notes_dir, suggested):
            super().__init__()
            self.candidates = candidates
            self.notes_dir = notes_dir
            # default target type = classifier suggestion, else candidate.type
            self.types: list[str] = [
                suggested.get(c.filename, c.type) for c in candidates
            ]
            self.excluded: set[int] = set()
            self.summary: dict | None = None

        def compose(self) -> ComposeResult:
            yield Static(_BANNER, id="banner")
            with Horizontal():
                yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="preview-wrap"):
                    yield Static(id="preview")
            yield Footer()

        def on_mount(self) -> None:
            t = self.query_one("#table", DataTable)
            t.add_column("✓", key="state", width=3)
            t.add_column("type", key="type", width=11)
            t.add_column("title", key="title")
            t.add_column("conf", key="conf", width=4)
            t.add_column("conflict", key="conflict", width=10)
            for i, c in enumerate(self.candidates):
                t.add_row(*self._row_cells(i), key=str(i))
            t.focus()
            self._update_preview(0)

        # -- row rendering -------------------------------------------------
        def _row_cells(self, i: int):
            c = self.candidates[i]
            kept = i not in self.excluded
            mark = "[green]✓[/]" if kept else "[red]✗[/]"
            typ = self.types[i]
            conflict = (c.conflict_classification or "-").upper()
            title = c.title if kept else f"[strike dim]{c.title}[/]"
            return (mark, typ, title, f"{c.confidence:.1f}", conflict)

        def _refresh_row(self, i: int) -> None:
            t = self.query_one("#table", DataTable)
            cells = self._row_cells(i)
            for col, val in zip(("state", "type", "title", "conf", "conflict"), cells):
                t.update_cell(str(i), col, val, update_width=False)

        def _update_preview(self, i: int) -> None:
            if not (0 <= i < len(self.candidates)):
                return
            c = self.candidates[i]
            state = "EXCLUDED — will be REJECTED" if i in self.excluded else "kept — will be accepted"
            header = (
                f"[b]{c.title}[/b]\n"
                f"[dim]{c.filename}[/]\n"
                f"target type: [b]{self.types[i]}[/]   ·   {state}\n"
                + "─" * 40 + "\n"
            )
            self.query_one("#preview", Static).update(header + c.body.strip())

        # -- events --------------------------------------------------------
        def on_data_table_row_highlighted(self, event) -> None:
            self._update_preview(event.cursor_row)

        # -- actions -------------------------------------------------------
        def _cursor(self) -> int:
            return self.query_one("#table", DataTable).cursor_row

        def action_cursor_down(self) -> None:
            self.query_one("#table", DataTable).action_cursor_down()

        def action_cursor_up(self) -> None:
            self.query_one("#table", DataTable).action_cursor_up()

        def action_toggle(self) -> None:
            i = self._cursor()
            if i in self.excluded:
                self.excluded.discard(i)
            else:
                self.excluded.add(i)
            self._refresh_row(i)
            self._update_preview(i)

        def action_cycle_type(self) -> None:
            i = self._cursor()
            cur = self.types[i]
            nxt = _TYPE_CYCLE[(_TYPE_CYCLE.index(cur) + 1) % len(_TYPE_CYCLE)] if cur in _TYPE_CYCLE else _TYPE_CYCLE[0]
            self.types[i] = nxt
            self._refresh_row(i)
            self._update_preview(i)

        def build_actions(self) -> list[TriageAction]:
            """Pure: map current UI state to engine actions (1-based rows)."""
            actions = []
            for i in range(len(self.candidates)):
                if i in self.excluded:
                    actions.append(TriageAction(row=i + 1, action="reject"))
                else:
                    actions.append(
                        TriageAction(row=i + 1, action="accept", target_type=self.types[i])
                    )
            return actions

        def action_commit(self) -> None:
            n_reject = len(self.excluded)
            n_accept = len(self.candidates) - n_reject
            msg = (
                f"[b]Accept {n_accept}[/b] note(s) into their type folders.\n"
                f"[b red]Reject (DELETE) {n_reject}[/b red] excluded note(s)."
            )

            def _after(ok: bool | None) -> None:
                if ok:
                    self.summary = apply_actions(
                        self.candidates, self.build_actions(), notes_dir=self.notes_dir
                    )
                    self.exit(0)

            self.push_screen(ConfirmScreen(msg), _after)

        def action_cancel(self) -> None:
            self.summary = None
            self.exit(0)

    return TriageApp


def run_interactive_triage(notes_dir: Path | None = None) -> int:
    """Launch the triage TUI. Falls back to printing suggestions when there is
    no interactive terminal (pipes, CI, headless)."""
    candidates = load_candidates_for_triage(notes_dir)
    if not candidates:
        print("Inbox is empty.")
        return 0

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("Not a TTY — printing suggestions instead (use a terminal for the triage UI):")
        for c in candidates:
            print(f"  {c.filename} -> {c.type}")
        return 0

    suggested = _suggested_types(candidates, notes_dir)
    TriageApp = _build_textual()
    app = TriageApp(candidates, notes_dir, suggested)
    app.run()

    summary = app.summary
    if summary is None:
        print("Cancelled — no changes made.")
        return 0
    print(
        f"Applied: accepted {summary['accepted']}, rejected {summary['rejected']}, "
        f"failed {summary['failed']}."
    )
    for err in summary.get("errors", []):
        print(f"  ! {err}")
    print("Run `ledger sleep index` to refresh the tier-2 index.")
    return 0

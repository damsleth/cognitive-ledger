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
"""
from __future__ import annotations

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

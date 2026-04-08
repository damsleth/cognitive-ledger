"""Main screen with 3-panel layout."""

import asyncio
import re
import subprocess
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Footer,
    Header,
    Input,
    LoadingIndicator,
    Markdown,
    OptionList,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from ..models.enums import NoteType, Scope
from ..models.note import Note
from ..services.note_store import NoteStore
from ..services.sheep_runner import SheepRunner
from ..widgets.graph_panel import GraphPanel
from ..widgets.lint_panel import LintPanel
from ..widgets.note_tree import NoteTree


class MainScreen(Screen):
    """Main 3-panel screen: sidebar, note view, metadata."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("?", "show_help", "Help"),
        Binding("/", "focus_filter", "Filter"),
        Binding("e", "edit_external", "Edit"),
        Binding("t", "edit_tags", "Tags"),
        Binding("f", "quick_fix", "Fix"),
        Binding("g", "toggle_graph", "Graph"),
        Binding("ctrl+l", "run_lint", "Lint"),
        Binding("ctrl+s", "run_status", "Status"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("1", "filter_type_1", "Facts", show=False),
        Binding("2", "filter_type_2", "Prefs", show=False),
        Binding("3", "filter_type_3", "Goals", show=False),
        Binding("4", "filter_type_4", "Loops", show=False),
        Binding("5", "filter_type_5", "Concepts", show=False),
        Binding("0", "filter_clear", "All", show=False),
    ]

    def __init__(self, store: NoteStore, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.current_note: Note | None = None
        self.show_graph = True
        self._lint_running = False
        self._query_running = False
        self._query_result_paths: dict[str, Path] = {}
        self._watch_snapshot: dict[Path, float] = {}
        self._watch_timer = None
        self._watch_interval_seconds = 2.0

    def compose(self) -> ComposeResult:
        yield Header()

        with Horizontal(id="main-container"):
            # Sidebar
            with Vertical(id="sidebar"):
                yield Input(placeholder="Filter...", id="filter-input")
                yield Input(placeholder="Query... (press Enter)", id="query-input")
                yield Select(
                    options=[
                        ("All Scopes", "all"),
                        ("Home", "home"),
                        ("Work", "work"),
                        ("Dev", "dev"),
                        ("Personal", "personal"),
                        ("Meta", "meta"),
                    ],
                    value="all",
                    id="scope-select",
                )
                yield Select(
                    options=[
                        ("Updated (Newest)", "updated_desc"),
                        ("Updated (Oldest)", "updated_asc"),
                        ("Alphabetical (A-Z)", "alpha_asc"),
                        ("Alphabetical (Z-A)", "alpha_desc"),
                    ],
                    value="updated_desc",
                    id="sort-select",
                )
                yield Select(
                    options=[("All Tags", "all")],
                    value="all",
                    id="tag-select",
                )
                with Horizontal(id="task-status"):
                    yield LoadingIndicator(id="task-spinner")
                    yield Static("", id="task-status-text")
                yield Static("[dim]Query Results[/dim]", id="query-results-label")
                yield OptionList(id="query-results")
                yield NoteTree(self.store, id="note-tree")

            # Note view
            with Vertical(id="note-view"):
                yield Markdown(id="note-markdown")

            # Metadata panel
            with Vertical(id="metadata"):
                yield Static(id="frontmatter-panel")
                yield GraphPanel(self.store, id="graph-panel")
                yield LintPanel(id="lint-panel")

        yield Footer()

    def on_mount(self) -> None:
        """Load notes on mount."""
        self.store.load_all()
        self._watch_snapshot = self.store.filesystem_snapshot()
        self._populate_tag_filter("all")
        self._set_task_status(None)
        self._set_query_results_visibility(False)
        tree = self.query_one("#note-tree", NoteTree)
        tree.rebuild()
        self._watch_timer = self.set_interval(self._watch_interval_seconds, self._poll_file_changes)

    def on_unmount(self) -> None:
        """Stop watch timer when leaving screen."""
        if self._watch_timer is not None:
            self._watch_timer.stop()
            self._watch_timer = None

    def on_tree_node_selected(self, event) -> None:
        """Handle note selection in tree."""
        node = event.node
        if node.data and node.data.get("type") == "note":
            note = node.data["note"]
            self._show_note(note)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle query result selection."""
        if event.option_list.id != "query-results":
            return

        option_id = str(event.option.id)
        path = self._query_result_paths.get(option_id)
        if not path:
            return

        note = self.store.get_note(path) or self.store.get_note(path.resolve()) or self.store.get_note_by_stem(path.stem)
        if note:
            self._show_note(note)
            tree = self.query_one("#note-tree", NoteTree)
            tree.select_note(note.path)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Handle filter input changes."""
        if event.input.id == "filter-input":
            tree = self.query_one("#note-tree", NoteTree)
            tree.set_filter_text(event.value)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submit events."""
        if event.input.id != "query-input":
            return

        query = event.value.strip()
        if not query:
            return

        await self._run_query(query)

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle scope, sort, and tag filter changes."""
        tree = self.query_one("#note-tree", NoteTree)
        value = event.value if isinstance(event.value, str) else ""

        if event.select.id == "scope-select":
            if value == "all":
                tree.set_filter_scope(None)
            elif value:
                tree.set_filter_scope(Scope(value))
            return

        if event.select.id == "sort-select":
            tree.set_sort_mode(value or "updated_desc")
            return

        if event.select.id == "tag-select":
            if value == "all" or not value:
                tree.set_filter_tag(None)
            else:
                tree.set_filter_tag(value)

    def on_graph_panel_link_selected(self, event: GraphPanel.LinkSelected) -> None:
        """Handle link navigation from graph."""
        note = self.store.get_note_by_stem(event.target)
        if note:
            self._show_note(note)
            tree = self.query_one("#note-tree", NoteTree)
            tree.select_note(note.path)

    def _show_note(self, note: Note) -> None:
        """Display a note in the view panels."""
        self.current_note = note

        # Update markdown view
        markdown = self.query_one("#note-markdown", Markdown)
        markdown.update(note.body)

        # Update frontmatter panel
        fm_panel = self.query_one("#frontmatter-panel", Static)
        fm_panel.update(self._render_frontmatter(note))

        # Update graph panel
        graph = self.query_one("#graph-panel", GraphPanel)
        graph.current_note = note

        # Update lint panel
        lint = self.query_one("#lint-panel", LintPanel)
        lint.current_note = note

    def _show_query_summary(
        self,
        query: str,
        command_exit_code: int,
        parsed_hits: list[tuple[Path, float | None, str]],
        raw_output: str,
    ) -> None:
        """Render query output in note view + metadata panel."""
        self.current_note = None

        markdown = self.query_one("#note-markdown", Markdown)
        markdown.update(
            self._render_query_markdown(
                query=query,
                command_exit_code=command_exit_code,
                parsed_hits=parsed_hits,
                raw_output=raw_output,
            )
        )

        fm_panel = self.query_one("#frontmatter-panel", Static)
        fm_panel.update(
            "\n".join(
                [
                    "[bold]QUERY[/bold]",
                    "",
                    f"[dim]text:[/dim]      {query}",
                    f"[dim]exit_code:[/dim] {command_exit_code}",
                    f"[dim]matches:[/dim]   {len(parsed_hits)}",
                ]
            )
        )

        graph = self.query_one("#graph-panel", GraphPanel)
        graph.current_note = None

        lint = self.query_one("#lint-panel", LintPanel)
        lint.current_note = None

    def _render_query_markdown(
        self,
        query: str,
        command_exit_code: int,
        parsed_hits: list[tuple[Path, float | None, str]],
        raw_output: str,
    ) -> str:
        """Build a readable markdown summary for query output."""
        lines = [
            "# Query Results",
            "",
            f"- Query: `{query}`",
            f"- Command exit code: `{command_exit_code}`",
            f"- Parsed matches: `{len(parsed_hits)}`",
        ]

        if parsed_hits:
            lines.extend(["", "## Matches"])
            for idx, (path, score, note_type_label) in enumerate(parsed_hits, start=1):
                note = self.store.get_note(path) or self.store.get_note_by_stem(path.stem)
                display_name = note.display_name if note else path.stem.replace("_", " ")
                note_type = note.note_type.value if note else (note_type_label or "note")
                score_text = f"{score:.3f}" if score is not None else "n/a"
                rel_path = self._to_relative_path(path)
                lines.append(f"{idx}. **{display_name}** (`{note_type}`, score `{score_text}`)")
                lines.append(f"   `{rel_path}`")
        else:
            lines.extend(["", "No note matches could be parsed from command output."])

        cleaned_output = self._clean_codex_output(raw_output)
        if cleaned_output:
            output_lines = cleaned_output.splitlines()
            lines.extend(["", "## Command Output", "```text", *output_lines[:100]])
            if len(output_lines) > 100:
                lines.append("... (truncated)")
            lines.append("```")

        return "\n".join(lines)

    def _render_frontmatter(self, note: Note) -> str:
        """Render frontmatter as formatted text."""
        fm = note.frontmatter
        lines = [
            "[bold]FRONTMATTER[/bold]",
            "",
            f"[dim]created:[/dim]  {fm.created.strftime('%Y-%m-%d')}",
            f"[dim]updated:[/dim]  {fm.updated.strftime('%Y-%m-%d')}",
            f"[dim]tags:[/dim]     {', '.join(fm.tags) or 'none'}",
            f"[dim]confidence:[/dim] {fm.confidence:.1f}",
            f"[dim]source:[/dim]   {fm.source.value}",
            f"[dim]scope:[/dim]    {fm.scope.value}",
            f"[dim]lang:[/dim]     {fm.lang}",
        ]
        if fm.status:
            lines.append(f"[dim]status:[/dim]   {fm.status.value}")
        return "\n".join(lines)

    def _populate_tag_filter(self, selected_tag: str) -> None:
        """Populate sidebar tag filter with all known tags."""
        tag_select = self.query_one("#tag-select", Select)
        tags = self.store.all_tags()
        options = [("All Tags", "all"), *[(tag, tag) for tag in tags]]
        tag_select.set_options(options)

        if selected_tag not in {"all", *tags}:
            selected_tag = "all"
        tag_select.value = selected_tag

    def _current_tag_filter(self) -> str:
        """Get currently selected tag filter value."""
        value = self.query_one("#tag-select", Select).value
        return value if isinstance(value, str) else "all"

    def _set_query_results_visibility(self, visible: bool) -> None:
        """Show/hide query results list controls."""
        label = self.query_one("#query-results-label", Static)
        results = self.query_one("#query-results", OptionList)
        label.display = visible
        results.display = visible

    def _set_task_status(self, message: str | None) -> None:
        """Show/hide running task indicator."""
        container = self.query_one("#task-status", Horizontal)
        label = self.query_one("#task-status-text", Static)
        if message:
            label.update(f"[dim]{message}[/dim]")
            container.display = True
        else:
            label.update("")
            container.display = False

    async def _run_query(self, query: str) -> None:
        """Run codex query command and show navigable results."""
        if self._lint_running:
            self.notify("Wait for lint to finish before running a query.", severity="warning")
            return
        if self._query_running:
            self.notify("Query already running.", severity="warning")
            return

        self._query_running = True
        self._set_task_status(f"Running query: {query}")
        command_exit_code = 1
        raw_output = ""

        try:
            command_exit_code, raw_output = await asyncio.to_thread(self._run_codex_query_command, query)
        except FileNotFoundError:
            try:
                hits = await asyncio.to_thread(self._run_direct_query, query)
            except Exception as exc:
                self.notify(f"Query failed: {exc}", severity="error")
                return
            self._populate_query_results(hits)
            if hits:
                self.notify(f"Query complete: {len(hits)} match(es) (direct).")
            else:
                self.notify("Query complete: no matches found.", severity="warning")
            return
        except subprocess.TimeoutExpired:
            self.notify("Query command timed out.", severity="error")
            return
        except Exception as exc:
            self.notify(f"Query failed: {exc}", severity="error")
            return
        else:
            parsed_hits = self._parse_json_hits(raw_output)
            self._populate_query_results(parsed_hits)
            self._show_query_summary(query, command_exit_code, parsed_hits, raw_output)

            if parsed_hits:
                self.notify(f"Query complete: {len(parsed_hits)} match(es).")
            elif command_exit_code == 0:
                self.notify("Query complete: no parsed matches found.", severity="warning")
            else:
                self.notify(f"Query failed (exit {command_exit_code}).", severity="error")
        finally:
            self._query_running = False
            self._set_task_status(None)

    def _run_codex_query_command(self, query: str) -> tuple[int, str]:
        """Run codex query command and return (exit_code, merged_output)."""
        import shlex

        prompt = (
            f"/notes query {shlex.quote(query)} -- "
            'Respond with ONLY a JSON array. Each element: {"path": "notes/...", '
            '"score": 0.0, "type": "...", "title": "..."}. No prose.'
        )
        command = [
            "codex",
            "-c",
            "mcp_servers.playwright.enabled=false",
            "e",
            prompt,
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            cwd=self.store.root_dir,
            timeout=240,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")

    def _parse_json_hits(self, output: str) -> list[tuple[Path, float | None, str]]:
        """Extract ranked note paths from JSON array in codex output."""
        import json

        results: list[tuple[Path, float | None, str]] = []
        seen: set[Path] = set()

        start = output.find("[")
        end = output.rfind("]") + 1
        if start == -1 or end == 0:
            return results

        try:
            items = json.loads(output[start:end])
        except json.JSONDecodeError:
            return results

        for item in items:
            if not isinstance(item, dict):
                continue
            path_str = item.get("path", "")
            if not path_str or not path_str.startswith("notes/"):
                continue
            full_path = (self.store.root_dir / path_str).resolve()
            if full_path in seen:
                continue
            score = None
            try:
                score = float(item.get("score", 0))
            except (TypeError, ValueError):
                pass
            seen.add(full_path)
            results.append((full_path, score, item.get("type", "")))

        return results

    def _run_direct_query(self, query: str) -> list[tuple[Path, float | None, str]]:
        """Direct rank_query_lexical fallback when codex is not on PATH."""
        from ledger.query import rank_query_lexical

        result = rank_query_lexical(query=query, scope="all", limit=8)
        hits: list[tuple[Path, float | None, str]] = []
        for item in result.results:
            path = Path(item.path)
            if not path.is_absolute():
                path = (self.store.root_dir / path).resolve()
            hits.append((path, item.score, item.type))
        return hits

    def _populate_query_results(self, parsed_hits: list[tuple[Path, float | None, str]]) -> None:
        """Populate sidebar query result options."""
        results = self.query_one("#query-results", OptionList)
        self._query_result_paths.clear()
        results.clear_options()

        if not parsed_hits:
            self._set_query_results_visibility(False)
            return

        for idx, (path, score, _note_type_label) in enumerate(parsed_hits, start=1):
            note = self.store.get_note(path) or self.store.get_note(path.resolve()) or self.store.get_note_by_stem(path.stem)
            display_name = note.display_name if note else path.stem.replace("_", " ")
            if len(display_name) > 44:
                display_name = display_name[:41] + "..."
            score_text = f"{score:.3f}" if score is not None else "n/a"
            option_id = f"query-{idx}"
            results.add_option(Option(f"{idx}. {display_name} ({score_text})", id=option_id))
            self._query_result_paths[option_id] = note.path if note else path

        self._set_query_results_visibility(True)

    def _clean_codex_output(self, output: str) -> str:
        """Remove ANSI and trailing codex metadata noise."""
        clean = re.sub(r"\x1b\[[0-9;]*m", "", output)
        lines = clean.splitlines()

        trimmed: list[str] = []
        for line in lines:
            if line.startswith("OpenAI Codex "):
                break
            trimmed.append(line)

        collapsed = "\n".join(trimmed).strip()
        return collapsed or clean.strip()

    def _to_relative_path(self, path: Path) -> str:
        """Render path relative to ledger root where possible."""
        try:
            return path.resolve().relative_to(self.store.root_dir.resolve()).as_posix()
        except ValueError:
            return str(path)

    # Actions
    def action_focus_filter(self) -> None:
        """Focus the filter input."""
        self.query_one("#filter-input", Input).focus()

    def action_edit_external(self) -> None:
        """Open current note in $EDITOR."""
        if self.current_note:
            import os

            editor = os.environ.get("EDITOR", "vim")
            subprocess.run([editor, str(self.current_note.path)])
            # Refresh after edit
            selected_tag = self._current_tag_filter()
            self.store.refresh()
            self._watch_snapshot = self.store.filesystem_snapshot()
            self._populate_tag_filter(selected_tag)
            tree = self.query_one("#note-tree", NoteTree)
            tree.rebuild()
            refreshed = self.store.get_note(self.current_note.path)
            if refreshed:
                self._show_note(refreshed)

    def action_toggle_graph(self) -> None:
        """Toggle graph panel visibility."""
        graph = self.query_one("#graph-panel", GraphPanel)
        self.show_graph = not self.show_graph
        graph.display = self.show_graph

    def action_refresh(self) -> None:
        """Refresh notes from disk."""
        selected_tag = self._current_tag_filter()
        self.store.refresh()
        self._watch_snapshot = self.store.filesystem_snapshot()
        self._populate_tag_filter(selected_tag)
        tree = self.query_one("#note-tree", NoteTree)
        tree.rebuild()
        if self.current_note:
            refreshed = self.store.get_note(self.current_note.path)
            if refreshed:
                self._show_note(refreshed)

    async def _poll_file_changes(self) -> None:
        """Poll note files and auto-refresh when disk state changes."""
        import asyncio

        if self._query_running or self._lint_running:
            return

        latest_snapshot = await asyncio.to_thread(self.store.filesystem_snapshot)
        if latest_snapshot == self._watch_snapshot:
            return

        self._watch_snapshot = latest_snapshot
        selected_tag = self._current_tag_filter()
        current_path = self.current_note.path if self.current_note else None
        await asyncio.to_thread(self.store.refresh)
        self._populate_tag_filter(selected_tag)
        tree = self.query_one("#note-tree", NoteTree)
        tree.rebuild()
        if current_path is not None:
            refreshed = self.store.get_note(current_path)
            if refreshed:
                self._show_note(refreshed)
        self.notify("Notes updated on disk. Refreshed.")

    def action_filter_type_1(self) -> None:
        self._set_type_filter(NoteType.FACT)

    def action_filter_type_2(self) -> None:
        self._set_type_filter(NoteType.PREF)

    def action_filter_type_3(self) -> None:
        self._set_type_filter(NoteType.GOAL)

    def action_filter_type_4(self) -> None:
        self._set_type_filter(NoteType.LOOP)

    def action_filter_type_5(self) -> None:
        self._set_type_filter(NoteType.CONCEPT)

    def action_filter_clear(self) -> None:
        self._set_type_filter(None)

    def _set_type_filter(self, note_type: NoteType | None) -> None:
        """Set type filter on tree."""
        tree = self.query_one("#note-tree", NoteTree)
        tree.set_filter_type(note_type)

    def action_quick_fix(self) -> None:
        """Open quick fix modal."""
        if self.current_note:
            from .quick_fix import QuickFixScreen

            self.app.push_screen(QuickFixScreen(self.current_note, self.store))

    def action_edit_tags(self) -> None:
        """Open tag editor modal."""
        if self.current_note:
            from .tag_editor import TagEditorScreen

            self.app.push_screen(TagEditorScreen(self.current_note, self.store))

    async def action_run_lint(self) -> None:
        """Run sheep lint with visible progress."""
        if self._query_running:
            self.notify("Wait for query to finish before lint.", severity="warning")
            return
        if self._lint_running:
            self.notify("Lint already running.", severity="warning")
            return

        self._lint_running = True
        self._set_task_status("Running sheep lint...")
        runner = SheepRunner(self.store.root_dir)

        try:
            exit_code, output = await asyncio.to_thread(runner.lint)
            if exit_code:
                self.notify(f"Lint complete: {exit_code} issues", severity="warning")
            else:
                self.notify("Lint complete: OK")
        except Exception as exc:
            self.notify(f"Lint failed: {exc}", severity="error")
        finally:
            self._lint_running = False
            self._set_task_status(None)

    async def action_run_status(self) -> None:
        """Run sheep status."""
        if self._query_running or self._lint_running:
            self.notify("Wait for running task to finish first.", severity="warning")
            return

        self._set_task_status("Running sheep status...")
        runner = SheepRunner(self.store.root_dir)
        try:
            output = await asyncio.to_thread(runner.status)
            self.notify(output.strip() or "Status complete")
        except Exception as exc:
            self.notify(f"Status failed: {exc}", severity="error")
        finally:
            self._set_task_status(None)

    def action_show_help(self) -> None:
        """Show help."""
        self.notify(
            "Keys: 1-5 type filter, 0 all, / filter, Enter on Query runs retrieval, e edit, f fix, t tags, g graph"
        )

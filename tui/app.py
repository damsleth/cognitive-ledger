"""Main Textual application for the Cognitive Ledger TUI."""

from pathlib import Path

from textual.app import App

from .services.note_store import NoteStore
from .screens.main_screen import MainScreen


class LedgerApp(App):
    """Cognitive Ledger TUI application."""

    TITLE = "Cognitive Ledger"
    CSS = """
    /* Main layout */
    #main-container {
        height: 1fr;
    }

    /* Sidebar */
    #sidebar {
        width: 25%;
        min-width: 30;
        border-right: solid $primary;
        padding: 1;
    }

    #filter-input {
        margin-bottom: 1;
    }

    #query-input {
        margin-bottom: 1;
    }

    #scope-select {
        margin-bottom: 1;
    }

    #sort-select {
        margin-bottom: 1;
    }

    #tag-select {
        margin-bottom: 1;
    }

    #task-status {
        height: 1;
        margin-bottom: 1;
        display: none;
    }

    #task-spinner {
        width: 2;
    }

    #task-status-text {
        margin-left: 1;
    }

    #query-results-label {
        display: none;
        margin-bottom: 1;
    }

    #query-results {
        display: none;
        height: 7;
        margin-bottom: 1;
    }

    #note-tree {
        height: 1fr;
    }

    /* Note view */
    #note-view {
        width: 50%;
        padding: 1 2;
    }

    #note-markdown {
        height: 1fr;
    }

    /* Metadata panel */
    #metadata {
        width: 25%;
        min-width: 30;
        border-left: solid $primary;
        padding: 1;
    }

    #frontmatter-panel {
        margin-bottom: 1;
    }

    #graph-panel {
        margin-bottom: 1;
        border-top: solid $primary-darken-1;
        padding-top: 1;
    }

    #lint-panel {
        border-top: solid $warning;
        padding-top: 1;
    }

    /* Quick fix modal */
    #quickfix-container {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #quickfix-container #buttons {
        margin-top: 1;
    }

    #quickfix-container Button {
        margin-right: 1;
    }

    /* Tag editor modal */
    #tag-editor-container {
        width: 60;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }

    #tag-editor-container OptionList {
        height: 8;
    }

    #tag-editor-container #buttons {
        margin-top: 1;
    }

    #tag-editor-container Button {
        margin-right: 1;
    }
    """

    def __init__(self, root_dir: Path):
        super().__init__()
        self.root_dir = root_dir
        self.store = NoteStore(root_dir)

    def on_mount(self) -> None:
        """Push the main screen."""
        self.push_screen(MainScreen(self.store))

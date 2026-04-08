"""Tag editor modal."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static, Input, OptionList
from textual.widgets.option_list import Option

from ..models.note import Note
from ..services.note_store import NoteStore
from ..services.note_writer import NoteWriter


class TagEditorScreen(ModalScreen):
    """Modal for managing note tags."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, note: Note, store: NoteStore, **kwargs):
        super().__init__(**kwargs)
        self.note = note
        self.store = store
        self.current_tags = list(note.frontmatter.tags)

    def compose(self) -> ComposeResult:
        with Vertical(id="tag-editor-container"):
            yield Static(f"[bold]Tags: {self.note.path.name}[/bold]", id="title")
            yield Static("")

            yield Static("[dim]Current tags (select to remove):[/dim]")
            yield OptionList(
                *[Option(tag, id=tag) for tag in self.current_tags],
                id="current-tags",
            )

            yield Static("")
            yield Static("[dim]Add new tag:[/dim]")
            yield Input(placeholder="tag-name", id="new-tag-input")

            yield Static("")
            yield Static("[dim]Available tags:[/dim]")
            available = [t for t in self.store.all_tags() if t not in self.current_tags]
            yield OptionList(
                *[Option(tag, id=tag) for tag in available[:20]],
                id="available-tags",
            )

            yield Static("")
            with Horizontal(id="buttons"):
                yield Button("Save", variant="primary", id="save-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle tag selection."""
        tag = str(event.option.id)

        if event.option_list.id == "current-tags":
            # Remove tag
            if tag in self.current_tags:
                self.current_tags.remove(tag)
                self._refresh_lists()
        elif event.option_list.id == "available-tags":
            # Add tag
            if tag not in self.current_tags:
                self.current_tags.append(tag)
                self._refresh_lists()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle new tag input."""
        if event.input.id == "new-tag-input":
            tag = event.value.strip().lower()
            # Validate tag format
            if tag and tag not in self.current_tags:
                import re

                if re.match(r"^[a-z][a-z0-9_-]*$", tag):
                    self.current_tags.append(tag)
                    event.input.value = ""
                    self._refresh_lists()
                else:
                    self.notify("Invalid tag format (lowercase, alphanumeric, hyphens)", severity="error")

    def _refresh_lists(self) -> None:
        """Refresh the tag option lists."""
        current_list = self.query_one("#current-tags", OptionList)
        current_list.clear_options()
        for tag in self.current_tags:
            current_list.add_option(Option(tag, id=tag))

        available_list = self.query_one("#available-tags", OptionList)
        available_list.clear_options()
        available = [t for t in self.store.all_tags() if t not in self.current_tags]
        for tag in available[:20]:
            available_list.add_option(Option(tag, id=tag))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "save-btn":
            self._save_tags()
        else:
            self.dismiss()

    def action_cancel(self) -> None:
        """Cancel and close."""
        self.dismiss()

    def _save_tags(self) -> None:
        """Save tags and close."""
        # Only save if changed
        if set(self.current_tags) != set(self.note.frontmatter.tags):
            writer = NoteWriter(self.store.root_dir, self.store.notes_dir)
            writer.update_frontmatter(self.note, {"tags": self.current_tags})
            self.store.refresh()
            self.notify(f"Updated tags: {', '.join(self.current_tags)}")

        self.dismiss()

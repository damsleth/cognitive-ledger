"""Quick fix modal for editing frontmatter fields."""

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static, Select, Input, Label

from ..models.note import Note
from ..models.enums import Source, Scope, LoopStatus
from ..services.note_store import NoteStore
from ..services.note_writer import NoteWriter


class QuickFixScreen(ModalScreen):
    """Modal for editing frontmatter fields."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("enter", "apply", "Apply"),
    ]

    def __init__(self, note: Note, store: NoteStore, **kwargs):
        super().__init__(**kwargs)
        self.note = note
        self.store = store
        self.selected_field: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="quickfix-container"):
            yield Static(f"[bold]Quick Fix: {self.note.path.name}[/bold]", id="title")
            yield Static("")

            yield Label("Select field to edit:")
            yield Select(
                options=[
                    ("Confidence", "confidence"),
                    ("Source", "source"),
                    ("Scope", "scope"),
                    ("Status (loops)", "status"),
                ],
                id="field-select",
            )

            yield Static("", id="current-value")
            yield Vertical(id="value-container")

            yield Static("")
            with Horizontal(id="buttons"):
                yield Button("Apply", variant="primary", id="apply-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        """Focus the field select."""
        self.query_one("#field-select", Select).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        """Update value input based on selected field."""
        if event.select.id != "field-select":
            return

        self.selected_field = event.value
        container = self.query_one("#value-container", Vertical)
        container.remove_children()

        fm = self.note.frontmatter
        current = self.query_one("#current-value", Static)

        if self.selected_field == "confidence":
            current.update(f"[dim]Current: {fm.confidence}[/dim]")
            container.mount(
                Input(
                    value=str(fm.confidence),
                    placeholder="0.0 - 1.0",
                    id="value-input",
                )
            )
        elif self.selected_field == "source":
            current.update(f"[dim]Current: {fm.source.value}[/dim]")
            container.mount(
                Select(
                    options=[(s.value, s.value) for s in Source],
                    value=fm.source.value,
                    id="value-select",
                )
            )
        elif self.selected_field == "scope":
            current.update(f"[dim]Current: {fm.scope.value}[/dim]")
            container.mount(
                Select(
                    options=[(s.value, s.value) for s in Scope if s != Scope.LIFE],
                    value=fm.scope.value,
                    id="value-select",
                )
            )
        elif self.selected_field == "status":
            if fm.status:
                current.update(f"[dim]Current: {fm.status.value}[/dim]")
                container.mount(
                    Select(
                        options=[(s.value, s.value) for s in LoopStatus],
                        value=fm.status.value,
                        id="value-select",
                    )
                )
            else:
                current.update("[dim]Not a loop[/dim]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "apply-btn":
            self._apply_change()
        else:
            self.dismiss()

    def action_cancel(self) -> None:
        """Cancel and close."""
        self.dismiss()

    def action_apply(self) -> None:
        """Apply changes."""
        self._apply_change()

    def _apply_change(self) -> None:
        """Apply the change and close."""
        if not self.selected_field:
            self.dismiss()
            return

        # Get the new value
        new_value = None

        try:
            if self.selected_field == "confidence":
                input_widget = self.query_one("#value-input", Input)
                new_value = float(input_widget.value)
                if not 0.0 <= new_value <= 1.0:
                    self.notify("Confidence must be between 0.0 and 1.0", severity="error")
                    return
            elif self.selected_field in ("source", "scope", "status"):
                select_widget = self.query_one("#value-select", Select)
                if self.selected_field == "source":
                    new_value = Source(select_widget.value)
                elif self.selected_field == "scope":
                    new_value = Scope(select_widget.value)
                elif self.selected_field == "status":
                    new_value = LoopStatus(select_widget.value)
        except Exception as e:
            self.notify(f"Invalid value: {e}", severity="error")
            return

        if new_value is not None:
            # Write the change
            writer = NoteWriter(self.store.root_dir)
            writer.update_frontmatter(self.note, {self.selected_field: new_value})

            # Refresh store
            self.store.refresh()

            self.notify(f"Updated {self.selected_field}")

        self.dismiss()

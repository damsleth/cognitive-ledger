"""ASCII graph panel for visualizing note connections."""

from textual.widgets import Static
from textual.reactive import reactive
from textual.message import Message

from ..models.note import Note
from ..services.note_store import NoteStore


class GraphPanel(Static):
    """Displays ASCII graph of note connections."""

    class LinkSelected(Message):
        """Emitted when a link is selected for navigation."""

        def __init__(self, target: str) -> None:
            super().__init__()
            self.target = target

    current_note: reactive[Note | None] = reactive(None)
    selected_index: reactive[int] = reactive(0)

    def __init__(self, store: NoteStore, **kwargs):
        super().__init__("", **kwargs)
        self.store = store
        self.links: list[tuple[str, str]] = []  # (direction, target)

    def watch_current_note(self, note: Note | None) -> None:
        """Rebuild graph when note changes."""
        self.links = []
        self.selected_index = 0

        if note is None:
            self.update("")
            return

        # Build link list
        for link in note.outgoing_links:
            self.links.append(("→", link.target))

        for path in note.incoming_links:
            self.links.append(("←", path.stem))

        self.update(self._build_content())

    def watch_selected_index(self, index: int) -> None:
        """Re-render when selection changes."""
        if self.links:
            self.update(self._build_content())

    def _build_content(self) -> str:
        """Render ASCII graph."""
        lines = ["[bold]CONNECTIONS[/bold]", ""]

        if not self.links:
            lines.append("[dim]No connections[/dim]")
            return "\n".join(lines)

        outgoing = [(i, t) for i, (d, t) in enumerate(self.links) if d == "→"]
        incoming = [(i, t) for i, (d, t) in enumerate(self.links) if d == "←"]

        if outgoing:
            lines.append(f"[dim]Outgoing ({len(outgoing)})[/dim]")
            for idx, target in outgoing:
                prefix = ">" if idx == self.selected_index else " "
                if idx == self.selected_index:
                    lines.append(f" {prefix} [bold]→ {target}[/bold]")
                else:
                    lines.append(f" {prefix} → {target}")
            lines.append("")

        if incoming:
            lines.append(f"[dim]Incoming ({len(incoming)})[/dim]")
            for idx, target in incoming:
                prefix = ">" if idx == self.selected_index else " "
                if idx == self.selected_index:
                    lines.append(f" {prefix} [bold]← {target}[/bold]")
                else:
                    lines.append(f" {prefix} ← {target}")

        return "\n".join(lines)

    def action_cursor_down(self) -> None:
        """Move selection down."""
        if self.links:
            self.selected_index = (self.selected_index + 1) % len(self.links)

    def action_cursor_up(self) -> None:
        """Move selection up."""
        if self.links:
            self.selected_index = (self.selected_index - 1) % len(self.links)

    def action_select(self) -> None:
        """Navigate to selected link."""
        if self.links and 0 <= self.selected_index < len(self.links):
            _, target = self.links[self.selected_index]
            self.post_message(self.LinkSelected(target))

"""Tree widget for browsing notes."""

from pathlib import Path

from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from ..models.enums import NoteType, Scope
from ..models.note import Note
from ..services.note_store import NoteStore


class NoteTree(Tree):
    """Tree view for browsing notes by type/scope."""

    def __init__(
        self,
        store: NoteStore,
        filter_type: NoteType | None = None,
        filter_scope: Scope | None = None,
        filter_tag: str | None = None,
        filter_text: str = "",
        sort_mode: str = "updated_desc",
        **kwargs,
    ):
        super().__init__("Notes", **kwargs)
        self.show_root = False
        self.store = store
        self.filter_type = filter_type
        self.filter_scope = filter_scope
        self.filter_tag = filter_tag
        self.filter_text = filter_text.lower()
        self.sort_mode = sort_mode
        self._note_nodes: dict[Path, TreeNode] = {}

    def rebuild(self) -> None:
        """Rebuild tree with current filters."""
        self.clear()
        self._note_nodes.clear()

        # Group notes by type
        for note_type in NoteType:
            if self.filter_type and note_type != self.filter_type:
                continue

            notes = self._filter_notes(self.store.get_notes_by_type(note_type))
            if not notes:
                continue

            notes = self._sort_notes(notes)

            # Add type folder
            folder_label = self._get_folder_label(note_type, len(notes))
            folder = self.root.add(folder_label, expand=True)
            folder.data = {"type": "folder", "note_type": note_type}

            # Add notes
            for note in notes:
                label = self._get_note_label(note)
                node = folder.add_leaf(label)
                node.data = {"type": "note", "path": note.path, "note": note}
                self._note_nodes[note.path] = node

        # Expand root
        self.root.expand()

    def _filter_notes(self, notes: list[Note]) -> list[Note]:
        """Apply scope, tag, and text filters."""
        result = []
        for note in notes:
            if self.filter_scope and note.frontmatter.scope != self.filter_scope:
                continue
            if self.filter_tag and self.filter_tag not in note.frontmatter.tags:
                continue
            if self.filter_text:
                searchable = (
                    note.title.lower()
                    + " "
                    + note.path.stem.lower()
                    + " "
                    + " ".join(note.frontmatter.tags)
                )
                if self.filter_text not in searchable:
                    continue
            result.append(note)
        return result

    def _sort_notes(self, notes: list[Note]) -> list[Note]:
        """Sort notes by the selected mode."""
        if self.sort_mode == "alpha_asc":
            return sorted(
                notes,
                key=lambda note: (
                    (note.title or note.slug.replace("_", " ")).lower(),
                    note.path.name.lower(),
                ),
            )
        if self.sort_mode == "alpha_desc":
            return sorted(
                notes,
                key=lambda note: (
                    (note.title or note.slug.replace("_", " ")).lower(),
                    note.path.name.lower(),
                ),
                reverse=True,
            )
        if self.sort_mode == "updated_asc":
            return sorted(
                notes,
                key=lambda note: (note.frontmatter.updated, note.path.name.lower()),
            )

        # Default: updated newest first.
        return sorted(
            notes,
            key=lambda note: (note.frontmatter.updated, note.path.name.lower()),
            reverse=True,
        )

    def _get_folder_label(self, note_type: NoteType, count: int) -> str:
        """Get display label for a folder."""
        labels = {
            NoteType.FACT: "Facts",
            NoteType.PREF: "Preferences",
            NoteType.GOAL: "Goals",
            NoteType.LOOP: "Open Loops",
            NoteType.CONCEPT: "Concepts",
        }
        return f"{labels.get(note_type, note_type.value)} ({count})"

    def _get_note_label(self, note: Note) -> str:
        """Get display label for a note."""
        title = note.title or note.slug.replace("_", " ").title()
        if len(title) > 40:
            title = title[:37] + "..."
        return title

    def set_filter_type(self, note_type: NoteType | None) -> None:
        """Set type filter and rebuild."""
        self.filter_type = note_type
        self.rebuild()

    def set_filter_scope(self, scope: Scope | None) -> None:
        """Set scope filter and rebuild."""
        self.filter_scope = scope
        self.rebuild()

    def set_filter_text(self, text: str) -> None:
        """Set text filter and rebuild."""
        self.filter_text = text.lower()
        self.rebuild()

    def set_filter_tag(self, tag: str | None) -> None:
        """Set tag filter and rebuild."""
        self.filter_tag = tag.lower() if tag else None
        self.rebuild()

    def set_sort_mode(self, sort_mode: str) -> None:
        """Set sort mode and rebuild."""
        self.sort_mode = sort_mode
        self.rebuild()

    def select_note(self, path: Path) -> None:
        """Select a specific note in the tree."""
        if path in self._note_nodes:
            node = self._note_nodes[path]
            self.select_node(node)

"""In-memory note repository."""

from collections import defaultdict
from pathlib import Path

from ledger.notes import NoteType, Scope, LoopStatus
from ..models.note import Note
from .note_parser import NoteParser


class NoteStore:
    """Central repository for all notes, loaded into memory."""

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.notes_dir = root_dir / "notes"
        self.parser = NoteParser()
        self._notes: dict[Path, Note] = {}
        self._by_type: dict[NoteType, list[Note]] = defaultdict(list)
        self._by_tag: dict[str, list[Note]] = defaultdict(list)
        self._by_scope: dict[Scope, list[Note]] = defaultdict(list)
        self._incoming_links: dict[str, list[Path]] = defaultdict(list)

    def load_all(self) -> None:
        """Load all notes from disk."""
        self._notes.clear()
        self._by_type.clear()
        self._by_tag.clear()
        self._by_scope.clear()
        self._incoming_links.clear()

        # Load from each note folder
        for note_type in NoteType:
            folder = self.root_dir / note_type.folder
            if not folder.exists():
                continue

            for path in folder.glob("*.md"):
                try:
                    note = self.parser.parse_file(path)
                    self._index_note(note)
                except Exception:
                    # Skip malformed notes
                    continue

        # Build incoming links index
        self._build_incoming_links()

    def filesystem_snapshot(self) -> dict[Path, float]:
        """Return current note-file mtime snapshot for watch mode."""
        snapshot: dict[Path, float] = {}
        for note_type in NoteType:
            folder = self.root_dir / note_type.folder
            if not folder.exists():
                continue
            for path in folder.glob("*.md"):
                try:
                    snapshot[path.resolve()] = path.stat().st_mtime
                except OSError:
                    continue
        return snapshot

    def _index_note(self, note: Note) -> None:
        """Add a note to all indexes."""
        self._notes[note.path] = note
        self._by_type[note.note_type].append(note)

        for tag in note.frontmatter.tags:
            self._by_tag[tag].append(note)

        self._by_scope[note.frontmatter.scope].append(note)

    def _build_incoming_links(self) -> None:
        """Build reverse index of incoming links."""
        for note in self._notes.values():
            for link in note.outgoing_links:
                # Normalize target to match against note stems
                target = link.target
                if target.endswith(".md"):
                    target = Path(target).stem
                # Strip path components for wiki-style links
                if "/" in target:
                    target = target.split("/")[-1]
                    if target.endswith(".md"):
                        target = target[:-3]

                self._incoming_links[target].append(note.path)

        # Attach incoming links to notes
        for note in self._notes.values():
            stem = note.path.stem
            if stem in self._incoming_links:
                note.incoming_links = list(self._incoming_links[stem])

    def get_note(self, path: Path) -> Note | None:
        """Get a single note by path."""
        return self._notes.get(path)

    def get_note_by_stem(self, stem: str) -> Note | None:
        """Get a note by filename stem (without .md)."""
        for note in self._notes.values():
            if note.path.stem == stem:
                return note
        return None

    def all_notes(self) -> list[Note]:
        """Get all notes, sorted by updated (most recent first)."""
        return sorted(
            self._notes.values(),
            key=lambda n: n.frontmatter.updated,
            reverse=True,
        )

    def get_notes_by_type(self, note_type: NoteType) -> list[Note]:
        """Get notes filtered by type."""
        return sorted(
            self._by_type.get(note_type, []),
            key=lambda n: n.frontmatter.updated,
            reverse=True,
        )

    def get_notes_by_tag(self, tag: str) -> list[Note]:
        """Get notes filtered by tag."""
        return sorted(
            self._by_tag.get(tag, []),
            key=lambda n: n.frontmatter.updated,
            reverse=True,
        )

    def get_notes_by_scope(self, scope: Scope) -> list[Note]:
        """Get notes filtered by scope."""
        return sorted(
            self._by_scope.get(scope, []),
            key=lambda n: n.frontmatter.updated,
            reverse=True,
        )

    def get_open_loops(self) -> list[Note]:
        """Get all open loops."""
        return [
            note
            for note in self._by_type.get(NoteType.LOOP, [])
            if note.frontmatter.status == LoopStatus.OPEN
        ]

    def search(self, query: str, scope: Scope | None = None) -> list[Note]:
        """Search notes by content (simple substring match)."""
        query_lower = query.lower()
        results = []

        for note in self._notes.values():
            if scope and note.frontmatter.scope != scope:
                continue

            # Search in title, body, and tags
            searchable = (
                note.title.lower()
                + " "
                + note.body.lower()
                + " "
                + " ".join(note.frontmatter.tags)
            )
            if query_lower in searchable:
                results.append(note)

        return sorted(results, key=lambda n: n.frontmatter.updated, reverse=True)

    def all_tags(self) -> list[str]:
        """Get all unique tags, sorted alphabetically."""
        return sorted(self._by_tag.keys())

    def refresh(self) -> None:
        """Reload all notes from disk."""
        self.load_all()

"""Note data models for the TUI.

The canonical ``Frontmatter``, ``NoteType``, and other enums now live in
``ledger.notes``.  This module re-exports them and defines ``Note`` - a
thin TUI-specific wrapper that adds ``incoming_links`` and ``lint_warnings``.
"""

from dataclasses import dataclass, field
from pathlib import Path

from ledger.notes import (
    Frontmatter,
    NoteType,
    LintWarning,
)
from ledger.parsing import NoteLink  # noqa: F401 - re-exported as part of TUI model surface

# Re-export for backward compatibility
__all__ = ["Frontmatter", "NoteLink", "LintWarning", "Note"]


@dataclass
class Note:
    """A single ledger note for TUI display."""

    path: Path
    note_type: NoteType
    frontmatter: Frontmatter
    title: str
    body: str  # Raw markdown content (without frontmatter)
    sections: dict[str, str] = field(default_factory=dict)
    outgoing_links: list[NoteLink] = field(default_factory=list)
    incoming_links: list[Path] = field(default_factory=list)
    lint_warnings: list[LintWarning] = field(default_factory=list)

    @property
    def slug(self) -> str:
        """Extract slug from filename."""
        name = self.path.stem
        prefix = self.note_type.prefix
        if name.startswith(prefix):
            return name[len(prefix):]
        return name

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        return self.title or self.slug.replace("_", " ").title()

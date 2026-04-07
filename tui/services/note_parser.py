"""Note parsing service - extracts frontmatter, sections, and links.

This module uses the canonical parser from ledger.parsing for frontmatter
and section parsing to ensure consistent behavior across CLI and TUI.
"""

from pathlib import Path

from ledger.notes import NoteType, Frontmatter
from ..models.note import Note

# Import canonical parsing utilities from ledger package
from ledger.parsing import (
    parse_sections as canonical_parse_sections,
    extract_title as canonical_extract_title,
    extract_links as canonical_extract_links,
)
from ledger.notes import read_note as read_base_note


class NoteParser:
    """Parses markdown notes with YAML frontmatter.

    Uses canonical parsing from ledger.parsing to ensure consistent
    behavior with CLI. The TUI-specific part is converting parsed
    data into Note dataclasses.
    """

    def parse_file(self, path: Path) -> Note:
        """Parse a note file into a Note object.

        Uses the canonical ledger.parsing module for frontmatter and
        section parsing to ensure consistent behavior with the CLI.
        """
        base_note = read_base_note(path)

        # base_note.frontmatter is already a Frontmatter instance
        # (auto-converted from dict in BaseNote.__post_init__)
        frontmatter: Frontmatter = base_note.frontmatter

        body = base_note.body
        sections = dict(base_note.sections)
        if not sections:
            raw_sections = canonical_parse_sections(body)
            sections = {k: "\n".join(v).strip() for k, v in raw_sections.items()}

        note_type = NoteType.from_path(str(path))
        if note_type is None:
            note_type = NoteType.CONCEPT

        outgoing_links = list(base_note.outgoing_links) or canonical_extract_links(body)

        return Note(
            path=path,
            note_type=note_type,
            frontmatter=frontmatter,
            title=base_note.title or canonical_extract_title(body),
            body=body,
            sections=sections,
            outgoing_links=outgoing_links,
        )

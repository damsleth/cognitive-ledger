"""Note parsing service - extracts frontmatter, sections, and links.

This module uses the canonical parser from ledger.parsing for frontmatter
and section parsing to ensure consistent behavior across CLI and TUI.
"""

from datetime import datetime, timezone
from pathlib import Path

from ..models.enums import NoteType, Source, Scope, LoopStatus
from ..models.note import Frontmatter, Note

# Import canonical parsing utilities from ledger package
from ledger.parsing import (
    parse_sections as canonical_parse_sections,
    extract_title as canonical_extract_title,
    extract_links as canonical_extract_links,
    normalize_tags,
    parse_timestamp,
)
from ledger.notes import read_note as read_base_note


class NoteParser:
    """Parses markdown notes with YAML frontmatter.

    Uses canonical parsing from ledger.parsing to ensure consistent
    behavior with CLI. The TUI-specific part is converting parsed
    data into Note/Frontmatter dataclasses with enums.
    """

    def parse_file(self, path: Path) -> Note:
        """Parse a note file into a Note object.

        Uses the canonical ledger.parsing module for frontmatter and
        section parsing to ensure consistent behavior with the CLI.
        """
        # Build canonical base note from ledger.notes, then adapt to TUI model.
        base_note = read_base_note(path)
        frontmatter = self._build_frontmatter(base_note.frontmatter)
        body = base_note.body
        sections = dict(base_note.sections)
        if not sections:
            raw_sections = canonical_parse_sections(body)
            sections = {k: "\n".join(v).strip() for k, v in raw_sections.items()}

        # Determine note type
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

    def _build_frontmatter(self, data: dict) -> Frontmatter:
        """Build Frontmatter dataclass from parsed dict."""
        # Use canonical timestamp parsing (returns datetime | None)
        created_raw = parse_timestamp(str(data.get("created", "")))
        updated_raw = parse_timestamp(str(data.get("updated", "")))

        # Fallback to now() if timestamp is None or invalid
        created = created_raw if created_raw else datetime.now(timezone.utc)
        updated = updated_raw if updated_raw else datetime.now(timezone.utc)

        # Parse enums with fallbacks
        source = self._parse_enum(Source, data.get("source", ""), Source.ASSISTANT)
        scope = self._parse_enum(Scope, data.get("scope", ""), Scope.PERSONAL)
        status = None
        if "status" in data:
            status = self._parse_enum(LoopStatus, data["status"], LoopStatus.OPEN)

        # Use canonical tag normalization
        tags = normalize_tags(data.get("tags"))

        # Parse confidence
        confidence = data.get("confidence", 0.5)
        if isinstance(confidence, str):
            try:
                confidence = float(confidence)
            except ValueError:
                confidence = 0.5

        return Frontmatter(
            created=created,
            updated=updated,
            tags=tags,
            confidence=confidence,
            source=source,
            scope=scope,
            lang=data.get("lang", "en"),
            status=status,
        )

    def _parse_enum(self, enum_class, value: str, default):
        """Parse enum value with fallback."""
        if not value:
            return default
        try:
            return enum_class(str(value).lower())
        except ValueError:
            return default

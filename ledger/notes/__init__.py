"""Note type definitions and models for Cognitive Ledger.

This module provides dataclasses representing different note types
(facts, preferences, goals, loops, concepts) and functions for
reading and parsing notes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.parsing import (
    parse_frontmatter_text,
    parse_sections,
    extract_title,
    first_content_line,
    first_checkbox,
    extract_links,
    NoteLink,
)


# Note type configuration
# TODO: Move to config when we refactor further
@dataclass(frozen=True)
class NoteTypeConfig:
    """Configuration for a note type."""

    folder: str
    """Folder name under notes/ directory."""

    prefix: str
    """File prefix (e.g., 'fact__')."""

    label: str
    """Short label for display."""

    @property
    def dir(self) -> Path:
        """Get the directory path for this note type."""
        config = get_config()
        return config.notes_dir / self.folder

    def path_in(self, notes_dir: Path) -> Path:
        """Get the directory path for this note type under an explicit notes root."""
        return Path(notes_dir) / self.folder


# Standard note types with their configurations
NOTE_TYPE_CONFIGS: dict[str, NoteTypeConfig] = {
    "facts": NoteTypeConfig(folder="02_facts", prefix="fact__", label="fact"),
    "preferences": NoteTypeConfig(folder="03_preferences", prefix="pref__", label="pref"),
    "goals": NoteTypeConfig(folder="04_goals", prefix="goal__", label="goal"),
    "loops": NoteTypeConfig(folder="05_open_loops", prefix="loop__", label="loop"),
    "concepts": NoteTypeConfig(folder="06_concepts", prefix="concept__", label="concept"),
}

CORE_NOTE_TYPES = tuple(NOTE_TYPE_CONFIGS.keys())
LOOP_STATUSES = ("open", "closed", "blocked", "snoozed")


@dataclass
class LintWarning:
    """A lint warning for a note."""

    code: str
    """Short identifier, e.g. 'HIGH_CONFIDENCE_INFERRED'."""

    message: str
    """Human-readable description of the issue."""

    severity: str
    """'error' or 'warning'."""

    suggested_fix: dict | None = None
    """Optional structured hint for auto-fix."""


@dataclass
class BaseNote:
    """Base dataclass for all note types."""

    path: Path
    """Path to the note file."""

    frontmatter: dict[str, Any]
    """Parsed YAML frontmatter."""

    body: str
    """Markdown body (without frontmatter)."""

    title: str
    """Extracted H1 title."""

    note_type: str
    """Type of note (facts, preferences, etc.)."""

    links: str = ""
    """Links section content."""

    sections: dict[str, str] = field(default_factory=dict)
    """Parsed markdown sections as normalized_name -> text."""

    outgoing_links: list[NoteLink] = field(default_factory=list)
    """Outgoing links extracted from markdown body."""

    @property
    def confidence(self) -> float:
        """Get confidence value from frontmatter (0.0-1.0)."""
        try:
            value = float(self.frontmatter.get("confidence", 0))
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, value))

    @property
    def updated(self) -> str:
        """Get updated timestamp string."""
        return str(self.frontmatter.get("updated", ""))

    @property
    def scope(self) -> str:
        """Get scope from frontmatter."""
        return str(self.frontmatter.get("scope", "")).lower()

    @property
    def status(self) -> str:
        """Get status from frontmatter (primarily for loops)."""
        return str(self.frontmatter.get("status", "")).lower()

    @property
    def tags(self) -> list[str]:
        """Get tags from frontmatter."""
        raw = self.frontmatter.get("tags", [])
        if isinstance(raw, list):
            return [str(t).lower() for t in raw]
        return [str(raw).lower()]


@dataclass
class LoopNote(BaseNote):
    """Dataclass for open loop notes."""

    question: str = ""
    """The question or task."""

    why: str = ""
    """Why it matters section."""

    next_action: str = ""
    """First/current next action."""

    @classmethod
    def from_path(cls, path: Path) -> LoopNote:
        """Create a LoopNote from a file path."""
        frontmatter, body = read_note_file(path)
        sections_raw = parse_sections(body)
        sections = {key: "\n".join(values).strip() for key, values in sections_raw.items()}

        question = sections.get("question or task", "")
        title = extract_title(body)
        if not question:
            question = title or path.name.replace("loop__", "").replace("_", " ")

        next_action_text = sections.get("next action", "")
        if not next_action_text:
            next_action_text = body
        next_action = first_checkbox(next_action_text)
        if not next_action and next_action_text:
            lines = next_action_text.splitlines()
            next_action = lines[0].strip() if lines else ""

        return cls(
            path=path,
            frontmatter=frontmatter,
            body=body,
            title=title,
            note_type="loops",
            links=sections.get("links", ""),
            sections=sections,
            outgoing_links=extract_links(body),
            question=question,
            why=sections.get("why it matters", ""),
            next_action=next_action,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (for backward compatibility)."""
        return {
            "path": str(self.path),
            "frontmatter": self.frontmatter,
            "question": self.question,
            "why": self.why,
            "next_action": self.next_action,
            "links": self.links,
            "title": self.title,
            "body": self.body,
            "type": self.note_type,
        }


@dataclass
class GenericNote(BaseNote):
    """Dataclass for generic notes (facts, preferences, goals, concepts)."""

    statement: str = ""
    """Statement section content."""

    context: str = ""
    """Context section content."""

    implications: str = ""
    """Implications section content."""

    @classmethod
    def from_path(cls, path: Path, note_type: str) -> GenericNote:
        """Create a GenericNote from a file path."""
        frontmatter, body = read_note_file(path)
        sections_raw = parse_sections(body)
        sections = {key: "\n".join(values).strip() for key, values in sections_raw.items()}

        title = extract_title(body) or path.name.replace("_", " ")
        statement = sections.get("statement", "")
        if not statement:
            statement = first_content_line(body)

        return cls(
            path=path,
            frontmatter=frontmatter,
            body=body,
            title=title,
            note_type=note_type,
            links=sections.get("links", ""),
            sections=sections,
            outgoing_links=extract_links(body),
            statement=statement,
            context=sections.get("context", ""),
            implications=sections.get("implications", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (for backward compatibility)."""
        return {
            "path": str(self.path),
            "frontmatter": self.frontmatter,
            "title": self.title,
            "statement": self.statement,
            "context": self.context,
            "implications": self.implications,
            "links": self.links,
            "body": self.body,
            "type": self.note_type,
        }


def read_note_file(path: Path) -> tuple[dict[str, Any], str]:
    """Read a note file and parse frontmatter.

    Args:
        path: Path to the note file.

    Returns:
        Tuple of (frontmatter dict, body string).
    """
    text = path.read_text(encoding="utf-8")
    return parse_frontmatter_text(text)


def read_note(path: Path, note_type: str | None = None) -> BaseNote:
    """Read a note file and return appropriate note object.

    Args:
        path: Path to the note file.
        note_type: Note type override. If None, inferred from path.

    Returns:
        LoopNote or GenericNote depending on type.
    """
    if note_type is None:
        # Infer from path
        for nt, cfg in NOTE_TYPE_CONFIGS.items():
            if cfg.folder in str(path):
                note_type = nt
                break
        else:
            note_type = "facts"  # Default fallback

    if note_type == "loops":
        return LoopNote.from_path(path)
    return GenericNote.from_path(path, note_type)


def get_notes(
    note_type: str,
    loop_status: str | None = None,
    notes_dir: Path | None = None,
) -> list[BaseNote]:
    """Get all notes of a given type, sorted by updated date.

    Args:
        note_type: Type of note ('facts', 'preferences', 'goals', 'loops', 'concepts', 'all').
        loop_status: For loops, filter by status ('open', 'closed', etc.).
        notes_dir: Optional notes root override.

    Returns:
        List of note objects sorted by updated timestamp (newest first).
    """
    notes: list[BaseNote] = []

    if note_type == "all":
        for nt in CORE_NOTE_TYPES:
            notes.extend(get_notes(nt, notes_dir=notes_dir))
        notes.sort(key=lambda x: x.updated, reverse=True)
        return notes

    if note_type not in NOTE_TYPE_CONFIGS:
        return notes

    config = NOTE_TYPE_CONFIGS[note_type]
    note_dir = config.path_in(notes_dir) if notes_dir is not None else config.dir

    if not note_dir.is_dir():
        return notes

    for path in note_dir.iterdir():
        if path.suffix != ".md":
            continue

        try:
            note = read_note(path, note_type)
        except Exception:
            # Skip notes that fail to parse
            continue

        # Filter loops by status if specified
        if note_type == "loops" and loop_status and loop_status != "all":
            if note.status != loop_status:
                continue

        notes.append(note)

    notes.sort(key=lambda x: x.updated, reverse=True)
    return notes


def compute_recency_score(updated_ts: datetime | None, now_dt: datetime) -> float:
    """Compute recency score (0.0-1.0) based on age.

    Args:
        updated_ts: When the note was last updated.
        now_dt: Current datetime.

    Returns:
        Recency score where 1.0 is very recent, 0.0 is old (> 90 days).
    """
    if not updated_ts:
        return 0.0
    age_days = max(0.0, (now_dt - updated_ts).total_seconds() / 86400.0)
    return max(0.0, 1.0 - (age_days / 90.0))

"""Note type definitions and models for Cognitive Ledger.

This module provides dataclasses representing different note types
(facts, preferences, goals, loops, concepts) and functions for
reading and parsing notes.

Canonical enums and the structured ``Frontmatter`` dataclass live here
so that both the core library and the TUI share a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.layout import NOTE_LAYOUTS, CORE_NOTE_TYPES as LAYOUT_CORE_NOTE_TYPES, logical_path
from ledger.parsing import (
    parse_frontmatter_text,
    parse_sections,
    extract_title,
    first_content_line,
    first_checkbox,
    extract_links,
    normalize_tags,
    parse_timestamp,
    NoteLink,
)


# ---------------------------------------------------------------------------
# Canonical enums (shared by library and TUI)
# ---------------------------------------------------------------------------

class NoteType(Enum):
    """Note type categories."""

    IDENTITY = "id"
    FACT = "fact"
    PREF = "pref"
    GOAL = "goal"
    LOOP = "loop"
    CONCEPT = "concept"

    @property
    def prefix(self) -> str:
        return f"{self.value}__"

    @property
    def folder(self) -> str:
        for layout in NOTE_LAYOUTS.values():
            if layout.label == self.value:
                return layout.logical_dir.as_posix()
        raise ValueError(f"Unknown note type label: {self.value}")

    @property
    def subdir(self) -> str:
        for layout in NOTE_LAYOUTS.values():
            if layout.label == self.value:
                return layout.subdir
        raise ValueError(f"Unknown note type label: {self.value}")

    @classmethod
    def from_path(cls, path: str) -> NoteType | None:
        """Infer note type from file path."""
        name = path.split("/")[-1] if "/" in path else path
        for t in cls:
            if name.startswith(t.prefix):
                return t
        return None


class Source(Enum):
    """Note provenance."""

    USER = "user"
    TOOL = "tool"
    ASSISTANT = "assistant"
    INFERRED = "inferred"


class Scope(Enum):
    """Life domain categorization."""

    HOME = "home"
    WORK = "work"
    DEV = "dev"
    PERSONAL = "personal"
    LIFE = "life"  # alias for personal
    META = "meta"


class LoopStatus(Enum):
    """Open loop status values."""

    OPEN = "open"
    CLOSED = "closed"
    BLOCKED = "blocked"
    SNOOZED = "snoozed"


# ---------------------------------------------------------------------------
# Structured Frontmatter
# ---------------------------------------------------------------------------

def _parse_enum(enum_class: type, value: str, default: Any) -> Any:
    """Parse an enum value with fallback."""
    if not value:
        return default
    try:
        return enum_class(str(value).lower())
    except ValueError:
        return default


@dataclass
class Frontmatter:
    """Structured YAML frontmatter fields.

    Canonical representation shared by library and TUI.
    """

    created: datetime
    updated: datetime
    tags: list[str]
    confidence: float
    source: Source
    scope: Scope
    lang: str
    status: LoopStatus | None = None  # Only for loops

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Frontmatter:
        """Construct from a raw frontmatter dict (as returned by YAML parsing)."""
        created_raw = parse_timestamp(str(data.get("created", "")))
        updated_raw = parse_timestamp(str(data.get("updated", "")))

        created = created_raw if created_raw else datetime.now(timezone.utc)
        updated = updated_raw if updated_raw else datetime.now(timezone.utc)

        source = _parse_enum(Source, data.get("source", ""), Source.ASSISTANT)
        scope = _parse_enum(Scope, data.get("scope", ""), Scope.PERSONAL)
        status = None
        if "status" in data:
            status = _parse_enum(LoopStatus, data["status"], LoopStatus.OPEN)

        tags = normalize_tags(data.get("tags"))

        confidence = data.get("confidence", 0.0)
        if isinstance(confidence, str):
            try:
                confidence = float(confidence)
            except ValueError:
                confidence = 0.0

        return cls(
            created=created,
            updated=updated,
            tags=tags,
            confidence=max(0.0, min(1.0, float(confidence))),
            source=source,
            scope=scope,
            lang=data.get("lang", "en"),
            status=status,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for YAML serialization or backward compatibility."""
        d: dict[str, Any] = {
            "created": self.created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated": self.updated.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tags": self.tags,
            "confidence": self.confidence,
            "source": self.source.value,
            "scope": self.scope.value,
            "lang": self.lang,
        }
        if self.status is not None:
            d["status"] = self.status.value
        return d

    # dict-like access for backward compatibility
    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like get() for backward compatibility."""
        return self.to_dict().get(key, default)


# ---------------------------------------------------------------------------
# Note type configuration
# ---------------------------------------------------------------------------

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
        return config.ledger_notes_dir / self.folder

    def path_in(self, notes_dir: Path) -> Path:
        """Get the directory path for this note type under an explicit notes root."""
        return Path(notes_dir) / self.folder


# Standard note types with their configurations
NOTE_TYPE_CONFIGS: dict[str, NoteTypeConfig] = {
    name: NoteTypeConfig(folder=layout.subdir, prefix=layout.prefix, label=layout.label)
    for name, layout in NOTE_LAYOUTS.items()
}

CORE_NOTE_TYPES = LAYOUT_CORE_NOTE_TYPES
LOOP_STATUSES = ("open", "closed", "blocked", "snoozed")
IDENTITY_TYPES = ("mission", "beliefs", "models", "strategies", "narratives", "voice")


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

    frontmatter: Any  # Frontmatter; accepts raw dict via __post_init__ conversion
    """Structured frontmatter. Accepts a raw dict for backward compatibility."""

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

    def __post_init__(self) -> None:
        """Auto-convert raw dicts to Frontmatter for backward compatibility."""
        if isinstance(self.frontmatter, dict):
            self.frontmatter = Frontmatter.from_dict(self.frontmatter)

    @property
    def confidence(self) -> float:
        """Get confidence value from frontmatter (0.0-1.0)."""
        return self.frontmatter.confidence

    @property
    def updated(self) -> str:
        """Get updated timestamp string."""
        return self.frontmatter.updated.strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def scope(self) -> str:
        """Get scope from frontmatter."""
        return self.frontmatter.scope.value

    @property
    def status(self) -> str:
        """Get status from frontmatter (primarily for loops)."""
        if self.frontmatter.status is None:
            return ""
        return self.frontmatter.status.value

    @property
    def tags(self) -> list[str]:
        """Get tags from frontmatter."""
        return self.frontmatter.tags


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
            "path": logical_path(
                self.path,
                ledger_root=get_config().ledger_root,
                ledger_notes_dir=get_config().ledger_notes_dir,
            ).as_posix(),
            "frontmatter": self.frontmatter.to_dict(),
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
            "path": logical_path(
                self.path,
                ledger_root=get_config().ledger_root,
                ledger_notes_dir=get_config().ledger_notes_dir,
            ).as_posix(),
            "frontmatter": self.frontmatter.to_dict(),
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

    Delegates to the canonical implementation in ledger.retrieval.

    Args:
        updated_ts: When the note was last updated.
        now_dt: Current datetime.

    Returns:
        Recency score where 1.0 is very recent, 0.0 is old (> 90 days).
    """
    from ledger.retrieval import compute_recency_component
    return compute_recency_component(updated_ts, now_dt)

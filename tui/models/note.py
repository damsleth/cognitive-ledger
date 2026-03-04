"""Note data models."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .enums import NoteType, Source, Scope, LoopStatus
from ledger.parsing import NoteLink  # noqa: F401 — re-exported as part of TUI model surface
from ledger.notes import LintWarning  # noqa: F401 — re-exported as part of TUI model surface


@dataclass
class Frontmatter:
    """YAML frontmatter fields."""

    created: datetime
    updated: datetime
    tags: list[str]
    confidence: float
    source: Source
    scope: Scope
    lang: str
    status: LoopStatus | None = None  # Only for loops

    def to_yaml_dict(self) -> dict:
        """Convert to dict for YAML serialization."""
        d = {
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


@dataclass
class Note:
    """A single ledger note."""

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
            return name[len(prefix) :]
        return name

    @property
    def display_name(self) -> str:
        """Human-readable name."""
        return self.title or self.slug.replace("_", " ").title()

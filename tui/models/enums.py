"""Enum definitions matching schema.yaml."""

from enum import Enum


class NoteType(Enum):
    """Note type categories."""

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
        folders = {
            "fact": "notes/02_facts",
            "pref": "notes/03_preferences",
            "goal": "notes/04_goals",
            "loop": "notes/05_open_loops",
            "concept": "notes/06_concepts",
        }
        return folders[self.value]

    @classmethod
    def from_path(cls, path: str) -> "NoteType | None":
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

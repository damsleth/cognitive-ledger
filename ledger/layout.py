"""Shared ledger note layout and path helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


NOTES_ROOT_NAME = "notes"
INBOX_SUBDIR = "00_inbox"
INDICES_SUBDIR = "08_indices"
TIMELINE_FILENAME = "timeline.md"
TIMELINE_JSONL_FILENAME = "timeline.jsonl"


@dataclass(frozen=True)
class NoteLayout:
    """Canonical metadata for a note type."""

    name: str
    subdir: str
    label: str
    prefix: str

    @property
    def logical_dir(self) -> Path:
        return Path(NOTES_ROOT_NAME) / self.subdir

    def physical_dir(self, ledger_notes_dir: Path) -> Path:
        return Path(ledger_notes_dir) / self.subdir


NOTE_LAYOUTS: dict[str, NoteLayout] = {
    "identity": NoteLayout(name="identity", subdir="01_identity", label="id", prefix="id__"),
    "facts": NoteLayout(name="facts", subdir="02_facts", label="fact", prefix="fact__"),
    "preferences": NoteLayout(name="preferences", subdir="03_preferences", label="pref", prefix="pref__"),
    "goals": NoteLayout(name="goals", subdir="04_goals", label="goal", prefix="goal__"),
    "loops": NoteLayout(name="loops", subdir="05_open_loops", label="loop", prefix="loop__"),
    "concepts": NoteLayout(name="concepts", subdir="06_concepts", label="concept", prefix="concept__"),
}

CORE_NOTE_TYPES: tuple[str, ...] = tuple(NOTE_LAYOUTS.keys())

LEDGER_NOTE_SUBDIRS: tuple[str, ...] = (
    INBOX_SUBDIR,
    *(layout.subdir for layout in NOTE_LAYOUTS.values()),
    "07_projects",
    INDICES_SUBDIR,
    "09_archive",
)


def note_type_metadata() -> dict[str, dict[str, str]]:
    """Return config-friendly note type metadata from the shared registry."""
    return {
        name: {
            "dir": layout.logical_dir.as_posix(),
            "subdir": layout.subdir,
            "label": layout.label,
            "prefix": layout.prefix,
        }
        for name, layout in NOTE_LAYOUTS.items()
    }


def note_type_layout(note_type: str) -> NoteLayout:
    try:
        return NOTE_LAYOUTS[note_type]
    except KeyError as exc:
        raise ValueError(f"Unknown note type: {note_type}") from exc


def note_type_dir(ledger_notes_dir: Path, note_type: str) -> Path:
    return note_type_layout(note_type).physical_dir(ledger_notes_dir)


def notes_subdir_path(ledger_notes_dir: Path, subdir: str) -> Path:
    return Path(ledger_notes_dir) / subdir


def indices_dir(ledger_notes_dir: Path) -> Path:
    return notes_subdir_path(ledger_notes_dir, INDICES_SUBDIR)


def inbox_dir(ledger_notes_dir: Path) -> Path:
    return notes_subdir_path(ledger_notes_dir, INBOX_SUBDIR)


def timeline_path(ledger_notes_dir: Path) -> Path:
    return indices_dir(ledger_notes_dir) / TIMELINE_FILENAME


def timeline_jsonl_path(ledger_notes_dir: Path) -> Path:
    return indices_dir(ledger_notes_dir) / TIMELINE_JSONL_FILENAME


def is_logical_note_path(path: str | Path) -> bool:
    normalized = str(path).replace("\\", "/").strip().lstrip("./")
    return normalized.startswith(f"{NOTES_ROOT_NAME}/")


def logical_note_path(path: str | Path, ledger_notes_dir: Path) -> Path | None:
    """Return a `notes/...` path for a file under the ledger notes dir."""
    try:
        rel = Path(path).resolve().relative_to(Path(ledger_notes_dir).resolve())
    except ValueError:
        return None
    return Path(NOTES_ROOT_NAME) / rel


def logical_path(
    path: str | Path,
    *,
    ledger_root: Path | None = None,
    ledger_notes_dir: Path | None = None,
) -> Path:
    """Normalize a path for display/persistence."""
    candidate = Path(path)
    if not candidate.is_absolute():
        normalized = Path(candidate.as_posix())
        if is_logical_note_path(normalized):
            return normalized
        if ledger_root is None:
            return normalized
        candidate = (Path(ledger_root) / normalized).resolve()
    else:
        candidate = candidate.resolve()

    if ledger_notes_dir is not None:
        note_rel = logical_note_path(candidate, ledger_notes_dir)
        if note_rel is not None:
            return note_rel

    if ledger_root is not None:
        try:
            return candidate.relative_to(Path(ledger_root).resolve())
        except ValueError:
            pass

    return candidate


def resolve_path(
    path: str | Path,
    *,
    ledger_root: Path,
    ledger_notes_dir: Path,
) -> Path:
    """Resolve a logical or repo-relative path to an absolute filesystem path."""
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    if is_logical_note_path(candidate):
        return (Path(ledger_notes_dir) / candidate.relative_to(NOTES_ROOT_NAME)).resolve()
    return (Path(ledger_root) / candidate).resolve()

"""Shared ledger note layout and path helpers.

This module is the canonical source of truth for index and artifact path
layout across the ledger package.  All path construction for note-type
directories, index files, artifact directories, and the rejected-candidates
log is centralised here so that callers never hard-code raw path strings.

Modules that depend on this layout:
  - ledger/maintenance.py  (rebuild, prune, migrate operations)
  - ledger/retrieval.py    (index loading and hybrid-search paths)
  - ledger/web/            (API route helpers and static-asset paths)
  - ledger/obsidian/       (vault importer path resolution)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


NOTES_ROOT_NAME = "notes"
INBOX_SUBDIR = "00_inbox"
INDICES_SUBDIR = "08_indices"
PROJECTS_SUBDIR = "07_projects"
ARCHIVE_SUBDIR = "09_archive"
TIMELINE_FILENAME = "timeline.md"
TIMELINE_JSONL_FILENAME = "timeline.jsonl"
REJECTED_CANDIDATES_FILENAME = "rejected_candidates.jsonl"
NOTE_INDEX_FILENAME = "note_index.json"
PATTERNS_JSON_FILENAME = "patterns.json"
PATTERNS_MD_FILENAME = "patterns.md"


@dataclass(frozen=True)
class NoteLayout:
    """Canonical metadata for a note type."""

    name: str
    subdir: str
    label: str
    prefix: str
    # Value returned by ``validation.infer_note_type_from_path``.
    kind: str = ""
    # Carries valid_from/valid_to (bitemporal lint + migration).
    bitemporal: bool = True
    # Notes carry a ``status`` field (open loops only).
    has_status: bool = False

    @property
    def logical_dir(self) -> Path:
        return Path(NOTES_ROOT_NAME) / self.subdir

    def physical_dir(self, ledger_notes_dir: Path) -> Path:
        return Path(ledger_notes_dir) / self.subdir


NOTE_LAYOUTS: dict[str, NoteLayout] = {
    "identity": NoteLayout(name="identity", subdir="01_identity", label="id", prefix="id__", kind="identity"),
    "facts": NoteLayout(name="facts", subdir="02_facts", label="fact", prefix="fact__", kind="fact"),
    "preferences": NoteLayout(
        name="preferences", subdir="03_preferences", label="pref", prefix="pref__", kind="preference"
    ),
    "goals": NoteLayout(name="goals", subdir="04_goals", label="goal", prefix="goal__", kind="goal"),
    "loops": NoteLayout(
        name="loops",
        subdir="05_open_loops",
        label="loop",
        prefix="loop__",
        kind="open_loop",
        bitemporal=False,
        has_status=True,
    ),
    "concepts": NoteLayout(name="concepts", subdir="06_concepts", label="concept", prefix="concept__", kind="concept"),
}

# Content types share embedding/import behavior; identity is loaded separately.
CONTENT_NOTE_LAYOUTS: tuple[NoteLayout, ...] = tuple(
    layout for layout in NOTE_LAYOUTS.values() if layout.name != "identity"
)
NOTE_PREFIXES: tuple[str, ...] = tuple(layout.prefix for layout in NOTE_LAYOUTS.values())

CORE_NOTE_TYPES: tuple[str, ...] = tuple(NOTE_LAYOUTS.keys())

LEDGER_NOTE_SUBDIRS: tuple[str, ...] = (
    INBOX_SUBDIR,
    *(layout.subdir for layout in NOTE_LAYOUTS.values()),
    PROJECTS_SUBDIR,
    INDICES_SUBDIR,
    ARCHIVE_SUBDIR,
)

# Folders whose notes carry valid_from/valid_to: bitemporal types plus the
# archive (superseded notes keep their window). 00_inbox is exempt.
BITEMPORAL_SUBDIRS: tuple[str, ...] = (
    *(layout.subdir for layout in NOTE_LAYOUTS.values() if layout.bitemporal),
    ARCHIVE_SUBDIR,
)

# Folder name -> path kind for folders that are not note types.
_NON_TYPE_KINDS: dict[str, str] = {
    INBOX_SUBDIR: "inbox",
    PROJECTS_SUBDIR: "project",
    ARCHIVE_SUBDIR: "archive",
}


def path_subdir(path: str | Path) -> str | None:
    """Return the ledger folder (``02_facts`` ...) a note path lives in, if any."""
    parts = str(path).replace("\\", "/").split("/")
    known = set(LEDGER_NOTE_SUBDIRS)
    # Nearest folder wins: a parent dir that happens to share a ledger folder
    # name (/tmp/08_indices/notes/05_open_loops/...) must not mask it.
    for part in reversed(parts[:-1]):
        if part in known:
            return part
    return None


def layout_for_path(path: str | Path) -> NoteLayout | None:
    """Return the note type a path belongs to, by folder."""
    subdir = path_subdir(path)
    for layout in NOTE_LAYOUTS.values():
        if layout.subdir == subdir:
            return layout
    return None


def path_kind(path: str | Path) -> str | None:
    """Folder kind of a path: a type's ``kind``, or inbox/project/archive."""
    layout = layout_for_path(path)
    if layout is not None:
        return layout.kind
    return _NON_TYPE_KINDS.get(path_subdir(path) or "")


def in_subdir(path: str | Path, subdir: str) -> bool:
    return path_subdir(path) == subdir


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


def typed_archive_notes(ledger_notes_dir: Path) -> list[Path]:
    """Archived notes that carry a typed prefix (``fact__``, ``pref__``, ...).

    `supersede()` only ever archives typed notes, so these are the notes an
    ``--as-of`` read can mean. The rest of ``09_archive`` is retired inbox
    capture (cron ingest summaries): no validity window, so an as-of read
    would treat each as valid for all time and flood the pool. Decided by
    filename so callers on the query path pay no file reads.
    """
    archive = Path(ledger_notes_dir) / ARCHIVE_SUBDIR
    if not archive.is_dir():
        return []
    prefixes = NOTE_PREFIXES
    return sorted(p for p in archive.glob("*.md") if p.name.startswith(prefixes))


def indices_dir(ledger_notes_dir: Path) -> Path:
    return notes_subdir_path(ledger_notes_dir, INDICES_SUBDIR)


def inbox_dir(ledger_notes_dir: Path) -> Path:
    return notes_subdir_path(ledger_notes_dir, INBOX_SUBDIR)


def timeline_path(ledger_notes_dir: Path) -> Path:
    return indices_dir(ledger_notes_dir) / TIMELINE_FILENAME


def timeline_jsonl_path(ledger_notes_dir: Path) -> Path:
    return indices_dir(ledger_notes_dir) / TIMELINE_JSONL_FILENAME


def rejected_candidates_path(ledger_notes_dir: Path) -> Path:
    return indices_dir(ledger_notes_dir) / REJECTED_CANDIDATES_FILENAME


def note_index_path(ledger_notes_dir: Path) -> Path:
    return indices_dir(ledger_notes_dir) / NOTE_INDEX_FILENAME


def patterns_json_path(ledger_notes_dir: Path) -> Path:
    return indices_dir(ledger_notes_dir) / PATTERNS_JSON_FILENAME


def patterns_md_path(ledger_notes_dir: Path) -> Path:
    return indices_dir(ledger_notes_dir) / PATTERNS_MD_FILENAME


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

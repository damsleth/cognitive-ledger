"""Corpus paths and note enumeration."""
from __future__ import annotations

from pathlib import Path
from ledger.layout import INDICES_SUBDIR, indices_dir as layout_indices_dir, logical_path
from ledger.config import get_config


def _config_paths() -> tuple[Path, Path, Path]:
    config = get_config()
    notes_dir = config.ledger_notes_dir
    indices_dir = layout_indices_dir(notes_dir)
    timeline = config.timeline_path
    return notes_dir, indices_dir, timeline


def _relative(path: Path) -> str:
    config = get_config()
    return logical_path(
        path,
        ledger_root=config.ledger_root,
        ledger_notes_dir=config.ledger_notes_dir,
    ).as_posix()


def _iter_note_files(include_indices: bool = False):
    notes_dir, _indices_dir, _timeline = _config_paths()
    for path in sorted(notes_dir.rglob("*.md")):
        rel = _relative(path)
        if not include_indices and f"/{INDICES_SUBDIR}/" in f"/{rel}":
            continue
        if "/templates/" in f"/{rel}":
            continue
        if path.name == ".gitkeep":
            continue
        yield path

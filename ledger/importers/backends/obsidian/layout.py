from __future__ import annotations

from pathlib import Path

from ledger.io import safe_write_text

from .models import ObsidianLedgerConfig
from .utils import TIMELINE_HEADER


LEDGER_NOTE_DIRS = (
    "00_inbox",
    "02_facts",
    "03_preferences",
    "04_goals",
    "05_open_loops",
    "06_concepts",
    "08_indices",
)


def ensure_layout(config: ObsidianLedgerConfig) -> None:
    config.ledger_root.mkdir(parents=True, exist_ok=True)
    config.notes_root.mkdir(parents=True, exist_ok=True)
    for folder in LEDGER_NOTE_DIRS:
        (config.notes_root / folder).mkdir(parents=True, exist_ok=True)
    config.bases_root.mkdir(parents=True, exist_ok=True)

    if not config.timeline_path.is_file():
        safe_write_text(config.timeline_path, TIMELINE_HEADER)

    if not config.log_path.is_file():
        safe_write_text(config.log_path, "# Obsidian Import Log\n")

    if not config.scan_path.is_file():
        safe_write_text(config.scan_path, "# Obsidian Scan Report\n")

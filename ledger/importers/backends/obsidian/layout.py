from __future__ import annotations

from pathlib import Path

from ledger.layout import (
    ATOMIC_NOTE_LAYOUTS,
    INBOX_SUBDIR,
    INDICES_SUBDIR,
)
from ledger.io import safe_write_text

from .models import ObsidianLedgerConfig
from .state import migrate_legacy_state
from .utils import TIMELINE_HEADER


LEDGER_NOTE_DIRS = (INBOX_SUBDIR, *(layout.subdir for layout in ATOMIC_NOTE_LAYOUTS), INDICES_SUBDIR)


def ensure_layout(config: ObsidianLedgerConfig) -> None:
    config.ledger_root.mkdir(parents=True, exist_ok=True)
    config.notes_root.mkdir(parents=True, exist_ok=True)
    for folder in LEDGER_NOTE_DIRS:
        (config.notes_root / folder).mkdir(parents=True, exist_ok=True)
    config.bases_root.mkdir(parents=True, exist_ok=True)
    config.adapter_state_dir.mkdir(parents=True, exist_ok=True)
    migrate_legacy_state(config)

    if not config.timeline_path.is_file():
        safe_write_text(config.timeline_path, TIMELINE_HEADER)

    if not config.log_path.is_file():
        safe_write_text(config.log_path, "# Obsidian Import Log\n")

    if not config.scan_path.is_file():
        safe_write_text(config.scan_path, "# Obsidian Scan Report\n")

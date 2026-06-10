from __future__ import annotations

import json
import warnings
from dataclasses import asdict
from datetime import datetime, timezone

from ledger.io import safe_write_text
from ledger.importers.state import relocate_legacy_file

from .models import ImportState, ObsidianLedgerConfig


# Pre-phase-4 layout: loose prefixed files directly in 08_indices.
LEGACY_STATE_FILES: dict[str, str] = {
    "state.json": "obsidian_import_state.json",
    "import_log.md": "obsidian_import_log.md",
    "scan.md": "obsidian_scan.md",
}


def migrate_legacy_state(config: ObsidianLedgerConfig) -> list[str]:
    """One-time relocation of pre-phase-4 state files into the adapter-state dir."""
    moved: list[str] = []
    for new_name, legacy_name in LEGACY_STATE_FILES.items():
        legacy = config.indices_root / legacy_name
        if relocate_legacy_file(legacy, config.adapter_state_dir / new_name):
            moved.append(legacy_name)
    return moved


def load_state(config: ObsidianLedgerConfig) -> ImportState:
    migrate_legacy_state(config)
    if not config.state_path.is_file():
        return ImportState(
            version=1,
            vault_root=str(config.vault_root),
            ledger_root=str(config.ledger_root),
            processed_files={},
            imported_keys={},
        )

    try:
        raw = json.loads(config.state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        corrupt_path = config.state_path.with_name(f"{config.state_path.name}.corrupt-{ts}")
        try:
            config.state_path.rename(corrupt_path)
        except OSError:
            pass
        warnings.warn(
            f"Obsidian state file {config.state_path} was corrupt and has been reset "
            f"(renamed to {corrupt_path.name}): {exc}",
            stacklevel=2,
        )
        return ImportState(
            version=1,
            vault_root=str(config.vault_root),
            ledger_root=str(config.ledger_root),
            processed_files={},
            imported_keys={},
        )

    if not isinstance(raw, dict):
        raw = {}

    return ImportState(
        version=int(raw.get("version", 1)),
        vault_root=str(raw.get("vault_root", str(config.vault_root))),
        ledger_root=str(raw.get("ledger_root", str(config.ledger_root))),
        last_run=str(raw.get("last_run", "")),
        processed_files=dict(raw.get("processed_files", {})),
        imported_keys=dict(raw.get("imported_keys", {})),
    )


def save_state(config: ObsidianLedgerConfig, state: ImportState) -> None:
    payload = asdict(state)
    safe_write_text(config.state_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

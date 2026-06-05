from __future__ import annotations

import json
from dataclasses import asdict

from ledger.io import safe_write_text

from .models import ImportState, ObsidianLedgerConfig


def load_state(config: ObsidianLedgerConfig) -> ImportState:
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
    except (json.JSONDecodeError, OSError):
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

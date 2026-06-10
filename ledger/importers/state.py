"""Shared adapter-state storage for import backends.

Every import backend persists its mutable artifacts (processed-file state,
import logs, scan reports) under one shared adapter-state root inside the
target ledger's indices directory:

    <notes_dir>/08_indices/importers/<backend>/

``08_indices`` already owns generated state for the core engine (sync state,
signals, query log), so adapter state lives there too — namespaced per
backend instead of the old binary-era convention of prefixed loose files
(``obsidian_import_state.json`` etc.) scattered directly in ``08_indices``.
"""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ledger.io import safe_write_text
from ledger.layout import indices_dir

IMPORTERS_STATE_SUBDIR = "importers"


def importers_state_root(notes_dir: Path) -> Path:
    """Root directory holding per-backend adapter state."""
    return indices_dir(notes_dir) / IMPORTERS_STATE_SUBDIR


def backend_state_dir(notes_dir: Path, backend: str) -> Path:
    """State directory for one backend under the shared adapter-state root."""
    return importers_state_root(notes_dir) / backend


def load_json_state(path: Path) -> dict[str, Any]:
    """Read a JSON state file, returning ``{}`` on missing or corrupt content.

    On parse error the file is renamed to ``<name>.corrupt-<ts>`` so it is
    not silently discarded — the caller can inspect it for data recovery.
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        corrupt_path = path.with_name(f"{path.name}.corrupt-{ts}")
        try:
            path.rename(corrupt_path)
        except OSError:
            pass
        warnings.warn(
            f"State file {path} was corrupt and has been reset "
            f"(renamed to {corrupt_path.name}): {exc}",
            stacklevel=2,
        )
        return {}
    return raw if isinstance(raw, dict) else {}


def save_json_state(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a JSON state file atomically (parents created as needed)."""
    safe_write_text(path, json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")


def relocate_legacy_file(legacy: Path, new: Path) -> bool:
    """Move a legacy state file to its shared-root location (one-time).

    Returns True when the file was moved; no-op when the legacy file is
    absent or the new location already exists.
    """
    if not legacy.is_file() or new.exists():
        return False
    new.parent.mkdir(parents=True, exist_ok=True)
    legacy.rename(new)
    return True

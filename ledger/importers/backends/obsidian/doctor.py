from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path

from .models import ObsidianLedgerConfig


def run_doctor(config: ObsidianLedgerConfig) -> tuple[int, list[str]]:
    lines: list[str] = []
    errors = 0

    lines.append(f"vault: {config.vault_root}")
    lines.append(f"ledger root: {config.ledger_root}")

    if sys.version_info < (3, 11):
        errors += 1
        lines.append("error: python 3.11+ is required")
    else:
        lines.append(f"ok: python {sys.version.split()[0]}")

    if not config.vault_root.exists():
        errors += 1
        lines.append("error: vault path does not exist")
    else:
        lines.append("ok: vault path exists")

    if not config.vault_root.is_dir():
        errors += 1
        lines.append("error: vault path is not a directory")

    obsidian_dir = config.vault_root / ".obsidian"
    if obsidian_dir.exists():
        lines.append("ok: .obsidian folder found")
    else:
        lines.append("warn: .obsidian not found; treating root as a generic markdown note base")

    # A diagnostic must not mutate the vault: probing by mkdir left an empty
    # `cognitive-ledger/` behind in vaults that were never initialized.
    # Check the nearest existing ancestor instead: "could init create it?"
    target = config.ledger_root
    while not target.exists() and target != target.parent:
        target = target.parent
    if os.access(target, os.W_OK):
        suffix = "" if config.ledger_root.exists() else " (not initialized; run `init`)"
        lines.append(f"ok: ledger root is writable{suffix}")
    else:
        errors += 1
        lines.append("error: ledger root is not writable")

    core_plugins_path = obsidian_dir / "core-plugins.json"
    if core_plugins_path.is_file():
        try:
            data = json.loads(core_plugins_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("bases") is True:
                lines.append("ok: Obsidian Bases plugin is enabled")
            else:
                lines.append("warn: Obsidian Bases plugin not enabled")
        except (json.JSONDecodeError, OSError):
            lines.append("warn: unable to parse core-plugins.json")
    else:
        lines.append("warn: core-plugins.json missing; cannot verify Bases")

    try:
        import watchdog  # noqa: F401

        lines.append("ok: watchdog installed")
    except ImportError:
        errors += 1
        lines.append("error: watchdog is not installed")

    if platform.system().lower() == "darwin":
        lines.append("ok: macOS daemon mode supported")
    else:
        lines.append("warn: daemon mode is macOS-first; use foreground watch on this platform")

    return (1 if errors else 0, lines)

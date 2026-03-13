"""Optional Obsidian adapter surface for Cognitive Ledger.

This package is intentionally peripheral to the core retrieval/eval/context
stack. The supported surface is the bootstrap/import/watch tooling exported
here; core ledger modules should not depend on `ledger.obsidian`.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["main", "load_config", "run_import", "sync_queue"]

_EXPORTS = {
    "main": ("ledger.obsidian.cli", "main"),
    "load_config": ("ledger.obsidian.config", "load_config"),
    "run_import": ("ledger.obsidian.importer", "run_import"),
    "sync_queue": ("ledger.obsidian.queue", "sync_queue"),
}


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _EXPORTS[name]
    module = import_module(module_name)
    return getattr(module, attr_name)

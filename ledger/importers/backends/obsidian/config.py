from __future__ import annotations

import json
from pathlib import Path

from ledger.io import safe_write_text

from .models import ObsidianLedgerConfig


LEDGER_SUBDIR = "cognitive-ledger"
DEFAULT_VERSION = 1
DEFAULT_AUTO_WRITE_CONFIDENCE_MIN = 0.90
DEFAULT_QUEUE_CONFIDENCE_MIN = 0.70
DEFAULT_FILE_SIGNAL_MIN = 1.5
DEFAULT_MAX_FILES_PER_CYCLE = 50
DEFAULT_MAX_NOTES_PER_CYCLE = 100
DEFAULT_DEBOUNCE_SECONDS = 3.0
DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    ".obsidian",
    ".git",
    ".trash",
    ".smart-env",
    ".smartchats",
    "cognitive-ledger",
    "attachments",
)


def _normalize_excludes(raw: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_EXCLUDE_DIRS
    clean = tuple(sorted({str(item).strip() for item in raw if str(item).strip()}))
    return clean or DEFAULT_EXCLUDE_DIRS


def default_config(vault_root: Path) -> ObsidianLedgerConfig:
    vault = Path(vault_root).expanduser().resolve()
    ledger_root = vault / LEDGER_SUBDIR
    return ObsidianLedgerConfig(
        version=DEFAULT_VERSION,
        vault_root=vault,
        ledger_root=ledger_root,
        auto_write_confidence_min=DEFAULT_AUTO_WRITE_CONFIDENCE_MIN,
        queue_confidence_min=DEFAULT_QUEUE_CONFIDENCE_MIN,
        file_signal_min=DEFAULT_FILE_SIGNAL_MIN,
        max_files_per_cycle=DEFAULT_MAX_FILES_PER_CYCLE,
        max_notes_per_cycle=DEFAULT_MAX_NOTES_PER_CYCLE,
        debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
        exclude_dirs=DEFAULT_EXCLUDE_DIRS,
    )


def load_config(vault_root: Path) -> ObsidianLedgerConfig:
    cfg = default_config(vault_root)
    path = cfg.config_path
    if not path.is_file():
        return cfg

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return cfg

    if not isinstance(raw, dict):
        return cfg

    return ObsidianLedgerConfig(
        version=int(raw.get("version", cfg.version)),
        vault_root=cfg.vault_root,
        ledger_root=cfg.ledger_root,
        auto_write_confidence_min=float(raw.get("auto_write_confidence_min", cfg.auto_write_confidence_min)),
        queue_confidence_min=float(raw.get("queue_confidence_min", cfg.queue_confidence_min)),
        file_signal_min=float(raw.get("file_signal_min", cfg.file_signal_min)),
        max_files_per_cycle=int(raw.get("max_files_per_cycle", cfg.max_files_per_cycle)),
        max_notes_per_cycle=int(raw.get("max_notes_per_cycle", cfg.max_notes_per_cycle)),
        debounce_seconds=float(raw.get("debounce_seconds", cfg.debounce_seconds)),
        exclude_dirs=_normalize_excludes(raw.get("exclude_dirs")),
    )


def save_config(config: ObsidianLedgerConfig) -> None:
    payload = {
        "version": config.version,
        "vault_root": str(config.vault_root),
        "ledger_root": str(config.ledger_root),
        "auto_write_confidence_min": config.auto_write_confidence_min,
        "queue_confidence_min": config.queue_confidence_min,
        "file_signal_min": config.file_signal_min,
        "max_files_per_cycle": config.max_files_per_cycle,
        "max_notes_per_cycle": config.max_notes_per_cycle,
        "debounce_seconds": config.debounce_seconds,
        "exclude_dirs": list(config.exclude_dirs),
    }
    safe_write_text(config.config_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def validate_config(config: ObsidianLedgerConfig) -> None:
    if config.queue_confidence_min < 0 or config.queue_confidence_min > 1:
        raise ValueError("queue_confidence_min must be within [0.0, 1.0]")
    if config.auto_write_confidence_min < 0 or config.auto_write_confidence_min > 1:
        raise ValueError("auto_write_confidence_min must be within [0.0, 1.0]")
    if config.queue_confidence_min > config.auto_write_confidence_min:
        raise ValueError("queue_confidence_min cannot exceed auto_write_confidence_min")
    if config.max_files_per_cycle <= 0:
        raise ValueError("max_files_per_cycle must be > 0")
    if config.max_notes_per_cycle <= 0:
        raise ValueError("max_notes_per_cycle must be > 0")
    if config.file_signal_min < 0:
        raise ValueError("file_signal_min must be >= 0")
    if config.debounce_seconds < 0.25:
        raise ValueError("debounce_seconds must be >= 0.25")


def config_summary(config: ObsidianLedgerConfig) -> str:
    return (
        f"vault={config.vault_root} ledger={config.ledger_root} "
        f"auto>={config.auto_write_confidence_min:.2f} "
        f"queue>={config.queue_confidence_min:.2f} "
        f"signal>={config.file_signal_min:.2f}"
    )

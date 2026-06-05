from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ScanRow:
    path_abs: Path
    path_rel: str
    mtime_ms: int
    size: int
    words: int
    headings: int
    tasks: int
    signal_score: float
    yield_hint: float
    scope: str


@dataclass
class Candidate:
    kind: str
    statement: str
    confidence: float
    tags: list[str]
    context: str = ""


@dataclass
class ImportState:
    version: int = 1
    vault_root: str = ""
    ledger_root: str = ""
    last_run: str = ""
    processed_files: dict[str, dict[str, int | str]] = field(default_factory=dict)
    imported_keys: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class ImportResult:
    selected_files: int = 0
    notes_created: int = 0
    queue_created: int = 0
    skipped_low_confidence: int = 0
    skipped_deduped: int = 0
    dry_run: bool = False
    created_note_paths: list[str] = field(default_factory=list)
    created_queue_paths: list[str] = field(default_factory=list)


@dataclass
class ObsidianLedgerConfig:
    version: int
    vault_root: Path
    ledger_root: Path
    auto_write_confidence_min: float
    queue_confidence_min: float
    file_signal_min: float
    max_files_per_cycle: int
    max_notes_per_cycle: int
    debounce_seconds: float
    exclude_dirs: tuple[str, ...]

    @property
    def notes_root(self) -> Path:
        return self.ledger_root / "notes"

    @property
    def indices_root(self) -> Path:
        return self.notes_root / "08_indices"

    @property
    def bases_root(self) -> Path:
        return self.ledger_root / "bases"

    @property
    def state_path(self) -> Path:
        return self.indices_root / "obsidian_import_state.json"

    @property
    def log_path(self) -> Path:
        return self.indices_root / "obsidian_import_log.md"

    @property
    def scan_path(self) -> Path:
        return self.indices_root / "obsidian_scan.md"

    @property
    def timeline_path(self) -> Path:
        return self.indices_root / "timeline.md"

    @property
    def config_path(self) -> Path:
        return self.ledger_root / "config.json"


NOTE_FOLDERS: dict[str, str] = {
    "fact": "02_facts",
    "pref": "03_preferences",
    "goal": "04_goals",
    "loop": "05_open_loops",
    "concept": "06_concepts",
}

NOTE_PREFIX: dict[str, str] = {
    "fact": "fact",
    "pref": "pref",
    "goal": "goal",
    "loop": "loop",
    "concept": "concept",
}

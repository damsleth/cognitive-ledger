"""Obsidian import backend."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

from ledger.importers.types import DoctorResult, ImportOptions, ImportResult, QueueSyncResult

from .bases import write_bases
from .config import (
    config_summary,
    default_config,
    load_config,
    save_config,
    validate_config,
)
from .daemon import daemon_status as _daemon_status
from .daemon import start_daemon, stop_daemon
from .doctor import run_doctor as _run_doctor
from .importer import run_import as _run_import
from .layout import ensure_layout
from .queue import sync_queue
from .watch import run_watch


class ObsidianBackend:
    """Import backend for an Obsidian vault (or generic markdown note-base).

    Implementation modules are imported at module level (they are all
    package-local and depend only on core ledger modules). The one heavy
    dependency — ``ledger.retrieval`` and its embedding stack — is imported
    lazily inside :meth:`related`.
    """

    name = "obsidian"

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._config = None

    # --- config helpers ---

    def _load_config(self):
        if self._config is None:
            self._config = load_config(self.root)
        return self._config

    def _default_config(self):
        return default_config(self.root)

    # --- ImportBackend protocol ---

    def doctor(self) -> DoctorResult:
        try:
            config = self._load_config()
        except Exception as exc:
            return DoctorResult(backend=self.name, ok=False, errors=(str(exc),))
        code, lines = _run_doctor(config)
        return DoctorResult(backend=self.name, ok=(code == 0), checks={"lines": lines})

    def import_once(
        self,
        options: ImportOptions,
        max_files: int | None = None,
        max_notes: int | None = None,
    ) -> ImportResult:
        config = self._load_config()
        validate_config(config)
        r = _run_import(config, dry_run=options.dry_run, max_files=max_files, max_notes=max_notes)
        return ImportResult(
            backend=self.name,
            scanned=r.selected_files,
            imported=r.notes_created,
            skipped=r.skipped_deduped + r.skipped_low_confidence,
        )

    # --- obsidian-specific CLI-facing methods ---
    # These print output directly and return int exit codes.
    # TODO(import-adapters): decouple I/O into typed return values and move
    # the rendering into ledger/importers/cli.py (tracked in .plans/TODO.md).

    def init(self, auto_start: bool = True) -> int:
        config = self._default_config()
        ensure_layout(config)
        save_config(config)
        base_files = write_bases(config)
        print(f"initialized: {config.ledger_root}")
        print(config_summary(config))
        for f in base_files:
            try:
                rel = f.resolve().relative_to(self.root.resolve()).as_posix()
            except ValueError:
                rel = str(f)
            print(f"base: {rel}")
        if auto_start and platform.system().lower() == "darwin":
            try:
                print(start_daemon(config))
            except Exception as exc:
                print(f"warn: failed to auto-start daemon: {exc}")
        elif auto_start:
            print("warn: auto-start is macOS-only; run `ledger import obsidian watch` manually")
        return 0

    def bootstrap(
        self,
        options: ImportOptions,
        max_files: int | None = None,
        max_notes: int | None = None,
    ) -> int:
        config = self._default_config()
        if not options.dry_run:
            ensure_layout(config)
            save_config(config)
            write_bases(config)
            validate_config(config)
        r = _run_import(config, dry_run=options.dry_run, max_files=max_files, max_notes=max_notes)
        print(f"initialized: {config.ledger_root}")
        print(config_summary(config))
        print(json.dumps({
            "selected_files": r.selected_files,
            "notes_created": r.notes_created,
            "queue_created": r.queue_created,
            "skipped_low_confidence": r.skipped_low_confidence,
            "skipped_deduped": r.skipped_deduped,
            "dry_run": r.dry_run,
        }, indent=2))
        return 0

    def run_import(
        self,
        options: ImportOptions,
        max_files: int | None = None,
        max_notes: int | None = None,
    ) -> int:
        config = self._load_config()
        validate_config(config)
        r = _run_import(config, dry_run=options.dry_run, max_files=max_files, max_notes=max_notes)
        print(json.dumps({
            "selected_files": r.selected_files,
            "notes_created": r.notes_created,
            "queue_created": r.queue_created,
            "skipped_low_confidence": r.skipped_low_confidence,
            "skipped_deduped": r.skipped_deduped,
            "dry_run": r.dry_run,
        }, indent=2))
        return 0

    def watch(self, debounce_seconds: float | None = None) -> int:
        config = self._load_config()
        validate_config(config)
        return run_watch(config, debounce_seconds=debounce_seconds)

    def daemon_start(self) -> int:
        if self.root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        print(start_daemon(self._load_config()))
        return 0

    def daemon_stop(self) -> int:
        if self.root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        print(stop_daemon(self._load_config()))
        return 0

    def daemon_status(self) -> int:
        if self.root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        running, detail = _daemon_status(self._load_config())
        print(detail)
        return 0 if running else 1

    def run_doctor(self) -> DoctorResult:
        try:
            config = self._load_config()
        except Exception as exc:
            return DoctorResult(backend=self.name, ok=False, errors=(str(exc),))
        code, lines = _run_doctor(config)
        return DoctorResult(backend=self.name, ok=(code == 0), checks={"lines": lines})

    def queue_sync(self) -> QueueSyncResult | None:
        """Promote approved candidate notes. Returns None if root is missing."""
        if self.root is None:
            return None
        config = self._load_config()
        validate_config(config)
        raw = sync_queue(config)
        return QueueSyncResult(
            promoted=raw.get("promoted", 0),
            rejected=raw.get("rejected", 0),
            pending=raw.get("pending", 0),
        )

    def related(self, text: str, top_k: int = 5, json_output: bool = False) -> int:
        from ledger.retrieval import related_to_text

        results = related_to_text(text, top_k=top_k)
        if json_output:
            print(json.dumps(results, indent=2, ensure_ascii=False))
            return 0
        if not results:
            print("No related notes found.")
            return 0
        print(f"Related notes ({len(results)}):")
        for r in results:
            print(f"  {r['score']:.3f}  {r['title']}")
            print(f"         {r['path']}")
        return 0

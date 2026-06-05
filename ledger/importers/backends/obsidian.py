"""Obsidian import backend — thin wrapper over ledger.obsidian.*."""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

from ledger.importers.types import DoctorResult, ImportOptions, ImportResult


class ObsidianBackend:
    """Import backend wrapping the Obsidian adapter.

    All heavy imports are deferred so the module loads cheaply even when
    ledger.obsidian dependencies are not installed.
    """

    name = "obsidian"

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._config = None

    # --- config helpers ---

    def _load_config(self):
        from ledger.obsidian.config import load_config
        if self._config is None:
            self._config = load_config(self.root)
        return self._config

    def _default_config(self):
        from ledger.obsidian.config import default_config
        return default_config(self.root)

    # --- ImportBackend protocol ---

    def doctor(self) -> DoctorResult:
        from ledger.obsidian.doctor import run_doctor
        try:
            config = self._load_config()
        except Exception as exc:
            return DoctorResult(backend=self.name, ok=False, errors=(str(exc),))
        code, lines = run_doctor(config)
        return DoctorResult(backend=self.name, ok=(code == 0), checks={"lines": lines})

    def import_once(
        self,
        options: ImportOptions,
        max_files: int | None = None,
        max_notes: int | None = None,
    ) -> ImportResult:
        from ledger.obsidian.config import validate_config
        from ledger.obsidian.importer import run_import
        config = self._load_config()
        validate_config(config)
        r = run_import(config, dry_run=options.dry_run, max_files=max_files, max_notes=max_notes)
        return ImportResult(
            backend=self.name,
            scanned=r.selected_files,
            imported=r.notes_created,
            skipped=r.skipped_deduped + r.skipped_low_confidence,
        )

    # --- obsidian-specific CLI-facing methods ---
    # These print output directly and return int exit codes.
    # Phase 2 will decouple I/O into typed return values.

    def init(self, auto_start: bool = True) -> int:
        from ledger.obsidian.bases import write_bases
        from ledger.obsidian.config import config_summary, save_config
        from ledger.obsidian.layout import ensure_layout
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
                from ledger.obsidian.daemon import start_daemon
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
        from ledger.obsidian.bases import write_bases
        from ledger.obsidian.config import config_summary, save_config, validate_config
        from ledger.obsidian.importer import run_import
        from ledger.obsidian.layout import ensure_layout
        config = self._default_config()
        if not options.dry_run:
            ensure_layout(config)
            save_config(config)
            write_bases(config)
            validate_config(config)
        r = run_import(config, dry_run=options.dry_run, max_files=max_files, max_notes=max_notes)
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
        from ledger.obsidian.config import validate_config
        from ledger.obsidian.importer import run_import
        config = self._load_config()
        validate_config(config)
        r = run_import(config, dry_run=options.dry_run, max_files=max_files, max_notes=max_notes)
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
        from ledger.obsidian.config import validate_config
        from ledger.obsidian.watch import run_watch
        config = self._load_config()
        validate_config(config)
        return run_watch(config, debounce_seconds=debounce_seconds)

    def daemon_start(self) -> int:
        if self.root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        from ledger.obsidian.daemon import start_daemon
        print(start_daemon(self._load_config()))
        return 0

    def daemon_stop(self) -> int:
        if self.root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        from ledger.obsidian.daemon import stop_daemon
        print(stop_daemon(self._load_config()))
        return 0

    def daemon_status(self) -> int:
        if self.root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        from ledger.obsidian.daemon import daemon_status
        running, detail = daemon_status(self._load_config())
        print(detail)
        return 0 if running else 1

    def run_doctor(self) -> int:
        from ledger.obsidian.doctor import run_doctor
        try:
            config = self._load_config()
        except Exception as exc:
            print(f"error: {exc}")
            return 2
        code, lines = run_doctor(config)
        for line in lines:
            print(line)
        return code

    def queue_sync(self) -> int:
        if self.root is None:
            print("error: --vault or --root is required", file=sys.stderr)
            return 2
        from ledger.obsidian.config import validate_config
        from ledger.obsidian.queue import sync_queue
        config = self._load_config()
        validate_config(config)
        result = sync_queue(config)
        print(json.dumps(result, indent=2))
        return 0

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

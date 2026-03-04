from __future__ import annotations

import signal
import time
from pathlib import Path

from .config import load_config
from .importer import run_import
from .models import ObsidianLedgerConfig
from .queue import sync_queue
from .utils import should_skip_markdown


def run_watch(config: ObsidianLedgerConfig, debounce_seconds: float | None = None) -> int:
    try:
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError as exc:
        raise RuntimeError("watchdog is required for watch mode") from exc

    debounce = float(debounce_seconds if debounce_seconds is not None else config.debounce_seconds)
    changed_paths: set[Path] = set()
    last_event_at = 0.0
    stop = False

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event: FileSystemEvent) -> None:
            nonlocal last_event_at
            if event.is_directory:
                return
            path = Path(event.src_path)
            if path.suffix.lower() != ".md":
                return
            if should_skip_markdown(path, config.vault_root, config.exclude_dirs):
                return
            changed_paths.add(path.resolve())
            last_event_at = time.monotonic()

    def _handle_signal(signum, frame):  # type: ignore[no-untyped-def]
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    observer = Observer()
    handler = Handler()
    observer.schedule(handler, str(config.vault_root), recursive=True)
    observer.start()

    try:
        # Initial pass to bootstrap notes and queue status.
        run_import(config, dry_run=False)
        sync_queue(config)

        while not stop:
            now = time.monotonic()
            if changed_paths and last_event_at and now - last_event_at >= debounce:
                batch = set(changed_paths)
                changed_paths.clear()
                run_import(config, dry_run=False, changed_paths=batch)
                sync_queue(config)
            time.sleep(0.25)
    finally:
        observer.stop()
        observer.join(timeout=3)

    return 0

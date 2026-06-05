"""Generic markdown folder import backend."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger.importers.state import backend_state_dir, load_json_state, save_json_state
from ledger.importers.types import DoctorResult, ImportOptions, ImportResult


class FolderBackend:
    """Import backend for a generic markdown folder tree."""

    name = "folder"

    def __init__(self, root: Path) -> None:
        self.root = root

    def doctor(self) -> DoctorResult:
        exists = self.root.is_dir()
        checks: dict[str, Any] = {"root_exists": exists}
        errors: tuple[str, ...] = () if exists else (f"root not found: {self.root}",)
        return DoctorResult(backend=self.name, ok=exists, checks=checks, errors=errors)

    def import_once(
        self,
        options: ImportOptions,
        *,
        dest: Path | None = None,
        state_dir: Path | None = None,
    ) -> ImportResult:
        """Copy markdown files from ``options.root`` into the ledger inbox.

        ``dest`` overrides the inbox directory; when omitted the ledger config
        supplies ``ledger_notes_dir/00_inbox``.  ``dry_run=True`` skips all
        writes and config access, returning the file count as ``imported``.

        A run summary is recorded in the shared adapter-state root
        (``<notes_dir>/08_indices/importers/folder/state.json``).  ``state_dir``
        overrides that location; when both ``dest`` and ``state_dir`` are
        omitted it is derived from the ledger config, and when only ``dest``
        is overridden no state is written.
        """
        if not self.root.is_dir():
            return ImportResult(
                backend=self.name,
                errors=(f"root not found: {self.root}",),
            )

        files = sorted(options.root.rglob("*.md"))

        if options.dry_run:
            return ImportResult(
                backend=self.name,
                scanned=len(files),
                imported=len(files),
                skipped=0,
            )

        if dest is None:
            from ledger.config import get_config
            notes_dir = get_config().ledger_notes_dir
            dest = notes_dir / "00_inbox"
            if state_dir is None:
                state_dir = backend_state_dir(notes_dir, self.name)

        imported = 0
        skipped = 0
        errors_list: list[str] = []

        for f in files:
            rel = f.relative_to(self.root)
            target = dest / rel
            if target.exists():
                skipped += 1
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(f.read_bytes())
            except OSError as exc:
                errors_list.append(f"failed to copy {f.name}: {exc}")
                continue
            imported += 1

        result = ImportResult(
            backend=self.name,
            scanned=len(files),
            imported=imported,
            skipped=skipped,
            errors=tuple(errors_list),
        )

        if state_dir is not None:
            self._record_run(state_dir, result)

        return result

    def _record_run(self, state_dir: Path, result: ImportResult) -> None:
        """Persist a run summary in the shared adapter-state root."""
        state_path = state_dir / "state.json"
        state = load_json_state(state_path)
        state.update(
            {
                "version": 1,
                "root": str(self.root),
                "last_run": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "scanned": result.scanned,
                "imported": result.imported,
                "skipped": result.skipped,
            }
        )
        save_json_state(state_path, state)

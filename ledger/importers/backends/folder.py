"""Generic markdown folder import backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    ) -> ImportResult:
        """Copy markdown files from ``options.root`` into the ledger inbox.

        ``dest`` overrides the inbox directory; when omitted the ledger config
        supplies ``ledger_notes_dir/00_inbox``.  ``dry_run=True`` skips all
        writes and config access, returning the file count as ``imported``.
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
            dest = get_config().ledger_notes_dir / "00_inbox"

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

        return ImportResult(
            backend=self.name,
            scanned=len(files),
            imported=imported,
            skipped=skipped,
            errors=tuple(errors_list),
        )

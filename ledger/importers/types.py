"""Shared types for import adapter backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class ImportOptions:
    """Common options for a one-shot import run."""

    root: Path
    dry_run: bool = False


@dataclass(frozen=True)
class ImportResult:
    """Summary returned by backend import operations."""

    backend: str
    scanned: int = 0
    imported: int = 0
    skipped: int = 0
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class DoctorResult:
    """Health-check result returned by import backends."""

    backend: str
    ok: bool
    checks: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


@runtime_checkable
class ImportBackend(Protocol):
    """Minimal contract shared by import backends."""

    name: str

    def doctor(self) -> DoctorResult:
        """Check backend prerequisites and configuration."""

    def import_once(self, options: ImportOptions) -> ImportResult:
        """Run a single import pass."""

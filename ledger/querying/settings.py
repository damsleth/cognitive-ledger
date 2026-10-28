"""Settings operations for query."""

from __future__ import annotations

from pathlib import Path
from ledger.config import get_config


def _detailed_reasons_limit() -> int:
    return get_config().detailed_reasons_limit



def _aliases_path(aliases_path: str | Path | None) -> Path:
    if aliases_path is None:
        return get_config().aliases_path
    return Path(aliases_path)


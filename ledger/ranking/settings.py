"""Settings operations for retrieval."""

from __future__ import annotations

import datetime as dt
from ledger.config import get_config
import os


def _cfg():
    return get_config()



def now_utc() -> dt.datetime:
    """Get current UTC datetime."""
    return dt.datetime.now(tz=dt.timezone.utc)



def parse_ts(value: str) -> dt.datetime | None:
    """Parse ISO 8601 timestamp to datetime."""
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None



def resolve_retrieval_mode(retrieval_mode: str | None) -> str:
    """Resolve retrieval mode from argument, environment, or config."""
    config = get_config()
    if retrieval_mode is None:
        retrieval_mode = os.getenv("LEDGER_RETRIEVAL_MODE") or config.retrieval_mode
    mode = str(retrieval_mode or "").strip().lower() or "semantic_hybrid"
    if mode not in config.retrieval_modes:
        return "semantic_hybrid"
    return mode



def resolve_embed_backend(embed_backend: str | None) -> str:
    """Resolve embedding backend from argument, environment, or config."""
    config = get_config()
    if embed_backend is None:
        embed_backend = os.getenv("LEDGER_EMBED_BACKEND") or config.embed_backend
    backend = str(embed_backend or "").strip().lower() or "local"
    if backend not in config.embed_backends:
        return "local"
    return backend


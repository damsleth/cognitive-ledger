"""Cognitive Ledger - File-based memory for AI agents.

This package provides retrieval, scoring, and management utilities
for the Cognitive Ledger note system.
"""

from .config import get_config, reset_config, LedgerConfig
from .retrieval_types import (
    RetrievalCandidate,
    RetrievalResult,
    ScoredResult,
    ScoreComponents,
    TimingInfo,
)

__version__ = "0.4.1"

from .cli import main  # noqa: E402

__all__ = [
    "main",
    "get_config",
    "reset_config",
    "LedgerConfig",
    "RetrievalCandidate",
    "RetrievalResult",
    "ScoredResult",
    "ScoreComponents",
    "TimingInfo",
    "__version__",
]

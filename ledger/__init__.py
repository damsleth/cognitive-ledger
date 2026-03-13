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

__version__ = "0.2.0"
__all__ = [
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

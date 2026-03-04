"""Cognitive Ledger - File-based memory for AI agents.

This package provides retrieval, scoring, and management utilities
for the Cognitive Ledger note system.
"""

from .config import get_config, reset_config, LedgerConfig

__version__ = "0.2.0"
__all__ = ["get_config", "reset_config", "LedgerConfig", "__version__"]

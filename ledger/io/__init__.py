"""File I/O utilities for Cognitive Ledger.

This module provides safe file operations including atomic writes
and file locking to prevent data corruption.
"""

from ledger.io.safe_write import (
    FileLock,
    atomic_write,
    safe_write_text,
    safe_read_text,
    safe_append_line,
    append_timeline_entry,
)

__all__ = [
    "FileLock",
    "atomic_write",
    "safe_write_text",
    "safe_read_text",
    "safe_append_line",
    "append_timeline_entry",
]

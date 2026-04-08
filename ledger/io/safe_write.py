"""Safe file writing utilities for Cognitive Ledger.

This module provides atomic file writes and file locking to prevent
race conditions and data corruption when multiple processes access
notes concurrently.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ledger.errors import NoteWriteError, NoteReadError


class FileLock:
    """Advisory file lock context manager.

    Uses POSIX fcntl locking for cross-process coordination.
    The lock is advisory, meaning processes must cooperate.

    Usage:
        with FileLock(path):
            # ... safely modify file ...

    Note:
        - Creates a .lock file alongside the target file
        - Lock is released on context exit or interpreter crash
        - Blocking by default (waits for lock; use blocking=False to raise immediately)
        - Lock files are kept for debugging; advisory lock handles contention
    """

    def __init__(
        self,
        path: Path | str,
        timeout: float | None = None,
        blocking: bool = True,
    ) -> None:
        """Initialize the file lock.

        Args:
            path: Path to the file to lock.
            timeout: Not implemented (reserved for future use).
            blocking: If True, wait for lock; if False, raise immediately.
        """
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.blocking = blocking
        self._fd: int | None = None

    def __enter__(self) -> "FileLock":
        """Acquire the file lock."""
        # Create parent directories if needed
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Open lock file (create if needed)
        self._fd = os.open(
            str(self.lock_path),
            os.O_RDWR | os.O_CREAT,
            0o644,
        )

        try:
            flags = fcntl.LOCK_EX
            if not self.blocking:
                flags |= fcntl.LOCK_NB
            fcntl.flock(self._fd, flags)
        except BlockingIOError:
            os.close(self._fd)
            self._fd = None
            raise NoteWriteError(
                str(self.path),
                "file is locked by another process",
            )

        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Release the file lock."""
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
            # Note: we intentionally keep the lock file for debugging.
            # Advisory locking handles contention; the file is just a handle.


@contextmanager
def atomic_write(
    path: Path | str,
    mode: str = "w",
    encoding: str = "utf-8",
) -> Iterator:
    """Context manager for atomic file writes.

    Writes to a temporary file in the same directory, then atomically
    renames to the target path. This ensures the target file is never
    in a partially-written state.

    Usage:
        with atomic_write(path) as f:
            f.write("content")
        # File is atomically replaced on success

    Args:
        path: Target file path.
        mode: File mode ('w' or 'wb').
        encoding: Text encoding (ignored for binary mode).

    Yields:
        File handle for writing.

    Raises:
        NoteWriteError: If the write fails.
    """
    path = Path(path)

    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create temp file in same directory (for atomic rename)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )

    # Take ownership of fd immediately so cleanup is reliable even if
    # os.fdopen itself raises (e.g. resource limit, invalid mode).
    try:
        f_obj = os.fdopen(fd, mode, encoding=encoding if "b" not in mode else None)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    try:
        with f_obj as f:
            yield f
            # Flush to OS buffer
            f.flush()
            # fsync to disk for crash durability
            os.fsync(f.fileno())

        # Atomic rename (POSIX guarantees atomicity within same filesystem)
        os.replace(tmp_path, path)

        # fsync the directory to ensure rename is durable
        dir_fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    except Exception as e:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise NoteWriteError(str(path), str(e)) from e


def safe_write_text(
    path: Path | str,
    content: str,
    encoding: str = "utf-8",
    use_lock: bool = True,
) -> None:
    """Safely write text content to a file.

    Combines file locking and atomic writes for maximum safety.

    Args:
        path: Target file path.
        content: Text content to write.
        encoding: Text encoding.
        use_lock: Whether to use file locking.

    Raises:
        NoteWriteError: If the write fails.
    """
    path = Path(path)

    if use_lock:
        with FileLock(path):
            with atomic_write(path, encoding=encoding) as f:
                f.write(content)
    else:
        with atomic_write(path, encoding=encoding) as f:
            f.write(content)


def safe_read_text(
    path: Path | str,
    encoding: str = "utf-8",
    use_lock: bool = False,
) -> str:
    """Safely read text content from a file.

    Args:
        path: Source file path.
        encoding: Text encoding.
        use_lock: Whether to use file locking (usually not needed for reads).

    Returns:
        File content as string.

    Raises:
        NoteReadError: If the read fails.
    """
    path = Path(path)

    try:
        if use_lock:
            with FileLock(path):
                return path.read_text(encoding=encoding)
        return path.read_text(encoding=encoding)
    except FileNotFoundError:
        raise NoteReadError(str(path), "file not found")
    except PermissionError:
        raise NoteReadError(str(path), "permission denied")
    except Exception as e:
        raise NoteReadError(str(path), str(e)) from e


def ensure_parent_dir(path: Path | str) -> Path:
    """Ensure parent directory exists.

    Args:
        path: File path whose parent should exist.

    Returns:
        The path as a Path object.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def safe_append_line(
    path: Path | str,
    line: str,
    encoding: str = "utf-8",
) -> None:
    """Safely append a line to a file with locking.

    This is the canonical way to append to timeline.md and similar files.
    Uses file locking to prevent race conditions in concurrent appends.

    Args:
        path: Target file path.
        line: Line to append (newline added if not present).
        encoding: Text encoding.

    Raises:
        NoteWriteError: If the append fails.
    """
    path = Path(path)

    # Ensure line ends with newline
    if not line.endswith("\n"):
        line = line + "\n"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(path):
            with open(path, "a", encoding=encoding) as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
    except Exception as e:
        raise NoteWriteError(str(path), str(e)) from e


def append_timeline_entry(
    timeline_path: Path | str,
    action: str,
    note_path: Path | str,
    description: str,
    root_dir: Path | None = None,
    ledger_notes_dir: Path | None = None,
) -> None:
    """Append an entry to the timeline with proper locking.

    This is THE canonical function for all timeline writes.
    All code paths should use this instead of direct file appends.

    Args:
        timeline_path: Path to timeline.md
        action: Action type (created|updated|archived|deleted|closed|sleep)
        note_path: Path to the affected note
        description: Brief description of the change
        root_dir: Root directory for making paths relative (optional)
        ledger_notes_dir: Ledger notes dir for normalizing note paths to notes/... (optional)

    Raises:
        NoteWriteError: If the append fails.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Make path relative to root if possible
    from ledger.layout import logical_path

    note_path = logical_path(
        note_path,
        ledger_root=root_dir,
        ledger_notes_dir=ledger_notes_dir,
    )

    event = {
        "ts": timestamp,
        "action": action,
        "path": str(note_path),
        "desc": description,
    }

    timeline_path = Path(timeline_path)
    from ledger.timeline import append_timeline_jsonl

    # New source of truth: timeline.jsonl
    timeline_jsonl_path = timeline_path.with_name("timeline.jsonl")
    append_timeline_jsonl(timeline_jsonl_path, event)

    # Compatibility append for existing markdown consumers.
    entry = f"{timestamp} | {action} | {note_path} | {description}"
    safe_append_line(timeline_path, entry)

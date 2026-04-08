"""Input validation utilities for Cognitive Ledger.

This module provides validators for user input at CLI entry points
to prevent issues like oversized queries, directory traversal, etc.
"""

from __future__ import annotations

import os
from pathlib import Path

from ledger.config import get_config
from ledger.errors import (
    QueryValidationError,
    ScopeValidationError,
    PathValidationError,
)


# Valid scopes as defined by schema
VALID_SCOPES = frozenset({"home", "work", "dev", "personal", "life", "meta"})
VALID_SCOPES_WITH_ALL = VALID_SCOPES | {"all"}


def validate_query(query: str, max_length: int = 2000) -> str:
    """Validate a query string for safety and sanity.

    Args:
        query: The query string to validate.
        max_length: Maximum allowed length (default 2000 chars).

    Returns:
        The validated query string (stripped).

    Raises:
        QueryValidationError: If the query is invalid.
    """
    if query is None:
        raise QueryValidationError("", "query cannot be None")

    # Strip whitespace
    query = str(query).strip()

    # Empty check
    if not query:
        raise QueryValidationError(query, "query cannot be empty")

    # Length check
    if len(query) > max_length:
        raise QueryValidationError(
            query,
            f"query too long ({len(query)} chars, max {max_length})",
        )

    # Null bytes
    if "\x00" in query:
        raise QueryValidationError(query, "query contains null bytes")

    # Check for valid UTF-8 encoding
    try:
        query.encode("utf-8")
    except UnicodeEncodeError as e:
        raise QueryValidationError(query, f"invalid encoding: {e}") from e

    return query


def validate_scope(scope: str, allow_all: bool = True) -> str:
    """Validate a scope value.

    Args:
        scope: The scope string to validate.
        allow_all: Whether to allow 'all' as a valid scope.

    Returns:
        The validated scope (lowercase).

    Raises:
        ScopeValidationError: If the scope is invalid.
    """
    if scope is None:
        raise ScopeValidationError("None")

    normalized = str(scope).strip().lower()

    # Handle alias
    if normalized == "life":
        normalized = "personal"

    valid = VALID_SCOPES_WITH_ALL if allow_all else VALID_SCOPES

    if normalized not in valid:
        raise ScopeValidationError(
            scope,
            valid_scopes=tuple(sorted(valid)),
        )

    return normalized


def validate_limit(limit: int, min_val: int = 1, max_val: int = 1000) -> int:
    """Validate a limit/count parameter.

    Args:
        limit: The limit value to validate.
        min_val: Minimum allowed value (inclusive).
        max_val: Maximum allowed value (inclusive).

    Returns:
        The validated limit.

    Raises:
        ValueError: If the limit is out of bounds.
    """
    try:
        limit = int(limit)
    except (TypeError, ValueError) as e:
        raise ValueError(f"limit must be an integer, got {type(limit).__name__}") from e

    if limit < min_val:
        raise ValueError(f"limit must be at least {min_val}, got {limit}")

    if limit > max_val:
        raise ValueError(f"limit cannot exceed {max_val}, got {limit}")

    return limit


def validate_path_within_root(
    path: str | Path,
    root: Path | None = None,
) -> Path:
    """Validate that a path is within the allowed root directory.

    Prevents directory traversal attacks by ensuring the resolved
    path is within the configured notes directory.

    Args:
        path: The path to validate.
        root: The root directory (defaults to config.ledger_notes_dir).

    Returns:
        The resolved, validated Path object.

    Raises:
        PathValidationError: If the path is invalid or escapes root.
    """
    # Check for null bytes early (before Path operations)
    path_str = str(path)
    if "\x00" in path_str:
        raise PathValidationError(path_str, "path contains null bytes")

    if root is None:
        root = get_config().ledger_notes_dir

    root = root.resolve()

    # Convert to Path
    try:
        path = Path(path)
    except Exception as e:
        raise PathValidationError(path_str, f"invalid path format: {e}") from e

    # Resolve to absolute path
    try:
        if path.is_absolute():
            resolved = path.resolve()
        else:
            resolved = (root / path).resolve()
    except (OSError, ValueError) as e:
        raise PathValidationError(path_str, f"cannot resolve path: {e}") from e

    # Check for symlink escape
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathValidationError(
            path_str,
            f"path escapes root directory ({root})",
        )

    return resolved


def validate_note_type(note_type: str) -> str:
    """Validate a note type string.

    Args:
        note_type: The note type to validate.

    Returns:
        The validated note type (lowercase).

    Raises:
        ValueError: If the note type is invalid.
    """
    from ledger.notes import CORE_NOTE_TYPES

    if note_type is None:
        raise ValueError("note_type cannot be None")

    normalized = str(note_type).strip().lower()

    # Allow 'all' as special value
    if normalized == "all":
        return normalized

    if normalized not in CORE_NOTE_TYPES:
        valid_str = ", ".join(CORE_NOTE_TYPES)
        raise ValueError(
            f"Invalid note type: {note_type!r}. Valid types: {valid_str}, all"
        )

    return normalized


def validate_status(status: str) -> str:
    """Validate a loop status string.

    Args:
        status: The status to validate.

    Returns:
        The validated status (lowercase).

    Raises:
        ValueError: If the status is invalid.
    """
    from ledger.notes import LOOP_STATUSES

    if status is None:
        raise ValueError("status cannot be None")

    normalized = str(status).strip().lower()

    # Allow 'all' as special value
    if normalized == "all":
        return normalized

    if normalized not in LOOP_STATUSES:
        valid_str = ", ".join(LOOP_STATUSES)
        raise ValueError(
            f"Invalid status: {status!r}. Valid values: {valid_str}, all"
        )

    return normalized


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Sanitize a string for use as a filename.

    Args:
        name: The name to sanitize.
        max_length: Maximum filename length.

    Returns:
        A safe filename string.
    """
    # Remove/replace problematic characters
    safe = ""
    for char in name.lower():
        if char.isalnum() or char in "-_":
            safe += char
        elif char.isspace():
            safe += "_"
        # Skip other characters

    # Remove leading/trailing underscores
    safe = safe.strip("_")

    # Collapse multiple underscores
    while "__" in safe:
        safe = safe.replace("__", "_")

    # Truncate if needed
    if len(safe) > max_length:
        safe = safe[:max_length].rstrip("_")

    # Ensure not empty
    if not safe:
        safe = "untitled"

    return safe

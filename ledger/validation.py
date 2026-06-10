"""Input validation utilities for Cognitive Ledger.

This module provides validators for user input at CLI entry points
to prevent issues like oversized queries, directory traversal, etc.

It also exposes pure heuristic functions for frontmatter and note-body
validation, extracted from ledger/maintenance.py so they can be called
independently (e.g. from tests or from other tools).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from ledger.config import get_config
from ledger.errors import (
    QueryValidationError,
    ScopeValidationError,
    PathValidationError,
)
from ledger.schema_values import (
    LANG_VALUES,
    QUERY_SCOPE_VALUES,
    SCOPE_VALUES,
    SOURCE_VALUES,
    STATUS_VALUES,
    VIA_VALUES,
)


# Re-exported under the legacy names for backwards-compat with callers
# that import from this module rather than schema_values directly.
VALID_SCOPES = SCOPE_VALUES
VALID_SCOPES_WITH_ALL = QUERY_SCOPE_VALUES


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


# ---------------------------------------------------------------------------
# Frontmatter heuristic helpers (extracted from maintenance._lint_note)
# ---------------------------------------------------------------------------

_TAG_PATTERN = re.compile(r"^[a-zæøå][a-zæøå0-9_-]*$")
_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
)
_REQUIRED_FIELDS = [
    "created", "updated", "tags", "confidence", "source", "scope", "lang"
]


def infer_note_type_from_path(rel_path: str) -> str | None:
    """Infer the logical note type from the relative path component.

    Returns a string like ``"open_loop"``, ``"inbox"``, or ``None`` when the
    folder is not recognised.  This is intentionally a *heuristic* — callers
    should not rely on exhaustive coverage.
    """
    normed = f"/{rel_path}".replace("\\", "/")
    if "/00_inbox/" in normed:
        return "inbox"
    if "/01_identity/" in normed:
        return "identity"
    if "/02_facts/" in normed:
        return "fact"
    if "/03_preferences/" in normed:
        return "preference"
    if "/04_goals/" in normed:
        return "goal"
    if "/05_open_loops/" in normed:
        return "open_loop"
    if "/06_concepts/" in normed:
        return "concept"
    if "/07_reflections/" in normed:
        return "reflection"
    if "/08_people/" in normed:
        return "person"
    if "/09_archive/" in normed:
        return "archive"
    return None


def validate_frontmatter_fields(
    frontmatter: dict[str, Any],
    rel_path: str = "",
) -> list[str]:
    """Check a parsed frontmatter dict for required fields and value constraints.

    Args:
        frontmatter: Dict of frontmatter key/value pairs (already parsed).
        rel_path: Relative path of the note (used to detect open-loop notes).

    Returns:
        A list of human-readable issue strings.  Empty list means no issues.
    """
    issues: list[str] = []

    # --- required fields ---
    for field in _REQUIRED_FIELDS:
        if not frontmatter.get(field) and field not in frontmatter:
            issues.append(f"missing '{field}'")

    is_loop = "/05_open_loops/" in f"/{rel_path}"
    if is_loop and "status" not in frontmatter:
        issues.append("open loop missing 'status'")

    # --- timestamp format ---
    created = str(frontmatter.get("created", "")).strip()
    if created and not _TIMESTAMP_PATTERN.match(created):
        issues.append(f"invalid created timestamp: {created}")

    updated = str(frontmatter.get("updated", "")).strip()
    if updated and not _TIMESTAMP_PATTERN.match(updated):
        issues.append(f"invalid updated timestamp: {updated}")

    # --- enum fields ---
    source = str(frontmatter.get("source", "")).strip().lower()
    if source and source not in SOURCE_VALUES:
        issues.append(f"invalid source: {source}")

    scope = str(frontmatter.get("scope", "")).strip().lower()
    if scope and scope not in SCOPE_VALUES:
        issues.append(f"invalid scope: {scope}")

    lang = str(frontmatter.get("lang", "")).strip().lower()
    if lang and lang not in LANG_VALUES:
        issues.append(f"invalid lang: {lang}")

    status = str(frontmatter.get("status", "")).strip().lower()
    if is_loop and status and status not in STATUS_VALUES:
        issues.append(f"invalid status: {status}")

    via = str(frontmatter.get("via", "")).strip().lower()
    if via and via not in VIA_VALUES:
        issues.append(f"invalid via: {via}")

    # --- tags ---
    raw_tags = frontmatter.get("tags")
    if isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags]
    elif raw_tags:
        tags = [str(raw_tags).strip()]
    else:
        tags = []
    for tag in tags:
        if tag and not _TAG_PATTERN.match(tag):
            issues.append(f"invalid tag: {tag}")

    # --- confidence ---
    confidence_raw = frontmatter.get("confidence", "")
    try:
        confidence = float(confidence_raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None:
        if confidence < 0 or confidence > 1:
            issues.append(f"confidence out of range: {confidence_raw}")
        if source == "inferred" and confidence > 0.8:
            issues.append(
                f"inferred note has high confidence ({confidence} > 0.8) [warning]"
            )

    return issues


def validate_note_body(
    body: str,
    frontmatter: dict[str, Any],
    rel_path: str = "",
    large_file_word_threshold: int = 400,
) -> list[str]:
    """Check a note's markdown body for structural heuristic issues.

    Args:
        body: The markdown body text (everything after the frontmatter block).
        frontmatter: Parsed frontmatter dict (for tags / status lookups).
        rel_path: Relative path of the note (used to detect open-loop notes).
        large_file_word_threshold: Warn when the full body exceeds this many words.

    Returns:
        A list of human-readable issue strings.  Empty list means no issues.
    """
    from ledger.parsing import parse_sections, normalize_tags

    issues: list[str] = []

    # --- large file ---
    words = len(body.split())
    if words > large_file_word_threshold:
        issues.append(f"large file ({words} words) [warning]")

    # --- open-loop structural checks ---
    is_open_loop = (
        "/05_open_loops/" in f"/{rel_path}"
        and str(frontmatter.get("status", "open")).strip().lower() == "open"
    )
    sections = parse_sections(body)
    if is_open_loop:
        next_action_lines = sections.get("next action")
        if not next_action_lines:
            issues.append("open loop missing '## Next action' section [warning]")
        else:
            has_checkbox = any(
                re.match(r"^\s*-\s*\[[ xX]\]\s+", line or "") is not None
                for line in next_action_lines
            )
            if not has_checkbox:
                issues.append(
                    "open loop has no checkbox action in Next action section [warning]"
                )

    # --- placeholder link bullets ---
    links_lines = sections.get("links", [])
    placeholder_link = any(
        re.match(r"^\s*-\s*$", line or "") for line in links_lines
    )
    if placeholder_link:
        issues.append("placeholder bullet found in Links section [warning]")

    # --- synthesized notes must have outgoing links ---
    tags = normalize_tags(frontmatter.get("tags"))
    if "synthesized" in tags:
        from ledger.parsing.links import extract_links

        note_links = extract_links(body)
        if not note_links:
            issues.append(
                "synthesized note has no outgoing links (should reference sources) [warning]"
            )

    return issues


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

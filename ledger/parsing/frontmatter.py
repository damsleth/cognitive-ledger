"""Canonical YAML frontmatter parser for Cognitive Ledger.

This is THE source of truth for frontmatter parsing.
Do not duplicate this logic elsewhere.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


FRONTMATTER_BOUNDARY = re.compile(r"^---\s*$")


@dataclass
class ParsedFrontmatter:
    """Structured frontmatter with typed fields."""

    created: datetime | None
    updated: datetime | None
    tags: list[str]
    confidence: float
    source: str
    scope: str
    lang: str
    status: str | None  # loops only
    raw: dict[str, Any]  # Original parsed dict for extension


def strip_quotes(value: str) -> str:
    """Remove surrounding quotes from a YAML scalar.

    Args:
        value: String possibly wrapped in quotes.

    Returns:
        The unquoted string.

    Examples:
        >>> strip_quotes('"hello"')
        'hello'
        >>> strip_quotes("'world'")
        'world'
        >>> strip_quotes('plain')
        'plain'
    """
    if len(value) >= 2:
        if (value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'"):
            return value[1:-1]
    return value


def strip_inline_comment(value: str) -> str:
    """Strip YAML-style inline comments while respecting quoted strings.

    Args:
        value: YAML line value portion.

    Returns:
        The value with inline comment stripped.

    Examples:
        >>> strip_inline_comment('value # comment')
        'value'
        >>> strip_inline_comment('"value # not comment"')
        '"value # not comment"'
    """
    in_single = False
    in_double = False
    escaped = False

    for idx, ch in enumerate(value):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if ch == "#" and not in_single and not in_double:
            return value[:idx].rstrip()
    return value


def parse_inline_list(value: str) -> list[str]:
    """Parse YAML flow-style list: [a, b, c] -> ['a', 'b', 'c'].

    Args:
        value: String in YAML flow-style list format.

    Returns:
        List of parsed items.

    Examples:
        >>> parse_inline_list('[a, b, c]')
        ['a', 'b', 'c']
        >>> parse_inline_list('["quoted", plain]')
        ['quoted', 'plain']
    """
    inner = value[1:-1].strip()
    if not inner:
        return []
    reader = csv.reader([inner], skipinitialspace=True)
    return [strip_quotes(item.strip()) for item in next(reader) if item.strip()]


def parse_scalar(value: str) -> str | int | float | bool | list[str]:
    """Parse a YAML scalar value with type inference.

    Args:
        value: Raw YAML value string.

    Returns:
        Parsed value with appropriate type.

    Examples:
        >>> parse_scalar('42')
        42
        >>> parse_scalar('3.14')
        3.14
        >>> parse_scalar('true')
        True
        >>> parse_scalar('[a, b]')
        ['a', 'b']
    """
    cleaned = strip_inline_comment(value).strip()
    if not cleaned:
        return ""

    # Inline list
    if cleaned.startswith("[") and cleaned.endswith("]"):
        return parse_inline_list(cleaned)

    cleaned = strip_quotes(cleaned)

    # Boolean
    if cleaned.lower() in ("true", "yes"):
        return True
    if cleaned.lower() in ("false", "no"):
        return False

    # Number
    try:
        return float(cleaned) if "." in cleaned else int(cleaned)
    except ValueError:
        pass

    return cleaned


def parse_frontmatter_lines(lines: list[str]) -> dict[str, Any]:
    """Parse YAML frontmatter lines into a dictionary.

    This is the canonical implementation. Use this everywhere.

    Args:
        lines: List of frontmatter lines (without --- delimiters).

    Returns:
        Dictionary of parsed frontmatter fields.
    """
    data: dict[str, Any] = {}
    current_list_key: str | None = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            continue

        # List item continuation
        list_item_match = re.match(r"^\s*-\s+(.*)$", line)
        if list_item_match and current_list_key is not None:
            item = parse_scalar(list_item_match.group(1))
            if isinstance(item, list):
                data[current_list_key].extend(item)
            elif item != "":
                data[current_list_key].append(item)
            continue

        # Key-value pair
        key_match = re.match(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$", stripped)
        if not key_match:
            current_list_key = None
            continue

        key, value = key_match.group(1), key_match.group(2)
        if value is None or value == "":
            data[key] = []
            current_list_key = key
            continue

        data[key] = parse_scalar(value)
        current_list_key = None

    return data


def parse_frontmatter_text(text: str) -> tuple[dict[str, Any], str]:
    """Parse frontmatter from full file text.

    Args:
        text: Full markdown file content.

    Returns:
        Tuple of (frontmatter_dict, body_text).
    """
    lines = text.splitlines()
    if not lines or not FRONTMATTER_BOUNDARY.match(lines[0]):
        return {}, text

    boundary_idx = None
    for idx in range(1, len(lines)):
        if FRONTMATTER_BOUNDARY.match(lines[idx]):
            boundary_idx = idx
            break

    if boundary_idx is None:
        return {}, text

    fm_dict = parse_frontmatter_lines(lines[1:boundary_idx])
    body = "\n".join(lines[boundary_idx + 1 :])
    return fm_dict, body


def parse_timestamp(value: str) -> datetime | None:
    """Parse ISO 8601 timestamp.

    Args:
        value: Timestamp string in ISO 8601 format.

    Returns:
        Parsed datetime or None if invalid.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_tags(raw_tags: Any) -> list[str]:
    """Normalize tags to a clean list of lowercase strings.

    Args:
        raw_tags: Raw tags value from frontmatter.

    Returns:
        List of normalized tag strings.
    """
    if raw_tags is None:
        return []

    if isinstance(raw_tags, str):
        values: list[Any] = [raw_tags]
    elif isinstance(raw_tags, list):
        values = raw_tags
    else:
        return []

    tags: list[str] = []
    seen: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            continue

        stripped = value.strip()
        if not stripped:
            continue

        # Handle embedded lists
        if stripped.startswith("[") and stripped.endswith("]"):
            candidates = parse_inline_list(stripped)
        elif "," in stripped:
            candidates = [part.strip() for part in stripped.split(",")]
        else:
            candidates = [stripped]

        for candidate in candidates:
            tag = strip_quotes(candidate.strip()).lower()
            if tag and tag not in seen:
                seen.add(tag)
                tags.append(tag)

    return tags


def to_parsed_frontmatter(raw: dict[str, Any]) -> ParsedFrontmatter:
    """Convert raw dict to structured ParsedFrontmatter.

    Args:
        raw: Dictionary from parse_frontmatter_lines.

    Returns:
        Structured ParsedFrontmatter dataclass.
    """
    return ParsedFrontmatter(
        created=parse_timestamp(str(raw.get("created", ""))),
        updated=parse_timestamp(str(raw.get("updated", ""))),
        tags=normalize_tags(raw.get("tags")),
        confidence=float(raw.get("confidence", 0.5)) if raw.get("confidence") else 0.5,
        source=str(raw.get("source", "")).strip().lower(),
        scope=str(raw.get("scope", "")).strip().lower(),
        lang=str(raw.get("lang", "en")),
        status=str(raw.get("status", "")).strip().lower() or None,
        raw=raw,
    )

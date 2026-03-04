"""Markdown section parsing for Cognitive Ledger.

This module provides functions for extracting structured sections
from markdown note bodies.
"""

from __future__ import annotations

import re
import textwrap


# Aliases for section name normalization
SECTION_ALIASES = {
    "next actions": "next action",
    "next steps": "next action",
    "next step": "next action",
    "question task": "question or task",
    "why matters": "why it matters",
}


def normalize_section_name(name: str) -> str:
    """Normalize a section name to canonical form.

    Args:
        name: Raw section header text.

    Returns:
        Normalized section name.

    Examples:
        >>> normalize_section_name("Next Actions")
        'next action'
        >>> normalize_section_name("Why matters")
        'why it matters'
    """
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return SECTION_ALIASES.get(normalized, normalized)


def parse_sections(body: str) -> dict[str, list[str]]:
    """Parse markdown body into sections keyed by normalized header names.

    Args:
        body: Markdown content body (without frontmatter).

    Returns:
        Dictionary mapping normalized section names to lists of lines.

    Note:
        Repeated headers with the same normalized name append to the
        existing section rather than overwriting it.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in body.splitlines():
        if line.startswith("## "):
            current = normalize_section_name(line[3:].strip())
            # Append to existing section instead of overwrite
            if current not in sections:
                sections[current] = []
            continue
        if line.startswith("### "):
            current = normalize_section_name(line[4:].strip())
            # Append to existing section instead of overwrite
            if current not in sections:
                sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)

    return sections


def extract_title(body: str) -> str:
    """Extract H1 title from markdown body.

    Args:
        body: Markdown content body.

    Returns:
        Title text, or empty string if not found.
        Strips "Loop:" prefix if present.
    """
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            # Strip "Loop:" prefix for loops
            if title.lower().startswith("loop:"):
                title = title[5:].strip()
            return title
    return ""


def first_content_line(body: str) -> str:
    """Get first meaningful content line from body.

    Skips headers, blank lines, and extracts list item text.

    Args:
        body: Markdown content body.

    Returns:
        First meaningful line of content.
    """
    for line in body.splitlines():
        if line.startswith("#"):
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            return stripped[2:].strip()
        return stripped
    return ""


def first_checkbox(text: str) -> str:
    """Find the first checkbox item in text.

    Args:
        text: Text containing possible checkbox items.

    Returns:
        Text of the first checkbox item, or empty string.
    """
    for line in text.splitlines():
        match = re.match(r"\s*-\s*\[[ xX]\]\s+(.*)", line)
        if match:
            return match.group(1).strip()
    return ""


def shorten(text: str, width: int, placeholder: str = "...") -> str:
    """Shorten text to fit within width.

    Args:
        text: Text to shorten.
        width: Maximum width.
        placeholder: Placeholder for truncated text.

    Returns:
        Shortened text.
    """
    if width <= 0:
        return ""
    return textwrap.shorten(" ".join(text.split()), width=width, placeholder=placeholder)

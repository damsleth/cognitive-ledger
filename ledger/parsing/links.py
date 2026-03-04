"""Link extraction for Cognitive Ledger.

This module provides functions for extracting wiki-style and
markdown links from note bodies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .tokenizer import tokenize


@dataclass
class NoteLink:
    """A link from one note to another."""

    target: str  # filename or relative path
    line_number: int
    is_wiki_link: bool  # [[...]] vs relative path
    display_text: str = ""  # Optional display text


def extract_links(body: str) -> list[NoteLink]:
    """Extract wiki-style [[...]] and relative path links from body.

    Args:
        body: Markdown content body.

    Returns:
        List of NoteLink objects.
    """
    links: list[NoteLink] = []

    for i, line in enumerate(body.splitlines(), 1):
        # Wiki-style links: [[note_name]] or [[note_name|display]]
        for match in re.finditer(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", line):
            target = match.group(1).strip()
            display = match.group(2).strip() if match.group(2) else ""
            links.append(
                NoteLink(
                    target=target,
                    line_number=i,
                    is_wiki_link=True,
                    display_text=display,
                )
            )

        # Markdown links to .md files: [text](path.md) or [text](../path.md)
        for match in re.finditer(r"\[([^\]]+)\]\(([^)]+\.md)\)", line):
            display = match.group(1).strip()
            target = match.group(2).strip()
            links.append(
                NoteLink(
                    target=target,
                    line_number=i,
                    is_wiki_link=False,
                    display_text=display,
                )
            )

    return links


def extract_link_tokens(body: str) -> set[str]:
    """Extract tokens from all links in body for search indexing.

    This captures both the display text and target path components
    to improve search coverage.

    Args:
        body: Markdown content body.

    Returns:
        Set of tokens extracted from links.
    """
    link_tokens: set[str] = set()

    # Standard markdown links [text](target)
    for text, target in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", body):
        link_tokens |= tokenize(text)
        # Normalize path to extract meaningful tokens
        normalized_target = target.replace("/", " ").replace("_", " ").replace("-", " ")
        link_tokens |= tokenize(normalized_target)

    # Wiki-style links [[ref]] or [[ref|display]]
    for wiki_ref in re.findall(r"\[\[([^\]]+)\]\]", body):
        link_tokens |= tokenize(wiki_ref.replace("|", " "))

    return link_tokens

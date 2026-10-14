"""Privacy fence support for content stripping.

Strips private sections from text before content enters candidates,
indexes, or generated artifacts. Two syntaxes are honoured:

- ``<private>...</private>`` tags (inline or multiline)
- fenced code blocks opened with a line starting ```` ```private ```` (or
  ``~~~private``)
"""

from __future__ import annotations

import re

_OPEN_TAG_RE = re.compile(r"<private>", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(r"</private>", re.IGNORECASE)
# A ```private fence runs to the next line that is only the same fence marker
# (or to end of text when unterminated: fail closed, never leak the tail).
# Markdown rules: up to 3 spaces of indent (4+ is an indented code example,
# kept), and CRLF line endings close a fence like LF does.
_PRIVATE_FENCE_RE = re.compile(
    r"^ {0,3}(?P<fence>```|~~~)private\b[^\n]*(?:\n.*?)?(?:\n {0,3}(?P=fence)[ \t]*\r?$|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def strip_private_tags(text: str) -> str:
    """Remove all private sections (``<private>`` tags and ```` ```private ```` fences).

    Tags: handles multiline content, multiple occurrences, nested fences,
    and case-insensitive tags. Uses balanced-tag matching to strip
    outermost fence spans correctly.
    """
    lowered = text.lower()
    if "```private" in lowered or "~~~private" in lowered:
        text = _PRIVATE_FENCE_RE.sub("", text)
        lowered = text.lower()
    if "<private>" not in lowered:
        return text

    result: list[str] = []
    depth = 0
    pos = 0

    while pos < len(text):
        open_match = _OPEN_TAG_RE.search(text, pos)
        close_match = _CLOSE_TAG_RE.search(text, pos)

        if depth == 0:
            if open_match is None:
                # No more opening tags - keep the rest
                result.append(text[pos:])
                break
            # Keep text before the opening tag
            result.append(text[pos:open_match.start()])
            depth = 1
            pos = open_match.end()
        else:
            if close_match is None:
                # Unclosed tag - discard remaining text (inside fence)
                break
            if open_match is not None and open_match.start() < close_match.start():
                # Nested opening tag
                depth += 1
                pos = open_match.end()
            else:
                # Closing tag
                depth -= 1
                pos = close_match.end()

    return "".join(result)

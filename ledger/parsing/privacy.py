"""Privacy fence support for content stripping.

Strips <private>...</private> tagged sections from text before
content enters candidates, indexes, or generated artifacts.
"""

from __future__ import annotations

import re

_OPEN_TAG_RE = re.compile(r"<private>", re.IGNORECASE)
_CLOSE_TAG_RE = re.compile(r"</private>", re.IGNORECASE)


def strip_private_tags(text: str) -> str:
    """Remove all <private>...</private> fenced sections from text.

    Handles multiline content, multiple occurrences, nested fences,
    and case-insensitive tags. Uses balanced-tag matching to strip
    outermost fence spans correctly.
    """
    if "<private>" not in text.lower():
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

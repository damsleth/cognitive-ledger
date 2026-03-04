"""Text tokenization for Cognitive Ledger.

This module provides the canonical tokenization function used
for search indexing and query processing.
"""

from __future__ import annotations

import re

from ..config import get_config


def tokenize(text: str, stopwords: frozenset[str] | None = None) -> set[str]:
    """Tokenize text for search indexing.

    Extracts lowercase alphanumeric tokens, removing stopwords.

    Args:
        text: Text to tokenize.
        stopwords: Optional custom stopwords. Defaults to config stopwords.

    Returns:
        Set of tokens.

    Examples:
        >>> sorted(tokenize("Hello World"))
        ['hello', 'world']
        >>> sorted(tokenize("The quick brown fox"))
        ['brown', 'fox', 'quick']
    """
    if stopwords is None:
        stopwords = get_config().stopwords

    return {
        tok
        for tok in re.findall(r"[a-z0-9]+", text.lower())
        if tok and tok not in stopwords
    }

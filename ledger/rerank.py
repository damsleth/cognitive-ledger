"""Cross-encoder reranker — thin shim over ``memcore.rerank``.

The implementation moved to the separately installable ``memcore`` package
(yaams consumes it too). This module re-exports the full public surface at
the old import path so existing callers are unaffected. ``_RERANKER_CACHE``
is re-exported as well: it is the same dict object, so mutating it here
(as tests do) mutates the real cache.
"""
from __future__ import annotations

from memcore.rerank import (  # noqa: F401
    _RERANKER_CACHE,
    candidate_text,
    get_reranker,
    rerank_pairs,
    rerank_timer_ms,
    reset_reranker_cache,
)

__all__ = [
    "get_reranker",
    "reset_reranker_cache",
    "candidate_text",
    "rerank_pairs",
    "rerank_timer_ms",
]

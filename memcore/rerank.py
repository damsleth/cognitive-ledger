"""Cross-encoder reranker for the semantic_rerank retrieval mode.

Moved verbatim from ``ledger/rerank.py``. The semantic_rerank mode runs
`semantic_hybrid` first to get an input_k candidate pool, then uses a
cross-encoder to score each (query, document) pair and re-sorts by
cross-encoder score.

Cross-encoders score (query, document) pairs jointly and consistently
beat bi-encoders on top-1 precision; the cost is per-query latency
(50-200ms for top-20 on CPU).

Importing this module needs nothing beyond the stdlib; sentence-transformers
is imported lazily inside ``get_reranker`` and only required to actually
score pairs (``pip install memcore[rerank]``).
"""
from __future__ import annotations

import time
from typing import Any

_RERANKER_CACHE: dict[str, Any] = {}


def get_reranker(model_name: str, max_length: int = 512) -> Any:
    """Load and cache a CrossEncoder. First call pays the model load cost."""
    cache_key = f"{model_name}::{max_length}"
    cached = _RERANKER_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        from sentence_transformers import CrossEncoder  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "reranking requires sentence-transformers. "
            "Install with: pip install memcore[rerank] "
            "(in the ledger repo: ./scripts/setup-venv.sh --embeddings)"
        ) from exc

    model = CrossEncoder(model_name, max_length=max_length)
    _RERANKER_CACHE[cache_key] = model
    return model


def reset_reranker_cache() -> None:
    _RERANKER_CACHE.clear()


def candidate_text(title: str, body: str, max_chars: int = 2048) -> str:
    """Build the candidate text passed to the cross-encoder.

    Cross-encoder tokenizers truncate to max_length; we pre-truncate by
    characters to bound transmit size while still letting the tokenizer
    do final truncation.
    """
    title = (title or "").strip()
    body = (body or "").strip()
    text = f"{title}\n{body}" if title else body
    if max_chars and len(text) > max_chars:
        text = text[:max_chars]
    return text


def rerank_pairs(
    query: str,
    pairs: list[tuple[str, str]],
    model_name: str,
    batch_size: int = 32,
    max_length: int = 512,
) -> list[float]:
    """Score each (query, doc) pair. Returns scores in input order.

    Note: the `query` argument is informational; pairs already contain the
    query as their first element. We accept it explicitly for clarity.
    """
    del query  # unused; pairs already contain query
    if not pairs:
        return []
    model = get_reranker(model_name, max_length=max_length)
    scores = model.predict(
        pairs,
        batch_size=batch_size,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [float(s) for s in scores]


def rerank_timer_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0

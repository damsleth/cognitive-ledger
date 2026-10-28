"""Rerank operations for query."""

from __future__ import annotations

from typing import Any
from typing import Callable
from pathlib import Path
from ledger.retrieval_types import RetrievalResult
from ledger.config import get_config
import time
from ledger.querying.hybrid import rank_query_semantic_hybrid
from ledger.querying.settings import _aliases_path


def rank_query_semantic_rerank(
    query: str,
    *,
    scope: str = "all",
    limit: int = 8,
    aliases_path: str | Path | None = None,
    now_dt=None,
    embed_backend: str = "local",
    embed_model: str | None = None,
    prf_enabled: bool | None = None,
    load_embeddings_module: Callable[[], Any],
    resolve_embed_model: Callable[[str, str | None], str],
    as_of=None,
    changed_since=None,
) -> RetrievalResult:
    """Run semantic_hybrid then re-order the top-N with a cross-encoder."""
    from ledger import rerank as rerank_lib

    config = get_config()
    input_k = max(int(config.rerank_input_k), max(1, limit))
    output_k = min(int(config.rerank_output_k), limit) if limit else int(config.rerank_output_k)
    if output_k <= 0:
        output_k = limit

    started = time.perf_counter()
    base = rank_query_semantic_hybrid(
        query=query,
        scope=scope,
        limit=input_k,
        aliases_path=_aliases_path(aliases_path),
        now_dt=now_dt,
        embed_backend=embed_backend,
        embed_model=embed_model,
        prf_enabled=prf_enabled,
        load_embeddings_module=load_embeddings_module,
        resolve_embed_model=resolve_embed_model,
        as_of=as_of,
        changed_since=changed_since,
    )
    base_results = list(getattr(base, "results", []))

    if not base_results:
        # No candidates -> nothing to rerank, return base shape but flag mode.
        base.retrieval_mode = "semantic_rerank"
        base.effective_retrieval_mode = "semantic_rerank"
        return base

    rerank_started = time.perf_counter()
    pairs = [
        (
            query,
            rerank_lib.candidate_text(
                getattr(c, "title", "") or "",
                getattr(c, "body", "") or "",
                max_chars=config.rerank_max_length * 6,
            ),
        )
        for c in base_results
    ]
    scores = rerank_lib.rerank_pairs(
        query=query,
        pairs=pairs,
        model_name=config.rerank_model,
        batch_size=int(config.rerank_batch_size),
        max_length=int(config.rerank_max_length),
    )
    rerank_ms = (time.perf_counter() - rerank_started) * 1000.0

    # Stable sort by cross-encoder score, descending. Ties broken by base order.
    indexed = list(enumerate(zip(base_results, scores)))
    indexed.sort(key=lambda t: (-t[1][1], t[0]))
    reranked = [c for _, (c, _) in indexed][:output_k]

    # Surface the new rank order on the result wrapper.
    base.results = reranked
    base.retrieval_mode = "semantic_rerank"
    base.effective_retrieval_mode = "semantic_rerank"
    base.shortlist_size = len(base_results)

    # Stash rerank diagnostics where downstream metrics can reach them.
    rerank_meta = {
        "model": config.rerank_model,
        "input_k": input_k,
        "output_k": output_k,
        "scored": len(scores),
        "rerank_ms": rerank_ms,
        "total_ms": (time.perf_counter() - started) * 1000.0,
    }
    setattr(base, "rerank", rerank_meta)
    if hasattr(base, "timing") and base.timing is not None:
        # Fold rerank time into total_ms; preserve the inner semantic timing.
        try:
            base.timing.total_ms = (time.perf_counter() - started) * 1000.0
        except (AttributeError, TypeError):
            pass

    return base


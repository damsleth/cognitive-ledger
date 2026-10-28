"""Query operations for query."""

from __future__ import annotations

from typing import Any
from typing import Callable
from pathlib import Path
from ledger.retrieval_types import RetrievalResult
import datetime as _dt
from ledger.config import get_config
import re
from ledger.retrieval import resolve_retrieval_mode
from ledger import retrieval as retrieval_lib
from ledger.querying.hybrid import rank_query_semantic_hybrid
from ledger.querying.rerank import rank_query_semantic_rerank
from ledger.querying.settings import _aliases_path


def rank_query_lexical(*args: Any, **kwargs: Any) -> RetrievalResult:
    return retrieval_lib.rank_lexical(*args, **kwargs)



def _default_load_embeddings_module():
    """Lazy import of the embeddings module (default for rank_query)."""
    from ledger import embeddings as _emb
    return _emb



def _default_resolve_embed_model(backend: str, model: str | None) -> str:
    """Resolve embed model using the semantic module (default for rank_query)."""
    from ledger.semantic import resolve_embed_model as _resolve
    return _resolve(backend, model)



_TEMPORAL_YEAR_RE = re.compile(r"\b(?:in|during|i|under)\s+((?:19|20)\d{2})\b", re.IGNORECASE)



def temporal_as_of(query: str, now_dt: _dt.datetime | None = None) -> _dt.datetime | None:
    """As-of instant implied by an unambiguous year cue in *query*, else None.

    A current or future year is the default lens already, so it yields None.
    """
    match = _TEMPORAL_YEAR_RE.search(query or "")
    if not match:
        return None
    now = now_dt or _dt.datetime.now(_dt.timezone.utc)
    year = int(match.group(1))
    if year >= now.year:
        return None
    # ponytail: one mid-year instant; misses a note valid only for a slice of
    # the year that excludes July. Upgrade: a year-window overlap check in
    # apply_temporal_filter.
    return _dt.datetime(year, 7, 1, tzinfo=_dt.timezone.utc)



def rank_query(
    query: str,
    *,
    scope: str = "all",
    limit: int = 8,
    aliases_path: str | Path | None = None,
    now_dt=None,
    retrieval_mode: str = "legacy",
    embed_backend: str = "local",
    embed_model: str | None = None,
    prf_enabled: bool | None = None,
    load_embeddings_module: Callable[[], Any] = _default_load_embeddings_module,
    resolve_embed_model: Callable[[str, str | None], str] = _default_resolve_embed_model,
    as_of=None,
    changed_since=None,
) -> RetrievalResult:
    if as_of is None:
        as_of = temporal_as_of(query, now_dt)
    mode = resolve_retrieval_mode(retrieval_mode)
    if mode == "semantic_hybrid":
        result = rank_query_semantic_hybrid(
            query=query,
            scope=scope,
            limit=limit,
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
    elif mode == "semantic_rerank":
        result = rank_query_semantic_rerank(
            query=query,
            scope=scope,
            limit=limit,
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
    else:
        result = rank_query_lexical(
            query=query,
            scope=scope,
            limit=limit,
            aliases_path=_aliases_path(aliases_path),
            now_dt=now_dt,
            retrieval_mode=mode,
            as_of=as_of,
            changed_since=changed_since,
        )

    # Plan 46: annotate results with a display-only trust verdict. Never
    # reorders. Uses the active signal summary for validation/contradiction
    # counts when available (None when signal scoring is inactive).
    from ledger.retrieval import attach_trust_verdicts
    from ledger.signals import signal_summary_if_active
    attach_trust_verdicts(result.results, signal_summary_if_active(get_config()))
    return result


from ledger.querying.hybrid import rank_query_semantic_hybrid, reciprocal_rank_fusion

from ledger.querying.formatting import bundle_results, format_query_results_human

from ledger.querying.expansion import prf_expand_query_vector

from ledger.querying.serialization import payload_get, payload_results, result_get, scored_result_to_dict, retrieval_result_to_dict, query_result_to_json

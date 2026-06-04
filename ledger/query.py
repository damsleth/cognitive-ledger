"""Canonical query helpers for script and library consumers."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Callable

from ledger.config import get_config
from ledger import retrieval as retrieval_lib
from ledger.parsing import shorten
from ledger.retrieval import (
    apply_prior_tiebreak,
    compute_prior_score,
    compute_recency_component,
    expand_query_tokens,
    load_aliases,
    now_utc,
    resolve_embed_backend,
    resolve_retrieval_mode,
    scope_matches,
    tokenize,
)
from ledger.retrieval_types import (
    RetrievalCandidate,
    RetrievalResult,
    ScoreComponents,
    ScoredResult,
    TimingInfo,
)


def _detailed_reasons_limit() -> int:
    return get_config().detailed_reasons_limit


def _aliases_path(aliases_path: str | Path | None) -> Path:
    if aliases_path is None:
        return get_config().aliases_path
    return Path(aliases_path)


def payload_get(payload: RetrievalResult | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(payload, RetrievalResult):
        return getattr(payload, key, default)
    return payload.get(key, default)


def payload_results(payload: RetrievalResult | dict[str, Any]) -> list[ScoredResult | dict[str, Any]]:
    if isinstance(payload, RetrievalResult):
        return payload.results
    return payload["results"]


def result_get(result: RetrievalCandidate | ScoredResult | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(result, (RetrievalCandidate, ScoredResult)):
        return getattr(result, key, default)
    return result.get(key, default)


_payload_get = payload_get


def scored_result_to_dict(item: RetrievalCandidate | ScoredResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    components = getattr(item, "components", None)
    return {
        "path": item.rel_path or item.path,
        "rel_path": item.rel_path,
        "type": item.type,
        "title": item.title,
        "statement": item.statement,
        "body": item.body,
        "updated": item.updated,
        "updated_ts": item.updated_ts,
        "confidence": item.confidence,
        "source": item.source,
        "scope": item.scope,
        "status": item.status,
        "tags": list(item.tags),
        "note_tokens": sorted(item.note_tokens),
        "tag_tokens": sorted(item.tag_tokens),
        "attention_tokens": sorted(item.attention_tokens),
        "snippet": item.snippet,
        "has_next_action_checkbox": item.has_next_action_checkbox,
        "word_count": item.word_count,
        "score": float(getattr(item, "score", 0.0) or 0.0),
        "reasons": list(getattr(item, "reasons", [])),
        "components": {
            "bm25_score": float(getattr(components, "bm25_score", 0.0) or 0.0),
            "lexical_match": float(getattr(components, "lexical_match", 0.0) or 0.0),
            "tag_overlap": float(getattr(components, "tag_overlap", 0.0) or 0.0),
            "scope_match": float(getattr(components, "scope_match", 0.0) or 0.0),
            "recency": float(getattr(components, "recency", 0.0) or 0.0),
            "confidence": float(getattr(components, "confidence", 0.0) or 0.0),
            "semantic_similarity": float(getattr(components, "semantic_similarity", 0.0) or 0.0),
            "lexical_score": float(getattr(components, "lexical_score", 0.0) or 0.0),
            "scope_component": float(getattr(components, "scope_component", 0.0) or 0.0),
            "recency_component": float(getattr(components, "recency_component", 0.0) or 0.0),
            "prior_score": float(getattr(components, "prior_score", 0.0) or 0.0),
        },
        "disclosure_level": str(getattr(item, "disclosure_level", "") or ""),
    }


def retrieval_result_to_dict(payload: RetrievalResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {
        "query": payload.query,
        "scope": payload.scope,
        "retrieval_mode": payload.retrieval_mode,
        "progressive_top_n": payload.progressive_top_n,
        "expanded_tokens": list(payload.expanded_tokens),
        "expansion_events": list(payload.expansion_events),
        "candidate_pool_size": payload.candidate_pool_size,
        "indexed_pool_size": payload.indexed_pool_size,
        "prefilter_size": payload.prefilter_size,
        "shortlist_size": payload.shortlist_size,
        "timing": {
            "expand_ms": payload.timing.expand_ms,
            "candidates_ms": payload.timing.candidates_ms,
            "prefilter_ms": payload.timing.prefilter_ms,
            "shortlist_ms": payload.timing.shortlist_ms,
            "score_ms": payload.timing.score_ms,
            "total_ms": payload.timing.total_ms,
        },
        "results": [scored_result_to_dict(item) for item in payload.results],
        "effective_retrieval_mode": payload.effective_retrieval_mode,
        "semantic": payload.semantic,
    }


def _result_index_fields(item: ScoredResult | dict[str, Any]) -> dict[str, Any]:
    """Compact index fields (~20-30 tokens per result)."""
    data: dict[str, Any] = {
        "path": result_get(item, "rel_path") or result_get(item, "path"),
        "title": result_get(item, "title"),
        "type": result_get(item, "type"),
        "score": round(float(result_get(item, "score", 0.0)), 6),
        "updated": result_get(item, "updated"),
        "confidence": result_get(item, "confidence"),
        "scope": result_get(item, "scope"),
        "word_count": result_get(item, "word_count", 0),
    }
    if result_get(item, "type") == "loop":
        data["status"] = result_get(item, "status", "")
    return data


def _result_context_fields(item: ScoredResult | dict[str, Any]) -> dict[str, Any]:
    """Context fields - index plus statement, snippet, tags (~80-120 tokens)."""
    data = _result_index_fields(item)
    data["statement"] = result_get(item, "statement", "")
    data["snippet"] = result_get(item, "snippet", "")
    data["tags"] = list(result_get(item, "tags", []) or [])
    data["source"] = result_get(item, "source")
    data["reasons"] = result_get(item, "reasons", [])
    if result_get(item, "disclosure_level"):
        data["disclosure_level"] = result_get(item, "disclosure_level")
    return data


def _result_detail_fields(item: ScoredResult | dict[str, Any]) -> dict[str, Any]:
    """Full detail - everything including body and score components."""
    data = _result_context_fields(item)
    data["body"] = result_get(item, "body", "")
    data["note_tokens"] = sorted(result_get(item, "note_tokens", set()) or set())
    data["tag_tokens"] = sorted(result_get(item, "tag_tokens", set()) or set())
    data["attention_tokens"] = sorted(result_get(item, "attention_tokens", set()) or set())
    data["has_next_action_checkbox"] = result_get(item, "has_next_action_checkbox", False)
    components = result_get(item, "components", None)
    if components is not None:
        data["components"] = {
            "bm25_score": float(getattr(components, "bm25_score", 0.0) or 0.0),
            "lexical_match": float(getattr(components, "lexical_match", 0.0) or 0.0),
            "tag_overlap": float(getattr(components, "tag_overlap", 0.0) or 0.0),
            "scope_match": float(getattr(components, "scope_match", 0.0) or 0.0),
            "recency": float(getattr(components, "recency", 0.0) or 0.0),
            "confidence": float(getattr(components, "confidence", 0.0) or 0.0),
            "semantic_similarity": float(getattr(components, "semantic_similarity", 0.0) or 0.0),
            "lexical_score": float(getattr(components, "lexical_score", 0.0) or 0.0),
            "scope_component": float(getattr(components, "scope_component", 0.0) or 0.0),
            "recency_component": float(getattr(components, "recency_component", 0.0) or 0.0),
            "prior_score": float(getattr(components, "prior_score", 0.0) or 0.0),
        }
    return data


_VIEW_FORMATTERS = {
    "index": _result_index_fields,
    "context": _result_context_fields,
    "detail": _result_detail_fields,
}


def query_result_to_json(
    payload: RetrievalResult | dict[str, Any],
    *,
    include_bundle: bool = False,
    bundle_word_budget: int = 1200,
    view: str = "context",
) -> dict[str, Any]:
    formatter = _VIEW_FORMATTERS.get(view, _result_context_fields)
    out = {
        "query": payload_get(payload, "query"),
        "scope": payload_get(payload, "scope"),
        "view": view,
        "retrieval_mode": payload_get(payload, "retrieval_mode", "legacy"),
        "effective_retrieval_mode": payload_get(
            payload,
            "effective_retrieval_mode",
            payload_get(payload, "retrieval_mode", "legacy"),
        ),
        "candidate_pool_size": payload_get(payload, "candidate_pool_size"),
        "indexed_pool_size": payload_get(payload, "indexed_pool_size"),
        "prefilter_size": payload_get(payload, "prefilter_size"),
        "shortlist_size": payload_get(payload, "shortlist_size"),
        "progressive_top_n": payload_get(payload, "progressive_top_n", 0),
        "expanded_tokens": payload_get(payload, "expanded_tokens", []),
        "timing": getattr(getattr(payload, "timing", None), "__dict__", None),
        "semantic": payload_get(payload, "semantic"),
        "results": [],
    }

    results = payload_results(payload)
    for item in results:
        out["results"].append(formatter(item))

    if include_bundle:
        out["bundle"] = bundle_results(results, word_budget=bundle_word_budget)
    return out


def _scored_result_from_candidate(
    candidate: RetrievalCandidate | dict[str, Any],
    *,
    score: float,
    reasons: list[str],
    components: ScoreComponents,
    disclosure_level: str = "",
) -> ScoredResult:
    return ScoredResult(
        path=str(result_get(candidate, "path", "") or ""),
        rel_path=str(result_get(candidate, "rel_path", "") or ""),
        type=str(result_get(candidate, "type", "") or ""),
        title=str(result_get(candidate, "title", "") or ""),
        statement=str(result_get(candidate, "statement", "") or ""),
        body=str(result_get(candidate, "body", "") or ""),
        updated=str(result_get(candidate, "updated", "") or ""),
        updated_ts=result_get(candidate, "updated_ts"),
        confidence=float(result_get(candidate, "confidence", 0.0) or 0.0),
        source=str(result_get(candidate, "source", "") or ""),
        scope=str(result_get(candidate, "scope", "") or ""),
        status=str(result_get(candidate, "status", "") or ""),
        tags=list(result_get(candidate, "tags", []) or []),
        note_tokens=set(result_get(candidate, "note_tokens", set()) or set()),
        tag_tokens=set(result_get(candidate, "tag_tokens", set()) or set()),
        attention_tokens=set(result_get(candidate, "attention_tokens", set()) or set()),
        snippet=str(result_get(candidate, "snippet", "") or ""),
        has_next_action_checkbox=bool(result_get(candidate, "has_next_action_checkbox", False)),
        word_count=int(result_get(candidate, "word_count", 0) or 0),
        created_ts=result_get(candidate, "created_ts"),
        score=score,
        reasons=reasons,
        components=components,
        disclosure_level=disclosure_level,
    )


def rank_query_lexical(*args: Any, **kwargs: Any) -> RetrievalResult:
    return retrieval_lib.rank_lexical(*args, **kwargs)


def lexical_score_component(candidate: ScoredResult | dict[str, Any], query_tokens: set[str]) -> tuple[float, int, int]:
    note_tokens = set(result_get(candidate, "note_tokens", set()) or set())
    tag_tokens = set(result_get(candidate, "tag_tokens", set()) or set())
    if query_tokens:
        lexical_overlap_count = len(note_tokens & query_tokens)
        lexical_match = lexical_overlap_count / len(query_tokens)
        tag_overlap_count = len(tag_tokens & query_tokens)
        tag_overlap = tag_overlap_count / len(query_tokens)
    else:
        lexical_overlap_count = 0
        lexical_match = 0.0
        tag_overlap_count = 0
        tag_overlap = 0.0

    lexical_score = (0.70 * lexical_match) + (0.30 * tag_overlap)
    lexical_score = max(0.0, min(1.0, lexical_score))
    return lexical_score, lexical_overlap_count, tag_overlap_count


def reciprocal_rank_fusion(
    ranked_lists: list[list[str]],
    k: int = 60,
) -> dict[str, float]:
    """Compute Reciprocal Rank Fusion scores for items appearing in multiple rank lists.

    Each element of ``ranked_lists`` is an ordered list of item keys (e.g.
    ``rel_path`` strings) from highest to lowest rank.  Items that appear in
    multiple lists accumulate scores; items absent from a list contribute 0.

    Formula: RRF(d) = sum_over_lists(1 / (k + rank(d)))
    where rank is 1-based and ``k`` is the smoothing constant (default 60).

    Returns a dict mapping item key -> RRF score (higher is better).
    The absolute score values are not meaningful on their own — use them
    only for relative ordering.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank_idx, item_key in enumerate(ranked):
            scores[item_key] = scores.get(item_key, 0.0) + 1.0 / (k + rank_idx + 1)
    return scores


def prf_expand_query_vector(
    query_vec: "np.ndarray",
    candidate_vectors: dict[str, "np.ndarray"],
    top_rel_paths: list[str],
    bottom_rel_paths: list[str],
    alpha: float = 1.0,
    beta: float = 0.75,
    gamma: float = 0.15,
) -> "np.ndarray":
    """Expand a query vector via Rocchio Pseudo-Relevance Feedback (PRF).

    Implements the Rocchio formula:
      q2 = alpha * q + beta * mean(pseudo_positives) - gamma * mean(pseudo_negatives)

    The result is L2-normalised to unit length so it can be used directly with
    cosine-similarity scoring.  If no pseudo-positive or pseudo-negative vectors
    are available (e.g. too few candidates), the original query vector is returned
    unchanged.

    Parameters
    ----------
    query_vec:
        Original query embedding, shape (1, dims) or (dims,), float32, L2-normalised.
    candidate_vectors:
        Mapping from rel_path -> embedding vector (shape (dims,) or (1, dims)).
    top_rel_paths:
        Paths of pseudo-positive candidates (top-m ranked results).
    bottom_rel_paths:
        Paths of pseudo-negative candidates (bottom-n from a wider pool).
    alpha, beta, gamma:
        Rocchio weighting coefficients.

    Returns
    -------
    np.ndarray of shape (1, dims), float32, L2-normalised expanded query vector.
    """
    import numpy as np

    q = np.asarray(query_vec, dtype=np.float32).reshape(-1)

    pos_vecs = [
        np.asarray(candidate_vectors[p], dtype=np.float32).reshape(-1)
        for p in top_rel_paths
        if p in candidate_vectors
    ]
    neg_vecs = [
        np.asarray(candidate_vectors[p], dtype=np.float32).reshape(-1)
        for p in bottom_rel_paths
        if p in candidate_vectors
    ]

    q2 = alpha * q

    if pos_vecs:
        pos_centroid = np.mean(np.stack(pos_vecs, axis=0), axis=0)
        q2 = q2 + beta * pos_centroid

    if neg_vecs:
        neg_centroid = np.mean(np.stack(neg_vecs, axis=0), axis=0)
        q2 = q2 - gamma * neg_centroid

    # L2 normalise
    norm = float(np.linalg.norm(q2))
    if norm > 1e-9:
        q2 = q2 / norm

    return q2.reshape(1, -1).astype(np.float32)


def rank_query_semantic_hybrid(
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
) -> dict[str, Any]:
    started = time.perf_counter()
    config = get_config()
    now_dt = now_dt or now_utc()
    aliases = load_aliases(_aliases_path(aliases_path))
    query_tokens = tokenize(query)
    expanded_tokens, expansion_events = expand_query_tokens(query_tokens, aliases)
    include_reasons = limit <= _detailed_reasons_limit()

    # Resolve fusion mode. Invalid config/env values fall back to the documented
    # default and report the effective mode truthfully in result metadata.
    fusion_mode = str(config.fusion or "weighted_sum").strip().lower()
    if fusion_mode not in {"weighted_sum", "rrf"}:
        fusion_mode = "weighted_sum"

    # Resolve PRF flag: explicit arg > config
    _use_prf = config.prf_enabled if prf_enabled is None else bool(prf_enabled)

    # Load signal summary once if signal scoring is enabled (same gate as lexical path)
    _signal_summary: dict[str, Any] | None = None
    if config.score_weight_signal > 0:
        from ledger.signals import load_signal_summary
        _sig_summary_raw = load_signal_summary()
        _real_signals = _sig_summary_raw.get("_meta", {}).get("real_signals", 0)
        if _real_signals >= config.signal_min_entries:
            _signal_summary = _sig_summary_raw

    candidates_started = time.perf_counter()
    candidates = retrieval_lib.build_candidates(use_cache=True)
    candidates_ms = (time.perf_counter() - candidates_started) * 1000.0
    backend = resolve_embed_backend(embed_backend)
    model = resolve_embed_model(backend, embed_model)
    embeddings = load_embeddings_module()

    if backend == "openai":
        embeddings.ensure_openai_api_key()

    score_started = time.perf_counter()
    semantic = embeddings.semantic_score_map(
        query=query,
        target="ledger",
        backend=backend,
        model=model,
    )
    if not semantic.get("available"):
        if semantic.get("reason") == "missing_index":
            fallback = rank_query_lexical(
                query=query,
                scope=scope,
                limit=limit,
                aliases_path=_aliases_path(aliases_path),
                now_dt=now_dt,
                retrieval_mode="precomputed_index",
            )
            fallback.retrieval_mode = "semantic_hybrid"
            fallback.effective_retrieval_mode = "precomputed_index"
            fallback.semantic = {
                "available": False,
                "reason": "missing_index",
                "backend": backend,
                "model": model,
            }
            return fallback
        raise RuntimeError(f"semantic_hybrid retrieval failed: {semantic.get('reason', 'unknown_error')}")

    score_by_rel_path: dict[str, float] = semantic.get("score_by_rel_path", {})

    # -------------------------------------------------------------------------
    # PRF: expand the query vector before scoring (dense path only, default off)
    # -------------------------------------------------------------------------
    if _use_prf and score_by_rel_path:
        # Run a fast initial ranking (top prf_top_m + prf_bottom_n pool) to
        # identify pseudo-positive and pseudo-negative candidates.
        prf_pool_size = max(config.prf_top_m + config.prf_bottom_n + 5, 20)
        _prf_by_score = sorted(score_by_rel_path.items(), key=lambda kv: kv[1], reverse=True)
        _prf_top_paths = [p for p, _ in _prf_by_score[: config.prf_top_m]]
        _prf_all_paths = [p for p, _ in _prf_by_score[:prf_pool_size]]
        _prf_bottom_paths = [p for p, _ in _prf_by_score[max(0, len(_prf_all_paths) - config.prf_bottom_n) :]]

        # Retrieve the actual index vectors for the pseudo-relevant docs. Tests
        # can provide them directly in the semantic payload; the real embeddings
        # module exposes them via load_semantic_index().
        _item_vectors: dict[str, Any] = semantic.get("item_vectors") or {}
        _item_list = semantic.get("items") or []
        _index_template = str(semantic.get("text_template", "none") or "none")
        if not _item_vectors and hasattr(embeddings, "load_semantic_index"):
            try:
                _index_data, _vectors = embeddings.load_semantic_index("ledger", backend, model)
                if _index_data is not None and _vectors is not None:
                    _item_list = _index_data.get("items", []) or []
                    _index_template = str(_index_data.get("text_template", "none") or "none")
                    _item_vectors = {
                        str(item.get("rel_path", "")): _vectors[i]
                        for i, item in enumerate(_item_list[: _vectors.shape[0]])
                        if item.get("rel_path")
                    }
            except Exception:
                _item_vectors = {}
        if _item_vectors:
            try:
                _q_vec = embeddings.embed_query_text(
                    query, backend=backend, model=model, text_template=_index_template
                )
                _expanded_q_vec = prf_expand_query_vector(
                    _q_vec,
                    _item_vectors,
                    top_rel_paths=_prf_top_paths,
                    bottom_rel_paths=_prf_bottom_paths,
                    alpha=config.prf_alpha,
                    beta=config.prf_beta,
                    gamma=config.prf_gamma,
                )
                # Re-compute cosine scores with expanded vector
                import numpy as np
                _vecs_matrix = None
                if _item_list:
                    _rows = [
                        np.asarray(_item_vectors.get(item.get("rel_path", ""), np.zeros(1)), dtype=np.float32).reshape(-1)
                        for item in _item_list
                    ]
                    if _rows:
                        _vecs_matrix = np.stack(_rows, axis=0)  # (N, dims)
                if _vecs_matrix is not None and _vecs_matrix.shape[0] > 0:
                    _q_flat = _expanded_q_vec.reshape(-1)
                    _dot = _vecs_matrix @ _q_flat  # (N,)
                    score_by_rel_path = {
                        item.get("rel_path", ""): float(_dot[i])
                        for i, item in enumerate(_item_list)
                        if item.get("rel_path")
                    }
            except Exception:
                # PRF is best-effort; fall through to original scores on any error
                pass

    # -------------------------------------------------------------------------
    # RRF fusion: generate a lexical rank list and a semantic rank list, then
    # merge with Reciprocal Rank Fusion.  Only active when fusion="rrf".
    # Default ("weighted_sum") runs the existing single-pass formula below.
    # -------------------------------------------------------------------------
    rrf_scores: dict[str, float] = {}
    if fusion_mode == "rrf":
        # Build BOTH rank lists over the SAME candidate pool so they are
        # comparable rankings.  Previously the lexical list only contained
        # candidates with lexical overlap > 0; that meant a strong-semantic /
        # zero-lexical note appeared in just ONE list while a weak-semantic but
        # lexically-matched note appeared in BOTH and accumulated ~2x the RRF
        # score — systematically demoting pure-semantic winners.  Ranking the
        # full pool in each list (zero-overlap candidates fall to the bottom of
        # the lexical list, by score then rel_path for determinism) restores a
        # proper reciprocal-rank comparison.
        pool_paths = [str(result_get(_c, "rel_path", "")) for _c in candidates]
        pool_paths = [p for p in pool_paths if p]

        # Semantic rank list: every candidate ordered by descending cosine.
        semantic_ranked_paths = sorted(
            pool_paths,
            key=lambda p: (score_by_rel_path.get(p, 0.0), p),
            reverse=True,
        )

        # Lexical rank list: every candidate ordered by descending lexical
        # score (zero-overlap candidates tie at 0 and sort to the bottom).
        _lex_by_path: dict[str, float] = {}
        for _c in candidates:
            _rp = str(result_get(_c, "rel_path", ""))
            if not _rp:
                continue
            _ls, _, _ = lexical_score_component(_c, expanded_tokens)
            _lex_by_path[_rp] = _ls
        lexical_ranked_paths = sorted(
            pool_paths,
            key=lambda p: (_lex_by_path.get(p, 0.0), p),
            reverse=True,
        )

        rrf_scores = reciprocal_rank_fusion(
            [semantic_ranked_paths, lexical_ranked_paths],
            k=int(config.rrf_k),
        )

    # -------------------------------------------------------------------------
    # Score every candidate (weighted_sum or rrf) and build result list
    # -------------------------------------------------------------------------
    ranked: list[ScoredResult] = []
    for candidate in candidates:
        rel_path = str(result_get(candidate, "rel_path", "") or "")
        cosine = float(score_by_rel_path.get(rel_path, 0.0))
        semantic_component = max(0.0, min(1.0, cosine))
        lexical_score, lexical_overlap_count, tag_overlap_count = lexical_score_component(candidate, expanded_tokens)
        scope_component = 1.0 if scope == "all" else (
            1.0 if scope_matches(result_get(candidate, "scope", ""), scope) else 0.0
        )
        recency_component = compute_recency_component(result_get(candidate, "updated_ts"), now_dt)

        # Prior score — uses cosine similarity as the relevance proxy in semantic mode.
        # Always-on once prior_enabled=True, signal-independent.
        prior = 0.0
        if config.prior_enabled:
            prior = compute_prior_score(
                candidate,
                now_dt=now_dt,
                query_lexical_relevance=semantic_component,
            )

        # Signal feedback score (requires real_signals gate to be met)
        sig_score = 0.0
        if config.score_weight_signal > 0 and _signal_summary is not None and rel_path:
            from ledger.signals import get_signal_score
            sig_score = get_signal_score(rel_path, summary=_signal_summary)

        if fusion_mode == "rrf":
            # Use normalised RRF score as the combined relevance signal; still
            # blend with scope and recency for diversity.
            rrf_score = rrf_scores.get(rel_path, 0.0)
            # Normalise RRF to [0, 1] against the MAX RRF actually observed in
            # this pool (not the theoretical rank-1-in-both maximum).  With the
            # theoretical max, scores compressed into a tiny band near 0 and the
            # minor recency/scope terms dominated ordering; normalising to the
            # observed max spreads scores across the full range so the fused
            # rank — not recency noise — drives ordering.
            max_rrf = max(rrf_scores.values()) if rrf_scores else 0.0
            rrf_normalised = rrf_score / max_rrf if max_rrf > 0 else 0.0
            # Prior is blended later as a tie-breaker (apply_prior_tiebreak).
            final_score = (
                (config.semantic_weight_vector + config.semantic_weight_lexical) * rrf_normalised
                + config.semantic_weight_scope * scope_component
                + config.semantic_weight_recency * recency_component
                + config.score_weight_signal * sig_score
            )
            final_score = max(0.0, min(1.0, final_score))
            if rrf_score == 0.0:
                continue
        else:
            # Default: weighted_sum formula. The prior is NOT added here; it is
            # blended in afterwards as a tie-breaker by apply_prior_tiebreak.
            final_score = (
                (config.semantic_weight_vector * semantic_component)
                + (config.semantic_weight_lexical * lexical_score)
                + (config.semantic_weight_scope * scope_component)
                + (config.semantic_weight_recency * recency_component)
                + config.score_weight_signal * sig_score
            )
            final_score = max(0.0, min(1.0, final_score))
            if semantic_component == 0.0 and lexical_score == 0.0:
                continue

        reasons: list[str] = []
        if include_reasons:
            if fusion_mode == "rrf":
                reasons.append(f"rrf={rrf_scores.get(rel_path, 0.0):.4f}")
                reasons.append(f"semantic={semantic_component:.3f}")
            else:
                reasons.append(f"semantic={semantic_component:.3f}")
            if _use_prf:
                reasons.append("prf")
            if lexical_overlap_count > 0:
                reasons.append(f"lexical_overlap={lexical_overlap_count}")
            if tag_overlap_count > 0:
                reasons.append(f"tag_overlap={tag_overlap_count}")
            if scope != "all":
                reasons.append("scope_match" if scope_component >= 1.0 else "scope_miss")
            reasons.append(f"recency={recency_component:.2f}")
            if prior > 0:
                reasons.append(f"prior={prior:.3f}")
            if sig_score != 0:
                reasons.append(f"signal={sig_score:.3f}")
            if expansion_events:
                alias_summary = ", ".join(
                    sorted({f"{event['alias']}->{event['phrase']}" for event in expansion_events})
                )
                reasons.append(f"alias_expansion={alias_summary}")

        ranked.append(
            _scored_result_from_candidate(
                candidate,
                score=final_score,
                reasons=reasons,
                components=ScoreComponents(
                    semantic_similarity=semantic_component,
                    lexical_score=lexical_score,
                    scope_component=scope_component,
                    recency_component=recency_component,
                    recency=recency_component,
                    prior_score=prior,
                ),
            )
        )

    # Blend the prior in as a tie-breaker over the full pool, then sort.
    # (single shared implementation used by both retrieval paths)
    apply_prior_tiebreak(ranked)

    score_ms = (time.perf_counter() - score_started) * 1000.0
    total_ms = (time.perf_counter() - started) * 1000.0

    effective_mode = "semantic_hybrid"
    if fusion_mode == "rrf":
        effective_mode = "semantic_hybrid_rrf"
    elif _use_prf:
        effective_mode = "semantic_hybrid_prf"

    return RetrievalResult(
        query=query,
        scope=scope,
        retrieval_mode="semantic_hybrid",
        effective_retrieval_mode=effective_mode,
        progressive_top_n=0,
        expanded_tokens=sorted(expanded_tokens),
        expansion_events=expansion_events,
        candidate_pool_size=len(candidates),
        indexed_pool_size=semantic.get("index_item_count"),
        prefilter_size=len(candidates),
        shortlist_size=len(candidates),
        semantic={
            "available": True,
            "backend": backend,
            "model": model,
            "index_item_count": semantic.get("index_item_count"),
            "fusion": fusion_mode,
            "prf": _use_prf,
        },
        results=ranked[:limit],
        timing=TimingInfo(
            candidates_ms=candidates_ms,
            score_ms=score_ms,
            total_ms=total_ms,
        ),
    )


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
    load_embeddings_module: Callable[[], Any],
    resolve_embed_model: Callable[[str, str | None], str],
) -> RetrievalResult:
    mode = resolve_retrieval_mode(retrieval_mode)
    if mode == "semantic_hybrid":
        return rank_query_semantic_hybrid(
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
        )

    if mode == "semantic_rerank":
        return rank_query_semantic_rerank(
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
        )

    return rank_query_lexical(
        query=query,
        scope=scope,
        limit=limit,
        aliases_path=_aliases_path(aliases_path),
        now_dt=now_dt,
        retrieval_mode=mode,
    )


def bundle_results(results: list[ScoredResult | dict[str, Any]], word_budget: int = 1200) -> list[dict[str, Any]]:
    bundle = []
    words_left = max(0, word_budget)

    for item in results:
        if words_left <= 0:
            break
        words = str(result_get(item, "body", "")).split()
        if not words:
            continue
        take = min(len(words), max(40, min(words_left, 200)))
        excerpt = " ".join(words[:take])
        words_left -= take
        bundle.append(
            {
                "path": result_get(item, "rel_path", "") or result_get(item, "path", ""),
                "title": result_get(item, "title", ""),
                "excerpt": excerpt,
                "words": take,
            }
        )
    return bundle


def format_query_results_human(
    payload: RetrievalResult | dict[str, Any],
    include_bundle: bool = False,
    view: str = "context",
) -> str:
    results = payload_results(payload)
    lines = [
        f"query: {_payload_get(payload, 'query', '')}",
        f"scope: {_payload_get(payload, 'scope', 'all')}",
        f"retrieval_mode: {_payload_get(payload, 'retrieval_mode', 'legacy')}",
    ]
    if view != "context":
        lines.append(f"view: {view}")
    if _payload_get(payload, "effective_retrieval_mode") and _payload_get(
        payload, "effective_retrieval_mode"
    ) != _payload_get(payload, "retrieval_mode"):
        lines.append(f"effective_retrieval_mode: {_payload_get(payload, 'effective_retrieval_mode')}")
    semantic_meta = _payload_get(payload, "semantic")
    if isinstance(semantic_meta, dict):
        if semantic_meta.get("available"):
            lines.append(
                f"semantic: available ({semantic_meta.get('backend', 'unknown')}/{semantic_meta.get('model', 'unknown')})"
            )
        else:
            lines.append(f"semantic: unavailable ({semantic_meta.get('reason', 'unknown')})")
    lines.append(f"results: {len(results)}")

    for item in results:
        wc = result_get(item, "word_count", 0) or 0
        cost_hint = f" ~{wc}w" if wc else ""

        if view == "index":
            # Compact: score, type, path, cost
            lines.append(
                f"- {result_get(item, 'score', 0.0):.3f} | "
                f"{result_get(item, 'type', '')} | "
                f"{result_get(item, 'rel_path', '')}{cost_hint}"
            )
        elif view == "detail":
            # Full: score line, then statement, body excerpt, tags
            rationale = ", ".join(result_get(item, "reasons", [])[:3])
            lines.append(
                f"- {result_get(item, 'score', 0.0):.3f} | "
                f"{result_get(item, 'type', '')} | "
                f"{result_get(item, 'rel_path', '')}{cost_hint} | "
                f"{rationale}"
            )
            statement = result_get(item, "statement", "")
            if statement:
                lines.append(f"  statement: {shorten(statement, 200)}")
            body = result_get(item, "body", "")
            if body:
                lines.append(f"  body: {shorten(body, 400)}")
            tags = result_get(item, "tags", [])
            if tags:
                lines.append(f"  tags: {', '.join(tags)}")
        else:
            # Context (default): score, type, path, cost, reasons
            rationale = ", ".join(result_get(item, "reasons", [])[:3])
            level = result_get(item, "disclosure_level", "")
            level_segment = f"{level} | " if level else ""
            lines.append(
                f"- {result_get(item, 'score', 0.0):.3f} | "
                f"{result_get(item, 'type', '')} | "
                f"{result_get(item, 'rel_path', '')}{cost_hint} | "
                f"{level_segment}{rationale}"
            )

    if include_bundle:
        lines.append("")
        lines.append("Bundle:")
        for chunk in bundle_results(results, word_budget=1200):
            lines.append(f"- {chunk['title']} [{chunk['path']}]")
            lines.append(f"  {shorten(chunk['excerpt'], 260)}")

    return "\n".join(lines)

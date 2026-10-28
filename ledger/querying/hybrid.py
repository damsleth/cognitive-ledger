"""Hybrid operations for query."""

from __future__ import annotations

from typing import Any
from typing import Callable
from pathlib import Path
from ledger.retrieval_types import RetrievalCandidate
from ledger.retrieval_types import RetrievalResult
from ledger.retrieval_types import ScoreComponents
from ledger.retrieval_types import ScoredResult
from ledger.retrieval_types import TimingInfo
from ledger.scoring import overlap_components as _overlap_components
from ledger.retrieval import apply_prior_tiebreak
from ledger.retrieval import compute_prior_score
from ledger.retrieval import compute_recency_component
from ledger.retrieval import expand_query_tokens
from ledger.config import get_config
from ledger.retrieval import load_aliases
from ledger.retrieval import now_utc
from ledger.retrieval import resolve_embed_backend
from ledger import retrieval as retrieval_lib
from ledger.retrieval import scope_matches
import time
from ledger.parsing import tokenize
from ledger.ranking.lexical import rank_lexical as rank_query_lexical
from ledger.querying.expansion import prf_expand_query_vector
from ledger.querying.serialization import result_get
from ledger.querying.settings import _aliases_path, _detailed_reasons_limit


def _scored_result_from_candidate(
    candidate: RetrievalCandidate | dict[str, Any],
    *,
    score: float,
    reasons: list[str],
    components: ScoreComponents,
    disclosure_level: str = "",
) -> ScoredResult:
    return ScoredResult(
        **retrieval_lib._candidate_fields(candidate),
        score=score,
        reasons=reasons,
        components=components,
        disclosure_level=disclosure_level,
    )



def lexical_score_component(candidate: ScoredResult | dict[str, Any], query_tokens: set[str]) -> tuple[float, int, int]:
    """Compute a blended lexical score and raw overlap counts for *candidate*.

    Delegates overlap computation to ``scoring.overlap_components`` and applies
    the query.py-specific 0.70/0.30 blend (note vs tag weights differ from the
    0.70/0.20/0.10 blend used in the retrieval-pass coarse scorer).
    """
    note_tokens = set(result_get(candidate, "note_tokens", set()) or set())
    tag_tokens = set(result_get(candidate, "tag_tokens", set()) or set())
    lexical_match, tag_overlap, lexical_overlap_count, tag_overlap_count = _overlap_components(
        note_tokens, tag_tokens, query_tokens
    )
    lexical_score = (0.70 * lexical_match) + (0.30 * tag_overlap)
    lexical_score = max(0.0, min(1.0, lexical_score))
    return lexical_score, lexical_overlap_count, tag_overlap_count



from memcore.rrf import reciprocal_rank_fusion  # noqa: F401, E402



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
    as_of=None,
    changed_since=None,
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
    from ledger.signals import signal_summary_if_active
    _signal_summary: dict[str, Any] | None = signal_summary_if_active(config)

    candidates_started = time.perf_counter()
    if as_of is not None:
        candidates = retrieval_lib.build_candidates_with_archive()
    else:
        candidates = retrieval_lib.build_candidates(use_cache=True)
    # Apply temporal filter after candidate generation.
    candidates = retrieval_lib.apply_temporal_filter(
        list(candidates), as_of=as_of, now_dt=now_dt
    )
    # Record-time window filter (--changed-since); no-op when None.
    candidates = retrieval_lib.apply_changed_since_filter(
        candidates, since=changed_since
    )
    candidates_ms = (time.perf_counter() - candidates_started) * 1000.0
    backend = resolve_embed_backend(embed_backend)
    model = resolve_embed_model(backend, embed_model)
    embeddings = load_embeddings_module()

    if backend == "openai":
        embeddings.ensure_openai_api_key()

    score_started = time.perf_counter()
    # Only as-of reads may score superseded notes (see semantic_score_map).
    # Passed only when needed: the embeddings module is an injectable seam,
    # and older implementations do not take the keyword.
    archive_kw = {"include_archive": True} if as_of is not None else {}
    semantic = embeddings.semantic_score_map(
        query=query,
        target="ledger",
        backend=backend,
        model=model,
        **archive_kw,
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
                as_of=as_of,
                changed_since=changed_since,
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
        _prf_bottom_paths = _prf_all_paths[-config.prf_bottom_n :]

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

        # Signal feedback score (requires real_signals gate to be met).
        # The raw score is query-independent (a note is "good"/"stale" globally),
        # so it is NOT added to the base here; it is carried in
        # ScoreComponents.signal_score and folded in by apply_prior_tiebreak as a
        # TIE-BREAKER (same mechanism as the prior). Adding it linearly let a
        # high weight float signalled-but-irrelevant notes above correct ones on
        # unrelated queries (measured: weight 0.3 dropped general hit@1
        # 0.80->0.667; even relevance-gated, 0.4 dropped it to 0.711).
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
            # Prior AND signal are blended later as tie-breakers
            # (apply_prior_tiebreak), not added to the base here.
            final_score = (
                (config.semantic_weight_vector + config.semantic_weight_lexical) * rrf_normalised
                + config.semantic_weight_scope * scope_component
                + config.semantic_weight_recency * recency_component
            )
            final_score = max(0.0, min(1.0, final_score))
            if rrf_score == 0.0:
                continue
        else:
            # Default: weighted_sum formula. Prior and signal are NOT added
            # here; both are blended in afterwards as tie-breakers by
            # apply_prior_tiebreak.
            final_score = (
                (config.semantic_weight_vector * semantic_component)
                + (config.semantic_weight_lexical * lexical_score)
                + (config.semantic_weight_scope * scope_component)
                + (config.semantic_weight_recency * recency_component)
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
                    signal_score=sig_score,
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


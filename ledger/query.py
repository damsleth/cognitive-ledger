"""Canonical query helpers for script and library consumers."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any, Callable

from ledger.config import get_config
from ledger import retrieval as retrieval_lib
from ledger.parsing import shorten
from ledger.retrieval import (
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


def query_result_to_json(
    payload: RetrievalResult | dict[str, Any],
    *,
    include_bundle: bool = False,
    bundle_word_budget: int = 1200,
) -> dict[str, Any]:
    out = {
        "query": payload_get(payload, "query"),
        "scope": payload_get(payload, "scope"),
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
        data = {
            "path": result_get(item, "rel_path") or result_get(item, "path"),
            "title": result_get(item, "title"),
            "type": result_get(item, "type"),
            "score": round(float(result_get(item, "score", 0.0)), 6),
            "reasons": result_get(item, "reasons", []),
            "updated": result_get(item, "updated"),
            "confidence": result_get(item, "confidence"),
            "source": result_get(item, "source"),
            "scope": result_get(item, "scope"),
            "word_count": result_get(item, "word_count", 0),
        }
        if result_get(item, "type") == "loop":
            data["status"] = result_get(item, "status", "")
        if result_get(item, "disclosure_level"):
            data["disclosure_level"] = result_get(item, "disclosure_level")
        out["results"].append(data)

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


def rank_query_semantic_hybrid(
    query: str,
    *,
    scope: str = "all",
    limit: int = 8,
    aliases_path: str | Path | None = None,
    now_dt=None,
    embed_backend: str = "local",
    embed_model: str | None = None,
    load_embeddings_module: Callable[[], Any],
    resolve_embed_model: Callable[[str, str | None], str],
) -> dict[str, Any]:
    started = time.perf_counter()
    now_dt = now_dt or now_utc()
    aliases = load_aliases(_aliases_path(aliases_path))
    query_tokens = tokenize(query)
    expanded_tokens, expansion_events = expand_query_tokens(query_tokens, aliases)
    include_reasons = limit <= _detailed_reasons_limit()

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
                retrieval_mode="legacy",
            )
            fallback.retrieval_mode = "semantic_hybrid"
            fallback.effective_retrieval_mode = "legacy"
            fallback.semantic = {
                "available": False,
                "reason": "missing_index",
                "backend": backend,
                "model": model,
            }
            return fallback
        raise RuntimeError(f"semantic_hybrid retrieval failed: {semantic.get('reason', 'unknown_error')}")

    score_by_rel_path = semantic.get("score_by_rel_path", {})
    ranked: list[ScoredResult] = []
    for candidate in candidates:
        cosine = float(score_by_rel_path.get(result_get(candidate, "rel_path", ""), 0.0))
        semantic_component = max(0.0, min(1.0, cosine))
        lexical_score, lexical_overlap_count, tag_overlap_count = lexical_score_component(candidate, expanded_tokens)
        scope_component = 1.0 if scope == "all" else (
            1.0 if scope_matches(result_get(candidate, "scope", ""), scope) else 0.0
        )
        recency_component = compute_recency_component(result_get(candidate, "updated_ts"), now_dt)

        final_score = (
            (0.55 * semantic_component)
            + (0.30 * lexical_score)
            + (0.10 * scope_component)
            + (0.05 * recency_component)
        )
        final_score = max(0.0, min(1.0, final_score))

        if semantic_component == 0.0 and lexical_score == 0.0:
            continue

        reasons: list[str] = []
        if include_reasons:
            reasons.append(f"semantic={semantic_component:.3f}")
            if lexical_overlap_count > 0:
                reasons.append(f"lexical_overlap={lexical_overlap_count}")
            if tag_overlap_count > 0:
                reasons.append(f"tag_overlap={tag_overlap_count}")
            if scope != "all":
                reasons.append("scope_match" if scope_component >= 1.0 else "scope_miss")
            reasons.append(f"recency={recency_component:.2f}")
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
                ),
            )
        )

    ranked.sort(
        key=lambda item: (
            item.score,
            item.updated or "",
            item.path,
        ),
        reverse=True,
    )

    score_ms = (time.perf_counter() - score_started) * 1000.0
    total_ms = (time.perf_counter() - started) * 1000.0

    return RetrievalResult(
        query=query,
        scope=scope,
        retrieval_mode="semantic_hybrid",
        effective_retrieval_mode="semantic_hybrid",
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
        },
        results=ranked[:limit],
        timing=TimingInfo(
            candidates_ms=candidates_ms,
            score_ms=score_ms,
            total_ms=total_ms,
        ),
    )


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


def format_query_results_human(payload: RetrievalResult | dict[str, Any], include_bundle: bool = False) -> str:
    results = payload_results(payload)
    lines = [
        f"query: {_payload_get(payload, 'query', '')}",
        f"scope: {_payload_get(payload, 'scope', 'all')}",
        f"retrieval_mode: {_payload_get(payload, 'retrieval_mode', 'legacy')}",
    ]
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
        rationale = ", ".join(result_get(item, "reasons", [])[:3])
        level = result_get(item, "disclosure_level", "")
        level_segment = f"{level} | " if level else ""
        wc = result_get(item, "word_count", 0) or 0
        cost_hint = f" ~{wc}w" if wc else ""
        lines.append(
            f"- score {result_get(item, 'score', 0.0):.3f} | "
            f"{result_get(item, 'type', '')} | {result_get(item, 'rel_path', '')}{cost_hint} | "
            f"{level_segment}{rationale}"
        )

    if include_bundle:
        lines.append("")
        lines.append("Bundle:")
        for chunk in bundle_results(results, word_budget=1200):
            lines.append(f"- {chunk['title']} [{chunk['path']}]")
            lines.append(f"  {shorten(chunk['excerpt'], 260)}")

    return "\n".join(lines)

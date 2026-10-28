"""Serialization operations for query."""

from __future__ import annotations

from typing import Any
from ledger.retrieval_types import RetrievalCandidate
from ledger.retrieval_types import RetrievalResult
from ledger.retrieval_types import ScoredResult



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



def _trust_to_dict(item: Any) -> dict[str, Any] | None:
    """Serialize a result's trust verdict (plan 46) to {level, reason, score}."""
    verdict = result_get(item, "trust", None) if isinstance(item, dict) else getattr(item, "trust", None)
    if verdict is None:
        return None
    if isinstance(verdict, dict):
        return verdict
    return {
        "level": str(getattr(verdict, "level", "") or ""),
        "reason": str(getattr(verdict, "reason", "") or ""),
        "score": round(float(getattr(verdict, "score", 0.0) or 0.0), 4),
    }



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
        "trust": _trust_to_dict(item),
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
    trust = _trust_to_dict(item)
    if trust is not None:
        data["trust"] = trust
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



def _tier1_result_to_json(item: dict[str, Any]) -> dict[str, Any]:
    """Serialise a single tier-1 result for JSON output.

    Metadata is deliberately omitted (privacy). Content is capped at 280 chars.
    """
    content = item.get("content", "") or ""
    return {
        "_tier": 1,
        "id": item.get("id", ""),
        "kind": item.get("kind", ""),
        "source": item.get("source", ""),
        "timestamp": item.get("timestamp", ""),
        "subject": item.get("subject", ""),
        "content": content[:280],
        "score": round(float(item.get("score", 0.0)), 6),
        "rrf": round(float(item.get("rrf", 0.0)), 8),
    }



def query_result_to_json(
    payload: RetrievalResult | dict[str, Any],
    *,
    include_bundle: bool = False,
    bundle_word_budget: int = 1200,
    view: str = "context",
) -> dict[str, Any]:
    # Handle fused payload (contains "fusion" key) — already a dict.
    if isinstance(payload, dict) and "fusion" in payload:
        out = dict(payload)
        # Re-serialise results with tier-aware routing.
        formatter = _VIEW_FORMATTERS.get(view, _result_context_fields)
        serialised: list[dict[str, Any]] = []
        for item in payload.get("results", []):
            if item.get("_tier") == 1:
                serialised.append(_tier1_result_to_json(item))
            else:
                # Tier-2: use the standard formatter on the dict.
                serialised.append(formatter(item))
        out["results"] = serialised
        # Copy fusion block verbatim.
        out["fusion"] = payload["fusion"]
        return out

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
        from ledger.querying.formatting import bundle_results
        out["bundle"] = bundle_results(results, word_budget=bundle_word_budget)
    return out


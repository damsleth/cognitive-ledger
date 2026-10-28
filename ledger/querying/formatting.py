"""Formatting operations for query."""

from __future__ import annotations

from typing import Any
from ledger.retrieval_types import RetrievalResult
from ledger.retrieval_types import ScoredResult
from ledger.parsing import shorten
from ledger.querying.serialization import _payload_get, _trust_to_dict, payload_results, result_get


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



def _format_fused(payload: dict[str, Any], view: str = "context") -> str:
    """Format a fused (tier-1 + tier-2) result payload as human-readable text."""
    fusion = payload.get("fusion", {})
    tier2_count = fusion.get("tier2_count", 0)
    tier1_count = fusion.get("tier1_count", 0)
    boost = fusion.get("tier2_boost", 0.0)
    unavailable = fusion.get("unavailable_reason")

    results = payload.get("results", [])
    tier2_results = [r for r in results if r.get("_tier") == 2]
    tier1_results_list = [r for r in results if r.get("_tier") == 1]

    lines = [
        f"query: {payload.get('query', '')}",
        f"scope: {payload.get('scope', 'all')}",
        f"retrieval_mode: {payload.get('retrieval_mode', 'legacy')}",
        f"fusion: tier2={tier2_count} tier1={tier1_count} boost={boost}",
    ]
    if unavailable:
        lines.append(f"tier1_warning: {unavailable}")

    lines.append(f"\nTier 2 results ({len(tier2_results)}):")
    for item in tier2_results:
        wc = item.get("word_count", 0) or 0
        cost_hint = f" ~{wc}w" if wc else ""
        rrf = item.get("rrf", 0.0)
        rationale = ", ".join((item.get("reasons") or [])[:3])
        level = item.get("disclosure_level", "")
        level_segment = f"{level} | " if level else ""
        lines.append(
            f"- {rrf:.4f} | {item.get('type', '')} | "
            f"{item.get('rel_path', '') or item.get('path', '')}{cost_hint} | "
            f"{level_segment}{rationale}"
        )

    lines.append(f"\nTier 1 results ({len(tier1_results_list)}):")
    for item in tier1_results_list:
        # Display: rrf | kind | display_id | timestamp
        # build display_id from id
        raw_id = item.get("id", "")
        display_id = f"yaams:{raw_id[:24]}" if raw_id else "yaams:?"
        ts = item.get("timestamp", "")[:10] if item.get("timestamp") else ""
        rrf = item.get("rrf", 0.0)
        subject = item.get("subject", "")
        content = item.get("content", "")
        preview = shorten(content, 80) if content else ""
        lines.append(
            f"- {rrf:.4f} | {item.get('kind', '')} | {display_id} | {ts}"
        )
        if subject:
            lines.append(f"  subject: {subject}")
        if preview:
            lines.append(f"  \"{preview}\"")

    return "\n".join(lines)



def format_query_results_human(
    payload: RetrievalResult | dict[str, Any],
    include_bundle: bool = False,
    view: str = "context",
) -> str:
    # Check for fused payload (contains "fusion" key).
    if isinstance(payload, dict) and "fusion" in payload:
        return _format_fused(payload, view=view)

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
            trust = _trust_to_dict(item)
            if trust is not None:
                lines.append(f"  trust: {trust['level']} — {trust['reason']}")
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


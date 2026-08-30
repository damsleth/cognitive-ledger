"""Shared semantic-search helpers for script and library consumers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator


@dataclass
class SourceSearchItem:
    rel_path: str = ""
    abs_path: str = ""
    type: str = ""
    scope: str = ""
    status: str = ""
    lang: str = ""
    updated: str = ""
    cosine_similarity: float = 0.0


@dataclass
class SourceSearchResult:
    mode: str = "source_only"
    target: str = "source"
    backend: str = "local"
    model: str = ""
    available: bool = False
    reason: str | None = None
    results: list[SourceSearchItem] = field(default_factory=list)


_EMBEDDINGS_MODULE_CACHE: Any | None = None


def reset_embeddings_cache() -> None:
    """Clear the cached embeddings module for test isolation."""
    global _EMBEDDINGS_MODULE_CACHE
    _EMBEDDINGS_MODULE_CACHE = None


def load_embeddings_module() -> Any:
    global _EMBEDDINGS_MODULE_CACHE
    if _EMBEDDINGS_MODULE_CACHE is not None:
        return _EMBEDDINGS_MODULE_CACHE

    from ledger import embeddings as module
    _EMBEDDINGS_MODULE_CACHE = module
    return _EMBEDDINGS_MODULE_CACHE


def resolve_embed_model(
    backend: str,
    embed_model: str | None,
    *,
    load_embeddings_module_fn: Callable[..., Any] = load_embeddings_module,
) -> str:
    embeddings = load_embeddings_module_fn()
    # config.yaml is authoritative when no explicit model is supplied.
    return str(embeddings.configured_model_for_backend(backend, embed_model))


def semantic_search_source(
    query: str,
    *,
    source_root: str | Path,
    limit: int,
    embed_backend: str,
    embed_model: str | None = None,
    allow_api_on_source: bool = False,
    load_embeddings_module_fn: Callable[..., Any] = load_embeddings_module,
    resolve_embed_model_fn: Callable[..., str] = resolve_embed_model,
) -> SourceSearchResult:
    embeddings = load_embeddings_module_fn()
    model = resolve_embed_model_fn(
        embed_backend,
        embed_model,
        load_embeddings_module_fn=load_embeddings_module_fn,
    )
    payload = embeddings.semantic_search(
        query=query,
        target="source",
        backend=embed_backend,
        model=model,
        limit=limit,
        source_root=Path(source_root).expanduser().resolve(),
        allow_api_on_source=allow_api_on_source,
    )

    items = [
        SourceSearchItem(
            rel_path=str(item.get("rel_path", "") or ""),
            abs_path=str(item.get("abs_path", "") or ""),
            type=str(item.get("type", "") or ""),
            scope=str(item.get("scope", "") or ""),
            status=str(item.get("status", "") or ""),
            lang=str(item.get("lang", "") or ""),
            updated=str(item.get("updated", "") or ""),
            cosine_similarity=float(item.get("cosine_similarity", 0.0) or 0.0),
        )
        for item in payload.get("results", [])
    ]

    return SourceSearchResult(
        mode="source_only",
        target=str(payload.get("target", "source") or "source"),
        backend=str(payload.get("backend", embed_backend) or embed_backend),
        model=str(payload.get("model", model) or model),
        available=bool(payload.get("available", False)),
        reason=str(payload.get("reason")) if payload.get("reason") is not None else None,
        results=items,
    )


def semantic_search_target(
    query: str,
    *,
    target: str = "ledger",
    limit: int = 5,
    embed_backend: str = "local",
    embed_model: str | None = None,
    allow_api_on_source: bool = False,
    load_embeddings_module_fn: Callable[[], Any] | None = None,
    resolve_embed_model_fn: Callable[..., str] | None = None,
) -> dict[str, Any]:
    if load_embeddings_module_fn is None:
        load_embeddings_module_fn = load_embeddings_module
    if resolve_embed_model_fn is None:
        resolve_embed_model_fn = resolve_embed_model
    embeddings = load_embeddings_module_fn()
    resolved_model = resolve_embed_model_fn(
        embed_backend,
        embed_model,
        load_embeddings_module_fn=load_embeddings_module_fn,
    )
    payload = embeddings.semantic_search(
        query=query,
        target=target,
        backend=embed_backend,
        model=resolved_model,
        limit=limit,
        allow_api_on_source=allow_api_on_source,
    )

    if not payload.get("available", False):
        return {
            "target": str(payload.get("target", target) or target),
            "backend": str(payload.get("backend", embed_backend) or embed_backend),
            "model": str(payload.get("model", resolved_model) or resolved_model),
            "available": False,
            "reason": str(payload.get("reason") or ""),
            "index_built_at": str(payload.get("index_built_at", "") or ""),
            "index_item_count": int(payload.get("index_item_count", 0) or 0),
            "results": [],
        }

    _DROP = {"score_by_id", "score_by_rel_path", "abs_path", "content_hash", "row", "id", "embedding_text"}
    projected_results = [
        {
            "rel_path": str(item.get("rel_path", "") or ""),
            "type": str(item.get("type", "") or ""),
            "scope": str(item.get("scope", "") or ""),
            "status": str(item.get("status", "") or ""),
            "lang": str(item.get("lang", "") or ""),
            "updated": str(item.get("updated", "") or ""),
            "cosine_similarity": float(item.get("cosine_similarity", 0.0) or 0.0),
        }
        for item in payload.get("results", [])
    ]

    return {
        "target": str(payload.get("target", target) or target),
        "backend": str(payload.get("backend", embed_backend) or embed_backend),
        "model": str(payload.get("model", resolved_model) or resolved_model),
        "available": True,
        "reason": str(payload.get("reason") or ""),
        "index_built_at": str(payload.get("index_built_at", "") or ""),
        "index_item_count": int(payload.get("index_item_count", 0) or 0),
        "results": projected_results,
    }


def batch_semantic_search_lines(
    lines: Iterable[str],
    *,
    default_target: str = "ledger",
    default_limit: int = 5,
    embed_backend: str = "local",
    embed_model: str | None = None,
    allow_api_on_source: bool = False,
    search_fn: Callable[..., dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Run one semantic search per JSONL request line, in input order.

    Backs ``ledger embed search --batch``. Each line is a JSON object
    ``{"query": str, "limit": int?, "target": "ledger"|"source"?}``; missing
    keys fall back to ``default_limit`` / ``default_target``. Yields one dict
    per non-blank line: the same payload shape as ``semantic_search_target``
    on success, or ``{"error": "..."}`` for that line's failure — a bad line
    never aborts the rest of the batch. Blank lines are skipped.

    All searches run in this process, so the embedding encoder is loaded once
    (module-level cache in ``ledger.embeddings``) instead of once per query —
    the point of batch mode versus one subprocess per statement.

    ``search_fn`` is injectable for tests and defaults to
    ``semantic_search_target``.
    """
    from ledger.validation import validate_limit, validate_query

    if search_fn is None:
        search_fn = semantic_search_target
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            yield {"error": f"invalid JSON: {exc}"}
            continue
        if not isinstance(request, dict):
            yield {"error": "each line must be a JSON object with a 'query' key"}
            continue
        target = str(request.get("target") or default_target)
        if target not in ("ledger", "source"):
            yield {"error": f"invalid target: {target!r} (expected 'ledger' or 'source')"}
            continue
        try:
            query = validate_query(request.get("query"))
            limit = validate_limit(request.get("limit", default_limit), min_val=1, max_val=100)
        except Exception as exc:  # QueryValidationError, ValueError, TypeError
            yield {"error": str(exc)}
            continue
        try:
            yield search_fn(
                query,
                target=target,
                limit=limit,
                embed_backend=embed_backend,
                embed_model=embed_model,
                allow_api_on_source=allow_api_on_source,
            )
        except Exception as exc:  # one bad search must not kill the batch
            yield {"error": str(exc)}


def format_embed_search_human(payload: dict[str, Any]) -> str:
    lines = [
        f"target: {payload.get('target', 'ledger')}",
        f"backend: {payload.get('backend', 'local')}",
        f"model: {payload.get('model', '')}",
    ]
    if not payload.get("available", False):
        lines.append(f"available: no ({payload.get('reason', 'unknown')})")
        return "\n".join(lines)

    lines.append(f"results: {len(payload.get('results', []))}")
    for item in payload.get("results", []):
        lines.append(
            "- "
            f"{float(item.get('cosine_similarity', 0.0)):.3f} | "
            f"{item.get('rel_path', '')} | "
            f"{item.get('scope', '')}"
        )
    return "\n".join(lines)


def build_semantic_index(
    *,
    target: str,
    backend: str,
    model: str | None = None,
    source_root: str | Path | None = None,
    text_template: str | None = None,
    device: str | None = None,
    batch_size: int | None = None,
    load_embeddings_module_fn: Callable[..., Any] = load_embeddings_module,
    resolve_embed_model_fn: Callable[..., str] = resolve_embed_model,
) -> dict[str, Any]:
    embeddings = load_embeddings_module_fn()
    resolved_model = resolve_embed_model_fn(
        backend,
        model,
        load_embeddings_module_fn=load_embeddings_module_fn,
    )
    resolved_source_root = Path(source_root).expanduser().resolve() if source_root is not None else None
    return embeddings.build_indices(
        target=target,
        backend=backend,
        model=resolved_model,
        source_root=resolved_source_root,
        text_template=text_template,
        device=device,
        batch_size=batch_size,
    )


def semantic_index_status(
    *,
    target: str,
    load_embeddings_module_fn: Callable[..., Any] = load_embeddings_module,
) -> dict[str, Any]:
    embeddings = load_embeddings_module_fn()
    return embeddings.index_status(target=target)


def clean_semantic_indices(
    *,
    target: str,
    load_embeddings_module_fn: Callable[..., Any] = load_embeddings_module,
) -> dict[str, Any]:
    embeddings = load_embeddings_module_fn()
    return embeddings.clean_indices(target=target)


def source_search_result_to_dict(result: SourceSearchResult | dict[str, Any]) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    return {
        "mode": result.mode,
        "target": result.target,
        "backend": result.backend,
        "model": result.model,
        "available": result.available,
        "reason": result.reason,
        "results": [
            {
                "rel_path": item.rel_path,
                "abs_path": item.abs_path,
                "type": item.type,
                "scope": item.scope,
                "status": item.status,
                "lang": item.lang,
                "updated": item.updated,
                "cosine_similarity": round(float(item.cosine_similarity), 6),
            }
            for item in result.results
        ],
    }


def format_source_search_human(result: SourceSearchResult | dict[str, Any]) -> str:
    payload = source_search_result_to_dict(result)
    lines = [
        f"mode: {payload.get('mode', 'source_only')}",
        f"backend: {payload.get('backend', 'local')}",
        f"model: {payload.get('model', '')}",
    ]
    if not payload.get("available", False):
        lines.append(f"available: no ({payload.get('reason', 'unknown')})")
        return "\n".join(lines)

    lines.append(f"results: {len(payload.get('results', []))}")
    for item in payload.get("results", []):
        lines.append(
            "- "
            f"{float(item.get('cosine_similarity', 0.0)):.3f} | "
            f"{item.get('rel_path', '')} | "
            f"{item.get('scope', '')}"
        )
    return "\n".join(lines)


def format_embed_build_human(payload: dict[str, Any]) -> str:
    lines = [
        f"target: {payload['target']}",
        f"backend: {payload['backend']}",
        f"model: {payload['model']}",
    ]
    for result in payload.get("results", []):
        lines.append(
            "- "
            f"{result.get('target')} "
            f"items={result.get('item_count', 0)} "
            f"dims={result.get('dims', 0)} "
            f"embedded={result.get('embedded_count', 0)} "
            f"reused={result.get('reused_count', 0)} "
            f"removed={result.get('removed_count', 0)}"
        )
    return "\n".join(lines)


def format_embed_status_human(payload: dict[str, Any]) -> str:
    lines = [f"target: {payload.get('target')}"]
    for target_name, entries in payload.get("targets", {}).items():
        lines.append(f"{target_name}:")
        if not entries:
            lines.append("- none")
            continue
        for entry in entries:
            lines.append(
                "- "
                f"{entry.get('backend','')}/{entry.get('model','')} "
                f"items={entry.get('item_count', 0)} "
                f"dims={entry.get('dims', 0)} "
                f"built_at={entry.get('built_at', '')}"
            )
    return "\n".join(lines)


def format_embed_clean_human(payload: dict[str, Any]) -> str:
    lines = [f"target: {payload.get('target')}"]
    removed = payload.get("removed", [])
    pruned = payload.get("manifest_pruned", [])
    if not removed and not pruned:
        lines.append("removed: none")
        return "\n".join(lines)
    for path in removed:
        lines.append(f"- removed {path}")
    if pruned:
        lines.append(f"- pruned manifest entries: {', '.join(pruned)}")
    return "\n".join(lines)

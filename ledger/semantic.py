"""Shared semantic-search helpers for script and library consumers."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


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


def load_embeddings_module(scripts_dir: str | Path | None = None) -> Any:
    global _EMBEDDINGS_MODULE_CACHE
    if _EMBEDDINGS_MODULE_CACHE is not None:
        return _EMBEDDINGS_MODULE_CACHE

    if scripts_dir is not None:
        resolved_dir = str(Path(scripts_dir).resolve())
        if resolved_dir not in sys.path:
            sys.path.insert(0, resolved_dir)

    import ledger_embeddings as module

    _EMBEDDINGS_MODULE_CACHE = module
    return _EMBEDDINGS_MODULE_CACHE


def resolve_embed_model(
    backend: str,
    embed_model: str | None,
    *,
    load_embeddings_module_fn: Callable[..., Any] = load_embeddings_module,
) -> str:
    if embed_model:
        return str(embed_model).strip()
    embeddings = load_embeddings_module_fn()
    return str(embeddings.default_model_for_backend(backend))


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


def build_semantic_index(
    *,
    target: str,
    backend: str,
    model: str | None = None,
    source_root: str | Path | None = None,
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
    if not removed:
        lines.append("removed: none")
        return "\n".join(lines)
    for path in removed:
        lines.append(f"- removed {path}")
    return "\n".join(lines)

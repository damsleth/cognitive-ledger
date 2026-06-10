"""Search service - small wrapper over ``ledger.query.rank_query``.

Surfaces two retrieval modes to the UI ("lexical" -> ``legacy``,
"semantic" -> ``semantic_hybrid``) and caches results for 60 s by
``(query, mode, scope, limit)`` so rapid typing stays snappy. The
semantic path is opt-in because cold model loads can take 1-2s.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

from ledger.config import get_config
from ledger.parsing import shorten
from ledger.query import rank_query as cli_rank_query, payload_results, result_get


UI_MODES: dict[str, str] = {
    "lexical": "legacy",
    "semantic": "semantic_hybrid",
}

DEFAULT_MODE = "lexical"
DEFAULT_SCOPE = "all"
DEFAULT_LIMIT = 12
CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class SearchHit:
    stem: str
    type: str
    title: str
    snippet: str
    score: float
    scope: str
    status: str
    tags: tuple[str, ...]
    updated: str


@dataclass(frozen=True)
class SearchResults:
    query: str
    mode: str
    scope: str
    limit: int
    hits: tuple[SearchHit, ...]
    elapsed_ms: float
    semantic_available: bool

    @property
    def empty(self) -> bool:
        return not self.hits


class Searcher:
    """In-process cache + adapter over the canonical rank_query."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str, str, int], tuple[float, SearchResults]] = {}
        self._lock = Lock()

    def search(
        self,
        query: str,
        *,
        mode: str = DEFAULT_MODE,
        scope: str = DEFAULT_SCOPE,
        limit: int = DEFAULT_LIMIT,
    ) -> SearchResults:
        query = (query or "").strip()
        if not query:
            return SearchResults(
                query="", mode=mode, scope=scope, limit=limit,
                hits=(), elapsed_ms=0.0, semantic_available=False,
            )

        ui_mode = mode if mode in UI_MODES else DEFAULT_MODE
        scope = scope if scope in get_config().query_scopes else DEFAULT_SCOPE
        limit = max(1, min(limit, 50))

        key = (query, ui_mode, scope, limit)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
                return cached[1]

        results = self._uncached_search(query, ui_mode, scope, limit)

        with self._lock:
            self._cache[key] = (now, results)
            # Trim opportunistically; this is single-process and bounded.
            if len(self._cache) > 64:
                oldest = min(self._cache.items(), key=lambda kv: kv[1][0])[0]
                self._cache.pop(oldest, None)
        return results

    def _uncached_search(
        self, query: str, ui_mode: str, scope: str, limit: int
    ) -> SearchResults:
        backend_mode = UI_MODES[ui_mode]
        t0 = time.monotonic()
        cfg = get_config()
        payload = cli_rank_query(
            query=query,
            scope=scope,
            limit=limit,
            aliases_path=cfg.aliases_path,
            retrieval_mode=backend_mode,
            embed_backend=cfg.embed_backend,
            embed_model=cfg.embed_model,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000.0

        hits: list[SearchHit] = []
        for raw in payload_results(payload):
            stem = _stem_from_result(raw)
            if not stem:
                continue
            hits.append(
                SearchHit(
                    stem=stem,
                    type=str(result_get(raw, "type", "fact") or "fact"),
                    title=str(result_get(raw, "title", "") or stem),
                    snippet=_make_snippet(raw),
                    score=float(result_get(raw, "score", 0.0) or 0.0),
                    scope=str(result_get(raw, "scope", "") or ""),
                    status=str(result_get(raw, "status", "") or ""),
                    tags=tuple(result_get(raw, "tags", []) or ()),
                    updated=str(result_get(raw, "updated", "") or "")[:10],
                )
            )

        semantic_meta = _payload_get(payload, "semantic") or {}
        semantic_available = bool(
            isinstance(semantic_meta, dict) and semantic_meta.get("available")
        )

        return SearchResults(
            query=query,
            mode=ui_mode,
            scope=scope,
            limit=limit,
            hits=tuple(hits),
            elapsed_ms=elapsed_ms,
            semantic_available=semantic_available,
        )

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()


def _payload_get(payload, key, default=None):
    if isinstance(payload, dict):
        return payload.get(key, default)
    return getattr(payload, key, default)


def _stem_from_result(raw: Any) -> str:
    """Derive a filename stem from a query result row."""
    rel = result_get(raw, "rel_path", None) or result_get(raw, "path", "")
    rel = str(rel or "")
    if not rel:
        return ""
    last = rel.rsplit("/", 1)[-1]
    if last.endswith(".md"):
        last = last[:-3]
    return last


def _make_snippet(raw: Any, width: int = 220) -> str:
    candidate = (
        result_get(raw, "snippet", None)
        or result_get(raw, "statement", None)
        or result_get(raw, "body", "")
    )
    text = str(candidate or "").strip()
    if not text:
        return ""
    # Collapse whitespace for compact display.
    return shorten(" ".join(text.split()), width)

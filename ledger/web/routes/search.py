"""Search route - lexical + semantic_hybrid via :class:`Searcher`.

Renders the full ``search.html`` page on a normal GET, or just the
``_search_results.html`` fragment when called from HTMX (the
``HX-Request`` header). The fragment swap lets the user keep typing
without a full reload.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ledger.web.services.search import (
    DEFAULT_LIMIT,
    DEFAULT_MODE,
    DEFAULT_SCOPE,
    Searcher,
    UI_MODES,
)

router = APIRouter()


def _searcher(request: Request) -> Searcher:
    searcher = getattr(request.app.state, "searcher", None)
    if searcher is None:
        searcher = Searcher()
        request.app.state.searcher = searcher
    return searcher


@router.get("/search", response_class=HTMLResponse)
async def search(
    request: Request,
    q: str = "",
    mode: str = DEFAULT_MODE,
    scope: str = DEFAULT_SCOPE,
    limit: int = DEFAULT_LIMIT,
) -> HTMLResponse:
    corpus = request.app.state.corpus
    templates = request.app.state.templates
    searcher = _searcher(request)

    if mode not in UI_MODES:
        mode = DEFAULT_MODE

    results = searcher.search(q, mode=mode, scope=scope, limit=limit)
    types = corpus.note_types()
    scopes = list(request.app.state.config.query_scopes)

    is_htmx = request.headers.get("HX-Request") == "true"
    template_name = "_search_results.html" if is_htmx else "search.html"

    return templates.TemplateResponse(
        request,
        template_name,
        {
            "types": types,
            "active_type": None,
            "active_label": "Search",
            "results": results,
            "modes": list(UI_MODES.keys()),
            "scopes": scopes,
            "q": q,
            "mode": mode,
            "scope": scope,
            "limit": limit,
        },
    )

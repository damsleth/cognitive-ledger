"""Admin and status routes.

These endpoints back the operations layer of the web UI:

- ``GET /healthz`` is a cheap JSON status probe used by tests, scripts,
  and the eventual status bar in ``base.html``.
- ``POST /admin/reload`` re-scans the corpus from disk and invalidates
  the search cache. Use after ``ledger sleep index`` to pick up new
  notes without restarting the server.

Both routes route through existing services; no parallel parsing.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ledger.retrieval import load_note_index
from ledger.web.services.corpus import Corpus
from ledger.web.services.search import Searcher


router = APIRouter()


def _corpus(request: Request) -> Corpus:
    return request.app.state.corpus


def _searcher(request: Request) -> Searcher | None:
    return getattr(request.app.state, "searcher", None)


def _embeddings_enabled(corpus: Corpus) -> bool:
    """Cheap check: do any built semantic indexes exist on disk?

    Avoids importing the embeddings module so ``/healthz`` stays fast
    and dependency-free for environments without numpy.
    """
    semantic_root = corpus.config.semantic_root
    if not semantic_root.is_dir():
        return False
    for target_dir in semantic_root.iterdir():
        if not target_dir.is_dir():
            continue
        for backend_dir in target_dir.iterdir():
            if (backend_dir / "index.json").is_file():
                return True
    return False


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    corpus = _corpus(request)
    types = corpus.note_types()
    notes_loaded = sum(t.count for t in types)
    index = load_note_index()
    return JSONResponse(
        {
            "ok": True,
            "notes_loaded": notes_loaded,
            "embeddings_enabled": _embeddings_enabled(corpus),
            "index_built_at": str(index.get("built", "") or ""),
        }
    )


@router.post("/admin/reload")
async def admin_reload(request: Request) -> JSONResponse:
    corpus = _corpus(request)
    corpus.reload()
    searcher = _searcher(request)
    if searcher is not None:
        searcher.invalidate()
    types = corpus.note_types()
    return JSONResponse(
        {
            "ok": True,
            "notes_loaded": sum(t.count for t in types),
            "search_cache_cleared": searcher is not None,
        }
    )

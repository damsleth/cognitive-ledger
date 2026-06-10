"""Browse routes: root, type listing, recent activity."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ledger.web.services.corpus import Corpus

router = APIRouter()


def _corpus(request: Request) -> Corpus:
    return request.app.state.corpus


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/browse", status_code=307)


@router.get("/browse", response_class=HTMLResponse)
def browse_index(request: Request) -> HTMLResponse:
    corpus = _corpus(request)
    templates = request.app.state.templates
    types = corpus.note_types()
    recent = corpus.recent(limit=30)
    return templates.TemplateResponse(
        request,
        "browse.html",
        {
            "active_type": None,
            "active_label": "Recent",
            "types": types,
            "items": recent,
            "total": sum(t.count for t in types),
        },
    )


@router.get("/browse/{type_key}", response_class=HTMLResponse)
def browse_type(
    request: Request,
    type_key: str,
    status: str | None = None,
) -> HTMLResponse:
    corpus = _corpus(request)
    templates = request.app.state.templates
    types = corpus.note_types()

    if type_key == "all":
        items = corpus.list_by_type("all")
        active_label = "All notes"
    else:
        info = corpus.note_type(type_key)
        if info is None:
            raise HTTPException(status_code=404, detail=f"Unknown note type: {type_key}")
        loop_status = status if type_key == "loops" else None
        items = corpus.list_by_type(type_key, loop_status=loop_status)
        active_label = info.label

    return templates.TemplateResponse(
        request,
        "browse.html",
        {
            "active_type": type_key,
            "active_label": active_label,
            "types": types,
            "items": items,
            "total": len(items),
            "loop_status_filter": status if type_key == "loops" else None,
        },
    )

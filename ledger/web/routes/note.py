"""Single-note detail route."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ledger.web.services.corpus import Corpus
from ledger.web.services.render import render_body

router = APIRouter()


def _corpus(request: Request) -> Corpus:
    return request.app.state.corpus


@router.get("/note/{stem}", response_class=HTMLResponse)
async def note_detail(request: Request, stem: str) -> HTMLResponse:
    corpus = _corpus(request)
    templates = request.app.state.templates
    item = corpus.get_by_stem(stem)
    if item is None:
        raise HTTPException(status_code=404, detail=f"No note with stem {stem!r}")

    rendered = render_body(item.body, corpus)
    types = corpus.note_types()

    return templates.TemplateResponse(
        request,
        "note.html",
        {
            "types": types,
            "active_type": item.type,
            "active_label": item.title or stem,
            "item": item,
            "stem": stem,
            "body_html": rendered.html,
            "broken_links": rendered.broken_links,
        },
    )

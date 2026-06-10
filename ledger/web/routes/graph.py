"""Graph view routes.

GET /graph         -> renders graph.html template
GET /graph/data.json -> JSON payload for the d3 force graph
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ledger.web.services.corpus import Corpus

router = APIRouter()


def _corpus(request: Request) -> Corpus:
    return request.app.state.corpus


@router.get("/graph", response_class=HTMLResponse)
async def graph_page(request: Request) -> HTMLResponse:
    corpus = _corpus(request)
    templates = request.app.state.templates
    types = corpus.note_types()
    return templates.TemplateResponse(
        request,
        "graph.html",
        {
            "active_type": "graph",
            "active_label": "Graph",
            "types": types,
        },
    )


@router.get("/graph/data.json")
async def graph_data(request: Request) -> JSONResponse:
    corpus = _corpus(request)
    return JSONResponse(corpus.graph())

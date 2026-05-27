"""Signals dashboard route — read-only feedback-loop insight.

Renders the same aggregate as ``ledger review --stats`` (coverage,
score distribution, correction backlog, top retrieval-miss gaps, and
the activation status) as an HTML page.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/signals", response_class=HTMLResponse)
async def signals_dashboard(request: Request) -> HTMLResponse:
    # Imported lazily so the web package doesn't pull review/signals at
    # module-import time (keeps app construction cheap and dependency-light).
    from ledger import review

    templates = request.app.state.templates
    corpus = request.app.state.corpus
    data = review.dashboard_data()

    return templates.TemplateResponse(
        request,
        "signals.html",
        {
            "types": corpus.note_types(),
            "active_type": "signals",
            "active_label": "Signals",
            "data": data,
        },
    )

"""Fast, human-in-the-loop moderation for inbox promotion candidates."""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ledger.inbox import REJECT_REASONS
from ledger.web.services import review as review_service


router = APIRouter()
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _session_stats(form: Any | None = None) -> dict[str, int]:
    stats: dict[str, int] = {
        "processed": 0,
        "accepted": 0,
        "rejected": 0,
        "answered": 0,
        "merged": 0,
        "streak": 0,
    }
    if form is None:
        return stats
    for key in stats:
        try:
            stats[key] = max(0, min(int(str(form.get(key, 0))), 100_000))
        except (TypeError, ValueError):
            pass
    return stats


def _type_choices(request: Request) -> list[Any]:
    return list(request.app.state.corpus.note_types())


def _render_stage(
    request: Request,
    *,
    stats: dict[str, int],
    item: review_service.ReviewItem | None = None,
    error: str | None = None,
) -> HTMLResponse:
    items, source_count = review_service.load_review_queue(
        request.app.state.corpus.notes_dir
    )
    if item is None and items:
        item = items[0]
    return request.app.state.templates.TemplateResponse(
        request,
        "_review_stage.html",
        {
            "item": item,
            "remaining": len(items),
            "source_capture_count": source_count,
            "stats": stats,
            "error": error,
            "csrf_token": request.app.state.review_csrf_token,
            "target_types": _type_choices(request),
            "reject_reasons": REJECT_REASONS,
        },
    )


def _mutation_allowed(request: Request, token: str) -> bool:
    client_host = request.client.host if request.client else ""
    expected = request.app.state.review_csrf_token
    return client_host in _LOOPBACK_HOSTS and hmac.compare_digest(token, expected)


@router.get("/review", response_class=HTMLResponse)
async def review_queue(request: Request) -> HTMLResponse:
    corpus = request.app.state.corpus
    items, source_count = review_service.load_review_queue(corpus.notes_dir)
    return request.app.state.templates.TemplateResponse(
        request,
        "review.html",
        {
            "types": corpus.note_types(),
            "active_type": "review",
            "active_label": "Review",
            "stats": _session_stats(),
            "item": items[0] if items else None,
            "remaining": len(items),
            "source_capture_count": source_count,
            "csrf_token": request.app.state.review_csrf_token,
            "target_types": _type_choices(request),
            "reject_reasons": REJECT_REASONS,
            "error": None,
        },
    )


@router.post("/review/{item_id}/decide", response_class=HTMLResponse)
async def review_decide(request: Request, item_id: str) -> HTMLResponse:
    form = await request.form()
    token = str(form.get("csrf_token", ""))
    if not _mutation_allowed(request, token):
        return JSONResponse(
            {"ok": False, "error": "review mutations require loopback + CSRF token"},
            status_code=403,
        )

    stats = _session_stats(form)
    notes_dir = request.app.state.corpus.notes_dir
    try:
        item = review_service.find_review_item(notes_dir, item_id)
    except FileNotFoundError as exc:
        return _render_stage(request, stats=stats, error=str(exc))

    action = str(form.get("action", ""))
    answer_value = form.get("answer")
    if answer_value is not None:
        action = "answer"

    if action == "skip":
        items = review_service.load_review_items(notes_dir)
        current_index = next(
            (i for i, queued in enumerate(items) if queued.id == item.id),
            0,
        )
        next_item = items[(current_index + 1) % len(items)] if items else None
        return _render_stage(request, stats=stats, item=next_item)

    try:
        if action in {"accept", "answer", "rewrite_accept"}:
            if item.requires_rewrite and action != "rewrite_accept":
                raise ValueError("This batch candidate must be rewritten into one atomic note")
            review_service.approve(
                item,
                notes_dir=notes_dir,
                title=str(form.get("title", item.candidate.title)),
                body=str(form.get("body", item.candidate.body)),
                target_type=str(form.get("target_type", item.candidate.type)),
                answer=str(answer_value) if answer_value is not None else None,
                confirm_conflict=str(form.get("confirm_conflict", "")) == "1",
            )
            stats["processed"] += 1
            stats["accepted"] += 1
            stats["streak"] += 1
            if action == "answer":
                stats["answered"] += 1
        elif action == "reject":
            review_service.reject(
                item,
                notes_dir=notes_dir,
                reason=str(form.get("reason", "discarded")),
            )
            stats["processed"] += 1
            stats["rejected"] += 1
            stats["streak"] = 0
        elif action == "merge":
            review_service.merge(item, notes_dir=notes_dir)
            stats["processed"] += 1
            stats["merged"] += 1
            stats["streak"] += 1
        else:
            raise ValueError("Choose approve, reject, merge, skip, or an answer")
    except (FileNotFoundError, OSError, ValueError) as exc:
        return _render_stage(request, stats=stats, item=item, error=str(exc))

    request.app.state.corpus.reload()
    searcher = getattr(request.app.state, "searcher", None)
    if searcher is not None:
        searcher.invalidate()
    return _render_stage(request, stats=stats)

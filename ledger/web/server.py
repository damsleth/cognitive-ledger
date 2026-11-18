"""FastAPI application factory and uvicorn launcher.

``create_app`` returns a fully wired :class:`fastapi.FastAPI` instance
with the Phase 1 route set mounted. ``run`` is the in-process entry
point used by the ``ledger web`` CLI subcommand.

The corpus is built once at app construction time and attached to
``app.state.corpus``. Route modules pull it from ``request.app.state``
so they remain easy to test with a custom corpus.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from ledger.config import LedgerConfig, get_config
from ledger.web.services.corpus import Corpus

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI


DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
LOGGER = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _require_fastapi():
    """Import FastAPI lazily so the CLI degrades gracefully."""
    try:
        import fastapi  # noqa: F401
        from fastapi import FastAPI
        from fastapi.staticfiles import StaticFiles
        from fastapi.templating import Jinja2Templates

        return FastAPI, StaticFiles, Jinja2Templates
    except ImportError as exc:  # pragma: no cover - covered by error message
        raise RuntimeError(
            "The web UI requires FastAPI. Install with: "
            "pip install 'cognitive-ledger[web]'"
        ) from exc


def _require_uvicorn():
    try:
        import uvicorn

        return uvicorn
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The web UI requires uvicorn. Install with: "
            "pip install 'cognitive-ledger[web]'"
        ) from exc


def create_app(
    *,
    config: LedgerConfig | None = None,
    corpus: Corpus | None = None,
) -> "FastAPI":
    """Build a FastAPI app wired to the given config (or the global one)."""
    FastAPI, StaticFiles, Jinja2Templates = _require_fastapi()

    cfg = config or get_config()
    app = FastAPI(
        title="Cognitive Ledger",
        description="Local web UI for browsing and reviewing the ledger.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
    )

    app.state.config = cfg
    app.state.corpus = corpus or Corpus(cfg)
    app.state.templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    app.state.review_csrf_token = secrets.token_urlsafe(32)

    from starlette.responses import JSONResponse

    token = os.environ.get("LEDGER_WEB_TOKEN") or secrets.token_urlsafe(32)
    app.state.auth_token = token

    @app.middleware("http")
    async def authenticate(request, call_next):
        query_token = request.query_params.get("token", "")
        scheme, _, bearer = request.headers.get("authorization", "").partition(" ")

        def matches(value: str) -> bool:
            return hmac.compare_digest(value.encode("utf-8"), token.encode("utf-8"))

        query_authenticated = matches(query_token)
        if not (
            query_authenticated
            or (scheme.lower() == "bearer" and matches(bearer))
            or matches(request.cookies.get("ledger_web_auth", ""))
        ):
            return JSONResponse(
                {"detail": "Authentication required"}, status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        if query_authenticated:
            response.set_cookie(
                "ledger_web_auth", token, httponly=True, samesite="strict",
                secure=request.url.scheme == "https",
            )
        return response

    # Inject statusbar data as Jinja2 globals so base.html can render without
    # each route having to pass it explicitly.  The corpus reference is captured
    # by closure so the lambda always reads the *current* corpus state.
    _the_corpus = app.state.corpus

    def _statusbar_notes() -> int:
        return sum(t.count for t in _the_corpus.note_types())

    def _statusbar_index_built_at() -> str:
        try:
            from ledger.retrieval import load_note_index

            idx = load_note_index()
            return str(idx.get("built", ""))
        except Exception:
            return ""

    def _statusbar_embeddings() -> bool:
        try:
            semantic_root = _the_corpus.config.semantic_root
            if not semantic_root.is_dir():
                return False
            for target_dir in semantic_root.iterdir():
                if not target_dir.is_dir():
                    continue
                for backend_dir in target_dir.iterdir():
                    if (backend_dir / "index.json").is_file():
                        return True
            return False
        except Exception:
            return False

    def _statusbar_review_count() -> int:
        from ledger.web.services.review import load_review_items

        return len(load_review_items(_the_corpus.notes_dir))

    app.state.templates.env.globals["statusbar_notes"] = _statusbar_notes
    app.state.templates.env.globals["statusbar_index_built_at"] = _statusbar_index_built_at
    app.state.templates.env.globals["statusbar_embeddings"] = _statusbar_embeddings
    app.state.templates.env.globals["statusbar_review_count"] = _statusbar_review_count

    # Singular type labels for the .note-type-pill chips: "facts" -> "fact",
    # "loops" -> "loop". Search hits already carry the singular label, but
    # BrowseItem.type holds the plural key, so we normalize in one place.
    type_labels = {key: meta.get("label", key) for key, meta in cfg.note_types.items()}

    def _type_label(value: str) -> str:
        return type_labels.get(value, value)

    app.state.templates.env.filters["type_label"] = _type_label

    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Local imports keep FastAPI optional at module import time.
    from ledger.web.routes import admin as admin_routes
    from ledger.web.routes import browse as browse_routes
    from ledger.web.routes import graph as graph_routes
    from ledger.web.routes import note as note_routes
    from ledger.web.routes import review as review_routes
    from ledger.web.routes import search as search_routes
    from ledger.web.routes import signals as signals_routes

    app.include_router(admin_routes.router)
    app.include_router(browse_routes.router)
    app.include_router(graph_routes.router)
    app.include_router(note_routes.router)
    app.include_router(review_routes.router)
    app.include_router(search_routes.router)
    app.include_router(signals_routes.router)

    return app


def run(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    reload: bool = False,
    log_level: str = "info",
) -> None:
    """Launch uvicorn against :func:`create_app` (synchronous, blocking).

    Remote binding requires explicit opt-in via LEDGER_WEB_ALLOW_REMOTE.
    """
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.lower() == "localhost"
    if not loopback and os.environ.get("LEDGER_WEB_ALLOW_REMOTE", "").lower() not in {
        "1", "true", "yes", "on",
    }:
        raise ValueError("Non-loopback binding requires LEDGER_WEB_ALLOW_REMOTE=1")

    uvicorn = _require_uvicorn()
    cfg = get_config()
    token = os.environ.get("LEDGER_WEB_TOKEN") or secrets.token_urlsafe(32)
    # The reload subprocess must use the same token printed by its parent.
    previous_token = os.environ.get("LEDGER_WEB_TOKEN")

    notes_dir = cfg.ledger_notes_dir
    url_host = f"[{host}]" if ":" in host else host
    print(
        f"Serving cognitive-ledger at http://{url_host}:{port}/?{urlencode({'token': token})}",
        file=sys.stderr,
    )
    print(f"  notes_dir = {notes_dir}", file=sys.stderr)
    print("Press Ctrl+C to stop.", file=sys.stderr)

    os.environ["LEDGER_WEB_TOKEN"] = token
    try:
        if reload:
            # uvicorn's reload mode needs an import string, not a factory call.
            uvicorn.run(
                "ledger.web.server:create_app",
                host=host,
                port=port,
                reload=True,
                log_level=log_level,
                factory=True,
            )
        else:
            uvicorn.run(create_app(), host=host, port=port, log_level=log_level)
    finally:
        if previous_token is None:
            os.environ.pop("LEDGER_WEB_TOKEN", None)
        else:
            os.environ["LEDGER_WEB_TOKEN"] = previous_token

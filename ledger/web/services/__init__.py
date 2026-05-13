"""Service layer for the web UI - thin wrappers over ledger.* APIs."""

from __future__ import annotations

from ledger.web.services.corpus import Corpus, NoteTypeInfo
from ledger.web.services.render import render_body

__all__ = ["Corpus", "NoteTypeInfo", "render_body"]

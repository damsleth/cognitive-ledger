"""Local read-only web UI for the cognitive ledger.

Phase 1 of the v1 web interface (see ``.plans/41-web-interface-v1.md``).
The entry point is :func:`ledger.web.server.create_app`, launched in
production via ``ledger web`` (see :func:`ledger.web.server.run`).

All retrieval logic is reused from :mod:`ledger.browse`,
:mod:`ledger.notes`, and :mod:`ledger.parsing` - the web layer adds
presentation only.
"""

from __future__ import annotations

from ledger.web.server import create_app, run

__all__ = ["create_app", "run"]

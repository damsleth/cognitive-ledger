"""Model Context Protocol server for Cognitive Ledger (plan 44).

Exposes the ledger's read verbs (and a gated inbox write) over MCP so any MCP
client (Claude Desktop, Cursor, …) can query/recall/answer against the ledger
with no bespoke glue. Local-first: a stdio process the client spawns — no
network, no auth, no cloud. The `mcp` SDK is an optional dependency; import this
module's `create_server`/`run` only when the `[mcp]` extra is installed.
"""

from __future__ import annotations

from ledger.mcp.server import create_server, run, scrub_for_egress

__all__ = ["create_server", "run", "scrub_for_egress"]

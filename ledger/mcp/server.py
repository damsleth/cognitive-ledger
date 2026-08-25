"""FastMCP server wiring ledger verbs as MCP tools (plan 44).

Every tool response is routed through ``scrub_for_egress`` — a defense-in-depth
private-content gate — before leaving the process. Writes are off unless the
server is started with ``allow_write=True`` and land in the inbox (triage stays
human-gated), never directly in typed folders.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ledger.parsing import strip_private_tags


def _require_mcp():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "The MCP server requires the optional 'mcp' dependency. "
            "Install with: pip install 'cognitive-ledger[mcp]'"
        ) from exc
    return FastMCP


def scrub_for_egress(obj: Any) -> Any:
    """Recursively strip <private>…</private> content from any string in *obj*.

    The retrieval path already strips private fences at candidate-build time;
    this is a belt-and-braces gate on every value that leaves via MCP.
    """
    if isinstance(obj, str):
        return strip_private_tags(obj)
    if isinstance(obj, dict):
        return {k: scrub_for_egress(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_for_egress(v) for v in obj]
    return obj


def _parse_date(raw: str | None):
    """Parse a date or ISO timestamp; naive values are assumed UTC.

    datetime.fromisoformat (3.11+) already covers YYYY-MM-DD, trailing "Z",
    offsets and fractional seconds -- all of which the previous strptime/date
    pair rejected, raising an uncaught ValueError out of the MCP tool.
    """
    if not raw:
        return None
    parsed = dt.datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def create_server(*, config=None, allow_write: bool = False, with_yaams: bool = False):
    """Build (but do not run) the FastMCP server with ledger tools."""
    FastMCP = _require_mcp()
    from ledger.config import get_config, set_config
    from ledger.query import query_result_to_json, rank_query
    from ledger.validation import validate_scope

    if config is not None:
        set_config(config)

    mcp = FastMCP("cognitive-ledger")

    @mcp.tool()
    def ledger_query(query: str, scope: str = "all", limit: int = 8, view: str = "context") -> dict:
        """Rank ledger notes for a query. Returns scored results with trust verdicts."""
        scope = validate_scope(scope)
        payload = rank_query(query=query, scope=scope, limit=limit,
                             aliases_path=get_config().aliases_path,
                             retrieval_mode=get_config().retrieval_mode)
        return scrub_for_egress(query_result_to_json(payload, view=view))

    @mcp.tool()
    def ledger_recall_as_of(query: str, as_of: str, scope: str = "all", limit: int = 8) -> dict:
        """Recall notes valid at a point in time (YYYY-MM-DD or full ISO)."""
        scope = validate_scope(scope)
        payload = rank_query(query=query, scope=scope, limit=limit,
                             aliases_path=get_config().aliases_path,
                             retrieval_mode=get_config().retrieval_mode,
                             as_of=_parse_date(as_of))
        return scrub_for_egress(query_result_to_json(payload, view="context"))

    @mcp.tool()
    def ledger_changed_since(query: str, since: str, scope: str = "all", limit: int = 8) -> dict:
        """Return notes matching *query* created/updated on or after *since*."""
        scope = validate_scope(scope)
        payload = rank_query(query=query, scope=scope, limit=limit,
                             aliases_path=get_config().aliases_path,
                             retrieval_mode=get_config().retrieval_mode,
                             changed_since=_parse_date(since))
        return scrub_for_egress(query_result_to_json(payload, view="context"))

    @mcp.tool()
    def ledger_context() -> dict:
        """Return the session boot context (identity + recent state)."""
        from ledger.context import build_context
        return scrub_for_egress({"context": build_context(get_config().ledger_notes_dir)})

    @mcp.tool()
    def ledger_answer(question: str, scope: str = "all", limit: int = 5) -> dict:
        """Synthesize a grounded, cited answer to a question (plan 45)."""
        scope = validate_scope(scope)
        from ledger.synthesize import answer as synth_answer
        result = synth_answer(question, scope=scope, limit=limit)
        return scrub_for_egress(result.to_dict())

    if allow_write:
        @mcp.tool()
        def ledger_remember(text: str, title: str = "", tags: str = "", scope: str = "all") -> dict:
            """Capture a note into the inbox for later human triage (write-gated)."""
            scope = validate_scope(scope)
            return scrub_for_egress(_write_inbox_note(text, title=title, tags=tags, scope=scope))

    if with_yaams:
        @mcp.tool()
        def yaams_query(query: str, limit: int = 10) -> dict:
            """Search tier-1 (YAAMS) digital exhaust. Requires yaams on PATH."""
            import json
            import subprocess
            try:
                proc = subprocess.run(
                    ["yaams", "query", query, "--top-k", str(limit), "--format", "json"],
                    capture_output=True, text=True, timeout=30,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                return {"available": False, "error": str(exc)}
            if proc.returncode != 0:
                return {"available": False, "error": proc.stderr.strip()}
            try:
                return scrub_for_egress({"available": True, "results": json.loads(proc.stdout)})
            except json.JSONDecodeError:
                return {"available": False, "error": "invalid yaams json"}

    return mcp


def _write_inbox_note(text: str, *, title: str = "", tags: str = "", scope: str = "all") -> dict:
    """Write a minimal inbox capture and return its path. Human triages later."""
    from ledger.config import get_config
    from ledger.inbox import _inbox_dir
    from ledger.text import write_markdown

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inbox = _inbox_dir(get_config().ledger_notes_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    slug_src = (title or text).strip().lower()
    slug = "".join(c if c.isalnum() else "_" for c in slug_src)[:40].strip("_") or "capture"
    path = inbox / f"{slug}.md"
    n = 1
    while path.exists():
        path = inbox / f"{slug}_{n}.md"
        n += 1
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    fm = {"created": now, "updated": now, "tags": tag_list,
          "confidence": 0.5, "source": "assistant", "scope": scope, "via": "mcp"}
    heading = f"# {title}\n\n" if title else ""
    write_markdown(path, fm, f"{heading}{text.strip()}")
    return {"created": str(path), "status": "captured_to_inbox"}


def run(allow_write: bool = False, with_yaams: bool = False) -> None:
    """Launch the MCP server over stdio (blocking)."""
    server = create_server(allow_write=allow_write, with_yaams=with_yaams)
    server.run()

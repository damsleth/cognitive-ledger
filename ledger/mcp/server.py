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
    """Recursively strip private sections (tags and fences) from any string in *obj*.

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


MAX_LIMIT = 50
SNIPPET_CHARS = 300
_VIEWS = ("index", "context", "detail")
_COMPACT_KEYS = ("query", "scope", "effective_retrieval_mode", "results")


def _rank_reply(payload, *, view: str, verbose: bool) -> dict:
    """Serialise a ranking for MCP: compact by default, full diagnostics on request.

    The CLI's `query --json` shape is unchanged; this projection is MCP-only.
    Pool sizes, timing, semantic stats and per-result score reasons cost ~5 KB
    per 3-result reply and are debugging output, not recall.
    """
    from ledger.query import query_result_to_json

    if view not in _VIEWS:
        raise ValueError(f"view must be one of: {', '.join(_VIEWS)}")
    out = query_result_to_json(payload, view=view)
    if verbose:
        return scrub_for_egress(out)
    compact = {key: out.get(key) for key in _COMPACT_KEYS}
    results = []
    for item in out.get("results", []):
        item = {k: v for k, v in item.items() if k != "reasons"}
        snippet = item.get("snippet")
        statement = str(item.get("statement") or "")
        if isinstance(snippet, str):
            # The snippet is usually the statement's opening; don't send it
            # twice. Only drop it when *all* of it repeats the statement: a
            # loop snippet appends " Next: <action>", which must survive.
            core = snippet.rstrip(" …").strip()
            if statement and core and statement.startswith(core):
                item.pop("snippet")
            elif len(snippet) > SNIPPET_CHARS:
                item["snippet"] = snippet[:SNIPPET_CHARS].rstrip() + "…"
        results.append(item)
    compact["results"] = results
    return scrub_for_egress(compact)


def _check_query(query: str, limit: int) -> int:
    """Reject an empty query and clamp *limit* to 1..MAX_LIMIT.

    Raising makes FastMCP return a protocol-level ``isError`` result. Without
    this, ``limit=-1`` or ``limit=100000`` returned the whole corpus (~650 KB).
    """
    if not str(query or "").strip():
        raise ValueError("query must not be empty")
    return max(1, min(int(limit), MAX_LIMIT))


def _parse_date(raw: str | None):
    """Parse a date or ISO timestamp; naive values are assumed UTC.

    datetime.fromisoformat (3.11+) already covers YYYY-MM-DD, trailing "Z",
    offsets and fractional seconds -- all of which the previous strptime/date
    pair rejected, raising an uncaught ValueError out of the MCP tool.
    """
    if not raw:
        return None
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"invalid date {raw!r}: use YYYY-MM-DD or ISO 8601") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def create_server(*, config=None, allow_write: bool = False, with_yaams: bool = False):
    """Build (but do not run) the FastMCP server with ledger tools."""
    FastMCP = _require_mcp()
    from ledger.config import get_config, set_config
    from ledger.query import rank_query
    from ledger.validation import validate_scope

    if config is not None:
        set_config(config)

    mcp = FastMCP("cognitive-ledger")

    @mcp.tool()
    def ledger_query(query: str, scope: str = "all", limit: int = 8, view: str = "context",
                     verbose: bool = False) -> dict:
        """Rank ledger notes for a query. Returns scored results with trust verdicts.
        view: index | context | detail. verbose: add ranking diagnostics."""
        limit = _check_query(query, limit)
        scope = validate_scope(scope)
        payload = rank_query(query=query, scope=scope, limit=limit,
                             aliases_path=get_config().aliases_path,
                             retrieval_mode=get_config().retrieval_mode)
        return _rank_reply(payload, view=view, verbose=verbose)

    @mcp.tool()
    def ledger_recall_as_of(query: str, as_of: str, scope: str = "all", limit: int = 8,
                            view: str = "context", verbose: bool = False) -> dict:
        """Recall notes valid at a point in time (YYYY-MM-DD or full ISO)."""
        limit = _check_query(query, limit)
        scope = validate_scope(scope)
        payload = rank_query(query=query, scope=scope, limit=limit,
                             aliases_path=get_config().aliases_path,
                             retrieval_mode=get_config().retrieval_mode,
                             as_of=_parse_date(as_of))
        return _rank_reply(payload, view=view, verbose=verbose)

    @mcp.tool()
    def ledger_changed_since(query: str, since: str, scope: str = "all", limit: int = 8,
                             view: str = "context", verbose: bool = False) -> dict:
        """Return notes matching *query* created/updated on or after *since*."""
        limit = _check_query(query, limit)
        scope = validate_scope(scope)
        payload = rank_query(query=query, scope=scope, limit=limit,
                             aliases_path=get_config().aliases_path,
                             retrieval_mode=get_config().retrieval_mode,
                             changed_since=_parse_date(since))
        return _rank_reply(payload, view=view, verbose=verbose)

    @mcp.tool()
    def ledger_context() -> dict:
        """Return the session boot context (identity + recent state)."""
        from ledger.context import build_context
        return scrub_for_egress({"context": build_context(get_config().ledger_notes_dir)})

    # Only advertise a cited answer when something can write one: with the
    # default `dummy` backend the tool returned a placeholder, never citations.
    if str(get_config().synth_backend or "dummy").strip().lower() != "dummy":
        @mcp.tool()
        def ledger_answer(question: str, scope: str = "all", limit: int = 5) -> dict:
            """Synthesize a grounded, cited answer to a question (plan 45)."""
            limit = _check_query(question, limit)
            scope = validate_scope(scope)
            from ledger.synthesize import answer as synth_answer
            result = synth_answer(question, scope=scope, limit=limit)
            return scrub_for_egress(result.to_dict())

    if allow_write:
        @mcp.tool()
        def ledger_remember(text: str, title: str = "", tags: str = "", scope: str = "meta") -> dict:
            """Capture a note into the inbox for later human triage (write-gated)."""
            if not str(text or "").strip():
                raise ValueError("text must not be empty")
            scope = validate_scope(scope, allow_all=False)
            return scrub_for_egress(_write_inbox_note(text, title=title, tags=tags, scope=scope))

        @mcp.tool()
        def ledger_signal(signal_type: str, note: str = "", query: str = "",
                          detail: str = "", rating: int | None = None) -> dict:
            """Log feedback on retrieval (write-gated). Log only on clear evidence:
            retrieval_hit when a returned note was actually used in your answer;
            retrieval_miss when a query found nothing useful; correction /
            affirmation when the user corrects or confirms a note; stale_flag when
            a note is outdated; preference_applied when a preference note shaped
            your output; rating (1-10) for an explicit user rating. Never log
            speculatively. `note` is the result's rel_path (notes/...)."""
            return _log_signal(signal_type, note=note, query=query, detail=detail, rating=rating)

    if with_yaams:
        @mcp.tool()
        def yaams_query(query: str, limit: int = 10) -> dict:
            """Search tier-1 (YAAMS) digital exhaust. Requires yaams on PATH."""
            import json
            import subprocess
            limit = _check_query(query, limit)
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


def _write_inbox_note(text: str, *, title: str = "", tags: str = "", scope: str = "meta") -> dict:
    """Write a lint-clean inbox capture and return its path. Human triages later."""
    from ledger.config import get_config
    from ledger.inbox import _inbox_dir
    from ledger.io.safe_write import append_timeline_entry
    from ledger.text import infer_lang, write_markdown

    cfg = get_config()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inbox = _inbox_dir(cfg.ledger_notes_dir)
    inbox.mkdir(parents=True, exist_ok=True)
    slug_src = (title or text).strip().lower()
    slug = "".join(c if c.isascii() and c.isalnum() else "_" for c in slug_src)[:40].strip("_") or "capture"
    # Claim the filename with O_EXCL so two concurrent captures cannot pick
    # the same name; the atomic write below then replaces our own empty file.
    n = 0
    while True:
        path = inbox / (f"note__{slug}.md" if n == 0 else f"note__{slug}_{n}.md")
        try:
            with open(path, "x", encoding="utf-8"):
                break
        except FileExistsError:
            n += 1
    tag_list = [t.strip().lower().replace(" ", "_") for t in tags.split(",") if t.strip()]
    heading = f"# {title}\n\n" if title else ""
    body = f"{heading}{text.strip()}"
    fm = {"created": now, "updated": now, "tags": tag_list,
          "confidence": 0.5, "source": "assistant", "scope": scope,
          "lang": infer_lang(body), "via": "mcp"}
    try:
        write_markdown(path, fm, body)
    except BaseException:
        # Drop our empty claim so failed captures don't pile up as 0-byte notes.
        if path.is_file() and path.stat().st_size == 0:
            path.unlink(missing_ok=True)
        raise
    append_timeline_entry(
        cfg.timeline_path,
        "created",
        path,
        "captured via MCP ledger_remember",
        root_dir=cfg.ledger_root,
        ledger_notes_dir=cfg.ledger_notes_dir,
    )
    return {"created": str(path), "status": "captured_to_inbox"}


# Feedback an agent may report. supersession / contradiction_flagged /
# llm_judged are written by the ledger itself, never by a client.
_AGENT_SIGNALS = {
    "retrieval_hit": "note", "retrieval_miss": "query", "correction": "note",
    "affirmation": "note", "stale_flag": "note", "preference_applied": "note",
    "rating": "rating",
}


def _contained_note(note: str, notes_dir) -> str:
    """Return the canonical notes/... path for *note*, or raise.

    The note must resolve (symlinks followed) to an existing file inside the
    corpus: `notes//etc/passwd`, `notes/../x` and links pointing outside are
    all refused, so a client can never make a signal reference a foreign file.
    """
    from pathlib import Path

    suffix = note[len("notes/"):] if note.startswith("notes/") else None
    if not suffix or Path(suffix).is_absolute():
        raise ValueError("note must be a result path like notes/02_facts/fact__x.md")
    root = Path(notes_dir).resolve()
    target = (root / suffix).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError(f"note not found in the ledger: {note}")
    return "notes/" + target.relative_to(root).as_posix()


def _log_signal(signal_type: str, *, note: str = "", query: str = "",
                detail: str = "", rating: int | None = None) -> dict:
    """Validate and append one agent-reported signal (channel ``mcp``)."""
    from ledger.config import get_config
    from ledger.signals import append_signal

    needs = _AGENT_SIGNALS.get(signal_type)
    if needs is None:
        raise ValueError(f"signal_type must be one of: {', '.join(sorted(_AGENT_SIGNALS))}")
    note = str(note or "").strip()
    if needs == "note" and not note:
        raise ValueError(f"{signal_type} needs the note (a result's path, notes/...)")
    if note:
        note = _contained_note(note, get_config().ledger_notes_dir)
    if needs == "query" and not str(query or "").strip():
        raise ValueError("retrieval_miss needs the query that found nothing")
    if rating is not None and (
        isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 10
    ):
        raise ValueError("rating must be an integer from 1 to 10")
    if needs == "rating" and rating is None:
        raise ValueError("rating needs a 1-10 rating")
    entry = append_signal(signal_type, query=query, note=note, detail=detail,
                          rating=rating, channel="mcp")
    return {"logged": entry}


def run(allow_write: bool = False, with_yaams: bool = False) -> None:
    """Launch the MCP server over stdio (blocking)."""
    server = create_server(allow_write=allow_write, with_yaams=with_yaams)
    server.run()

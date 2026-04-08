# Plan 17: Replace TUI Query's Codex Shell-Out with Direct Library Call

## Problem

The TUI query feature (`tui/screens/main_screen.py`) shells out to a `codex`
executable to run queries:

```python
command = ["codex", "-c", "mcp_servers.playwright.enabled=false", "e",
           f"/cognitive-ledger query {shlex.quote(query)}"]
```

This creates multiple failures:
- Depends on `codex` being on PATH (incompatible with standalone binary)
- References `/cognitive-ledger` skill which doesn't exist (skill is `/notes`)
- Parses human-readable codex output with regex (fragile)
- The structured `ledger.query.rank_query()` API already exists

## Priority

P2 - TUI query is broken in the documented setup.

## Plan

### 1. Replace _run_codex_query_command() with direct Python call

File: `tui/screens/main_screen.py`, lines 398-416.

Replace the subprocess call with:

```python
def _run_query(self, query: str) -> list[dict]:
    from ledger.query import rank_query
    from ledger.retrieval import resolve_retrieval_mode, resolve_embed_backend

    result = rank_query(
        query=query,
        scope="all",
        limit=8,
        aliases_path=self.store.config.aliases_path,
        retrieval_mode=resolve_retrieval_mode(None),
        embed_backend=resolve_embed_backend(None),
        embed_model=None,
    )
    return result.get("results", [])
```

Note: `rank_query` needs `load_embeddings_module` and `resolve_embed_model`
callbacks. Check how `scripts/ledger` wires these and replicate. May need
to pass the function references or use a simpler entry point.

### 2. Replace _parse_query_hits() with direct result mapping

File: `tui/screens/main_screen.py`, lines 418-466.

The regex-based parser becomes unnecessary. Map the structured `ScoredResult`
objects directly to whatever the TUI display needs (path, title, score, snippet).

### 3. Update the caller (action_query or similar)

Find where `_run_codex_query_command` is called, replace with the new
`_run_query()` call, and update result handling.

### 4. Remove shlex/subprocess imports if no longer needed

Clean up unused imports.

## Key Files

- `tui/screens/main_screen.py` (lines 398-466)
- `ledger/query.py` (rank_query API reference)

## Verification

- `pytest -q --tb=short` passes
- Launch TUI, press `/`, type a query, verify results appear
- No `codex` dependency required

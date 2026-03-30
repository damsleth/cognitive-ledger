# Add Query Telemetry Log

## Problem

No visibility into how retrieval performs in real usage. A/B eval uses synthetic cases, but real queries may hit different edge cases. No way to identify slow queries, missing aliases, or retrieval blind spots.

## Plan

### Step 1: Add telemetry writer to ledger/retrieval.py

1. At the end of `rank_lexical()`, if `LEDGER_QUERY_LOG=1` env var is set, append a JSONL line
2. Use `get_config().root_dir / "notes" / "08_indices" / "query_log.jsonl"` as path
3. Each line:
   ```json
   {"ts":"...","query":"calendar constraints","scope":"work","mode":"legacy","top_3":["fact__calendar.md","pref__scheduling.md"],"latency_ms":142,"candidate_count":45}
   ```
4. Use `safe_write_text` append mode or simple file append (telemetry is advisory, not critical)

### Step 2: Add opt-in flag

1. Check `os.environ.get("LEDGER_QUERY_LOG")` — disabled by default
2. Document in README under "Indexing and Retrieval"

### Step 3: Add analysis helper (optional)

1. Add `cmd_telemetry_summary()` to `ledger/maintenance.py` or a new `ledger/telemetry.py`
2. Reads `query_log.jsonl`, reports: slowest queries, most common queries, queries with no results

## Verification

```bash
LEDGER_QUERY_LOG=1 ./scripts/ledger query "calendar" --scope all
cat notes/08_indices/query_log.jsonl
# should contain one JSONL line with the query details
```

## Effort

~30 minutes for basic logging. Analysis helper is optional follow-on.

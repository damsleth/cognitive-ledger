# Plan 06 — Bitemporal event-time wiring (valid_from from YAAMS source timestamp)

**Target executor:** one Sonnet subagent. **Cross-repo:** touches both `cognitive-ledger` and `yaams`.
**Report:** A1 (Zep event-time vs ingestion-time; non-lossy invalidation). **Gate:** temporal cases from Plan 01. **Needs:** Plan 01 (temporal cases).

## Status — (a) and (c) shipped; (b) still open _(reconciled 2026-08-30)_
Already built: `valid_from`/`valid_to`/`superseded_by`/`supersedes` in `schema.yaml` + `ledger/bitemporal.py` (`parse_valid_time` 65-106, `is_valid_at` 107-140, `supersede()` 197-407 — sets `valid_to`, moves to `09_archive`, logs a supersession signal). `--as-of` retrieval lens exists: `apply_temporal_filter()` at `ledger/retrieval.py:893-962`.

**Of the three gaps (report A1):**
- (a) **DONE** — yaams `enrich_candidate_event_time()` (`yaams/promote/review.py:157`) populates `valid_from` from the earliest non-inferred source timestamp, emits `valid_from_confidence: low` when all timestamps are inferred; covered by `tests/test_promote_contract.py`.
- (b) **OPEN** — `--as-of` is still opt-in; no temporal-cue auto-selection exists in `ledger/query.py`/`retrieval.py`.
- (c) **DONE, via exclusion not down-rank** — `apply_temporal_filter()` hides live notes with `valid_to < now` from current retrieval while keeping them reachable via `--as-of` (`ledger/retrieval.py:893-962`). The plan's softer down-rank-multiplier variant was not implemented; revisit only if hiding proves too aggressive.

## Steps

### (a) Event-time from source — YAAMS side
YAAMS already stores `items.timestamp` (ISO 8601 source-native) + `items.timestamp_inferred` flag (`yaams/ingest/base.py:11-28`). The promote handoff writes frontmatter via `yaams/promote/review.py format_note()`.
- In `format_note()`, emit `valid_from:` = the **earliest real source timestamp** among `source_item_ids` (skip items where `timestamp_inferred=1`).
- When *all* contributing items are `timestamp_inferred`, **omit `valid_from`** (don't guess) and let the note fall back to ingestion-time. Record `via: inference` so provenance reflects the low confidence (mitigates the "forwarded email date" error the report warns about).
- Run yaams from `/Users/damsleth/code/yaams/.venv/bin/python`. Add a yaams-side test asserting `valid_from` is the min real timestamp and is omitted when all inferred.

### (b) Auto-`--as-of` on temporal queries — ledger side
- Find ledger's query parser / scope detection (start at `ledger/query.py` and `retrieval.py:893-962`). When the query carries a temporal scope (a year, "back in", "when I lived", a date range), default the as-of instant from the parsed date instead of `now`.
- Keep it conservative: only auto-engage on an unambiguous temporal cue; otherwise `now`. This is the report's "make `--as-of` the default lens when query parsing detects a temporal scope."

### (c) Down-rank `valid_to < now` (never delete)
- In the ranking path, when a live (non-archived) note has `valid_to < now`, apply a down-rank multiplier rather than excluding it (non-lossy: stale facts stay queryable, just demoted). `apply_temporal_filter()` already knows validity windows — add the down-rank there or in the scorer it feeds.
- **Invariant:** never delete, never hard-filter out of `--as-of` history. Archived/superseded notes remain reachable via `--as-of`.

## Acceptance gate
The temporal cases authored in Plan 01 (`id: temporal_*`) are the gate:
```bash
ledger ab run --baseline-ref main --candidate-ref HEAD \
  --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 ; echo "exit=$?"  # no regression (0/3)
ledger eval --cases tests/fixtures/retrieval_eval_cases.yaml --k 3   # temporal_* now resolve correctly:
#   "where do I live now" → live note ranks top; old note demoted
#   "where did I live in 2023" (--as-of) → 2023-valid note recovered from archive
```
Plus the yaams-side `valid_from` unit test, and a ledger unit test that a `valid_to < now` live note is demoted not dropped.

## Regression risk
Low-to-moderate. Event-time inference errors (forwarded mail) — mitigated by the `timestamp_inferred` skip + ingestion-time fallback. Auto-`--as-of` misfiring on a non-temporal query — keep the cue strict and A/B against the general cases to confirm no regression.

## Done when
Promoted notes carry source-derived `valid_from` (or correctly omit it), temporal queries auto-select the right as-of lens, stale-but-live notes are down-ranked not deleted, and the temporal eval cases pass with no general regression.

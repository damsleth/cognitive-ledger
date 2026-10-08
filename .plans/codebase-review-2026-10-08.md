# Codebase review 2026-10-08: structure, metadata, MCP, harness

Read-only review of `main@a6ac3bb` against the live corpus (`~/brain/ledger`,
921 notes) and the fixture corpus. Three parallel audits (schema/corpus,
MCP live stdio test, test + A/B harness); every bug below was re-verified
against the cited line before it went in. Second review: copilot (medium),
see "Second review" at the bottom.

Improvements that are not bugs live in `improvement-plan-2026-10-08.md`.

## Baseline (2026-10-08)

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | 1618 passed, 0 failed, 31.8s |
| `ledger eval` live, k=3 (46 cases) | hit@1 0.783, hit@3 0.870, mrr 0.830 |
| `ledger eval --strict-cases` live | refuses: 1 dangling expected path |
| `bash scripts/ab_gate.sh` on clean main (A/A) | exit 0 BENEFICIAL, 3/3 runs (should be 3) |
| `ledger sleep lint` | 0 errors, 104 warnings (92 are "large file" on archive) |
| MCP stdio (`ledger mcp`) | works: protocol negotiates, stdout clean, scrubbed; first call 8.1s (bge-m3 load), then <0.12s |
| MCP registration | none (`~/.claude.json`, `~/.codex/config.toml`, no `.mcp.json`) |

Corpus: 02_facts 381, 03_preferences 81, 06_concepts 55, 05_open_loops 19
(10 open / 8 snoozed / 1 closed), 04_goals 6, 01_identity 5, 00_inbox 30
(oldest 22.7 days), 09_archive 344 (261 `note__`, 81 `loop__`), 07_projects empty.

## Rules for every item

- Branch per batch off `main`; `.venv/bin/python -m pytest -q` green before merge.
- Tests use fixture corpora (`tests/fixtures/corpus/notes`), never `~/brain/ledger`.
- Retrieval-affecting files (`retrieval.py`, `query.py`, `scoring.py`, `signals.py`,
  `config.py`) also need `bash scripts/ab_gate.sh` in the PR body; but fix B1 first,
  because until then the gate cannot return neutral.
- CHANGELOG `### Fixed` bullet per shipped item. README only if a documented command changes.

## Batch 1: gate and harness correctness (do first: everything else is measured with it)

### B1. A/B decision: a quality tie is reported as "beneficial"
- Claim: `decide_outcome` returns `beneficial`/exit 0 when quality ties and latency
  is within tolerance (`ledger/ab.py:621-630`), and `neutral`/3 only when latency
  *fails* (`:632-640`). AGENTS.md, `ab_gate.sh` and `ai-memory/00-INDEX.md` define
  3 as "quality tie, latency ok, merge only if off-by-default". Net effect: every
  no-op passes as a win, and an off-by-default change that *slows* retrieval gets
  the "ok if off-by-default" exit.
- Design (in since `b068c6b`, so this is a contract change, not a typo):
  quality tie + latency ok → `neutral` (3); quality tie + latency fail → `regression`
  (2) with `regressed_metrics=["latency_eval_p95"|"latency_query_p95"]`.
  Improved quality still wins regardless of latency (unchanged).
- Tests: unit tests on `decide_outcome` for all four branches (regressed, improved,
  tie+latency ok, tie+latency fail). Fix `tests/test_ledger_ab.py:493`
  `test_head_vs_head_smoke` (+ `_with_cold_query` :545, `_with_runs_shortcut` :583)
  to assert exit 3.
- Accept: `bash scripts/ab_gate.sh` on a branch identical to main → prints NEUTRAL.
- Docs: update the reason strings; AGENTS.md exit table already says the right thing.

### B2. Live eval cases: one dangling path blocks `--strict-cases`
- Claim: case `dev_gth_next_action` in `~/brain/ledger/08_indices/retrieval_eval_cases.yaml`
  expects `notes/05_open_loops/loop__gth_prod_deployment_and_julia_update.md`, which no longer exists.
- Fix (live-corpus edit, owner runs it): repoint to the current GTH loop or drop the case.
- Code fix: `ledger eval` without `--strict-cases` should *warn* on missing expected
  paths (today it silently scores them as misses). Anchor: case validation in
  `ledger/eval.py` (the `expected_any` loader, ~:94-110).
- Test: fixture case file with one missing path → warning on stderr, exit 0.

### B3. Archive-expecting eval cases fail by design
- Claim: 5 live cases (`dev_norwegian_llm_loop`, `history_v019_deployment`,
  `history_une_script`, `history_brkh_visit`, `loop_verditakst`) expect
  `09_archive/` notes, which default retrieval hides. They are 5 of the 10 misses.
- Fix: add optional `as_of: YYYY-MM-DD` to the case schema (`ledger/eval.py`,
  case dict at ~:94), pass it to `rank_query(as_of=...)`. No `as_of` handling exists in eval today.
- Test: fixture case with `as_of` that hits an archived fixture note; same case without `as_of` misses.
- Accept: live eval hit@3 rises with no other case changing (owner adds `as_of` to the 5 cases).

## Batch 2: privacy and data safety

### B4. Documented private-fence syntax is not stripped
- Claim: `docs/privacy.md:18,24` and `docs/trust-boundaries.md:60` promise
  ```` ```private ```` code fences are stripped. `strip_private_tags`
  (`ledger/parsing/privacy.py:11-12`) only strips `<private>…</private>` (the README:334 syntax).
  Every egress path (embeddings :237, retrieval :479, web corpus :258 / render :103,
  MCP `scrub_for_egress`) goes through that one function, so a note written per the
  docs would leak into embeddings, MCP output and judge prompts.
- Exposure today: 0 notes use either syntax in `~/brain/ledger` or `~/brain/notes`.
- Fix: fail closed: make `strip_private_tags` also strip ```` ```private ```` … ```` ``` ```` blocks
  (one regex, multiline). Then make both docs list both syntaxes.
- Test: `tests/test_privacy*.py` cases for the fence form, the tag form, both in one
  note, an unterminated fence (strip to EOF), and a normal ```` ```python ```` fence (kept).
  Extend `tests/test_mcp_server.py` private-fence corpus with a fence-form note.

### B5. `ledger --doctor --fix` deletes every lock file, including held ones
- Claim: `ledger/doctor.py:161-173` collects `rglob("*.lock")` and unlinks all of
  them. AGENTS.md: unlinking a lock lets a waiter and a newcomer both hold it;
  `ledger inbox cleanup` only reaps locks a non-blocking flock proves unheld.
- Fix: replace the body with a call to `reap_unheld_locks(notes_dir=..., apply=fix)`
  (`ledger/inbox.py:162`). Report "N unheld lock files" rather than "stale".
- Test: hold a `FileLock` in-process on a fixture lock, run doctor `--fix`, assert the held lock survives and an unheld one is removed.

### B6. MCP `ledger_remember` writes notes that fail lint
- Claim: `_write_inbox_note` (`ledger/mcp/server.py:141-162`) defaults
  `scope="all"` (not a scope enum value), writes no `lang` (required), sets
  `via: "mcp"` (not in `VIA_VALUES`, `ledger/schema_values.py:21`), and uses an unprefixed filename.
  The slug-collision loop is unlocked (racy, inbox only).
- Fix: default `scope="meta"`, validate against the scope enum; write `lang: en`
  (or `mixed` when the text has æøå); add `mcp` to `VIA_VALUES` and `schema.yaml`;
  name files `note__<slug>.md` like other inbox captures. Reuse the inbox write helper if one exists in `ledger/inbox.py`.
- Test: call `_write_inbox_note` against a tmp corpus, run the lint function from `ledger/validation.py` on the result, assert zero issues.

### B7. MCP inputs are unbounded
- Claim: `limit=100000` returned 630 KB, `limit=-1` 679 KB, `query=""` returns results
  (`ledger/mcp/server.py:69-108`, no validation). `as_of="garbage"` surfaces a raw
  `Invalid isoformat string`.
- Fix: clamp `limit` to 1..50; reject empty/whitespace query; wrap date parse errors with "use YYYY-MM-DD or ISO 8601".
- Test: three cases in `tests/test_mcp_server.py`.

### B8. `ledger_answer` advertises a cited answer but returns the dummy adapter
- Claim: no `synth_backend` configured, so `ledger_answer` returns
  `[dummy adapter] no synthesis backend configured` (`ledger/synthesize/llm.py:40-51`) with empty `cited_paths`.
- Fix: in MCP, return `isError` with a config hint when the backend is `dummy`, or
  do not register the tool at all. Backend choice is the same open decision as
  ai-memory plan 14 (D2 below).

## Batch 3: lint and doctor tell the truth

### B9. Doctor reports a false `semantic_index_missing`
- Claim: `ledger/doctor.py:131` checks `08_indices/embeddings/`; the real index is
  under `<notes>/.smart-env/semantic/ledger/<backend>__<model>/` (built 2026-10-06).
- Fix: resolve the path with the same helper `ledger embed status` uses (`ledger/embeddings.py` / `ledger/semantic.py`).
- Test: fixture with a built index → no finding; without → finding.

### B10. Inferred-confidence lint threshold is looser than the spec
- Claim: `ledger/validation.py:383` warns only at `confidence > 0.8`; schema.yaml and
  AGENTS.md say `< 0.7` = hypothesis. 101 inferred notes are ≥ 0.7, 90 of them in 0.7–0.8 and silent.
- Fix: warn at `>= 0.7`. Warning only, no auto-fix.
- Test: extend the existing validation test with 0.69 (clean) and 0.7 (warn).

### B11. Archived loops have no validity window
- Claim: 81 `loop__` files sit in `09_archive/` and 56 typed archive notes lack
  `valid_to`. No code path archives loops (only `supersede()` moves notes, `ledger/bitemporal.py`);
  they were moved by hand or by skills. `collect_ledger_notes` (`ledger/embeddings.py:212-224`) embeds
  them, so `--as-of` reads treat a closed loop as valid forever.
- Fix: `ledger migrate bitemporal --check/--apply` back-fills `valid_to` for typed
  archive notes from `updated` (or the timeline `closed`/`archived` event when present);
  `ledger sleep lint` warns on a typed archive note without `valid_to`.
- Test: fixture archive loop without `valid_to` → `--check` lists it, `--apply` sets it, lint is clean after.

### B12. Doctor/lint check `timeline.md`, not `timeline.jsonl`
- Claim: `doctor.py` `timeline_missing` and `maintenance.py:_lint_timeline` (~781-822)
  read the generated markdown; AGENTS.md makes `timeline.jsonl` the source of truth.
  Today both match (1029 entries). A 0-byte `timeline.jsonl.tmp` from May sits in `08_indices`.
- Fix: check the jsonl (parse + monotonic timestamps); flag a jsonl/md count mismatch; `inbox cleanup` reaps orphan `*.tmp` older than a day.

## Ops the owner runs (live corpus, not code)

```
ledger sleep index                      # 3 loops newer than the 2026-10-06 index are unreachable in semantic_hybrid
ledger signal summarize                 # summary is from 2026-06-20 (131 vs 133 signals)
ledger inbox triage                     # 30 items, oldest 22.7 days
# fix or drop case dev_gth_next_action in 08_indices/retrieval_eval_cases.yaml (B2)
ledger migrate bitemporal --check       # then --apply once B11 lands
```

## Decisions needed (owner)

- D1. Register `ledger mcp` with Claude Code / Codex at all? `yaams` MCP already does
  recall, and it has cited a ledger note only 3 times (ai-memory plan 10). If yes:
  `{"command": "/Users/damsleth/.local/bin/ledger", "args": ["mcp"]}`. Registering it is
  the cheapest way to get `retrieval_hit` signals flowing, which unparks plans 10/16.
- D2. `synth_backend` (shared with ai-memory plan 14). Without it, drop `ledger_answer` from MCP (B8).
- D3. Identity limit: AGENTS.md says max 5 (five types); `schema.yaml:134,224` adds `voice` and max 6. Pick one.
- D4. CI retrieval gate: install `.[test,embeddings]` + cache the model, or assert and print the
  effective retrieval mode and fail on silent fallback (improvement plan I3).

## Second review

(filled in after the copilot review)

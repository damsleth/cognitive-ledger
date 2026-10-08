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
| `ledger migrate bitemporal --check` | 591 notes would be touched (never applied) |
| MCP stdio (`ledger mcp`) | works: protocol negotiates, stdout clean, scrubbed; first call 8.1s (bge-m3 load), then <0.12s |
| MCP registration | none (`~/.claude.json`, `~/.codex/config.toml`, no `.mcp.json`) |

Corpus: 02_facts 381, 03_preferences 81, 06_concepts 55, 05_open_loops 19
(10 open / 8 snoozed / 1 closed), 04_goals 6, 01_identity 5, 00_inbox 30
(oldest 22.7 days), 09_archive 344 (261 `note__`, 81 `loop__`), 07_projects empty.

## Rules for every item

- Branch per batch off `main`; `.venv/bin/python -m pytest -q` green before merge.
- Tests use fixture corpora (`tests/fixtures/corpus/notes`), never `~/brain/ledger`.
- Retrieval-affecting files (`retrieval.py`, `query.py`, `scoring.py`, `signals.py`,
  `config.py`) also need `bash scripts/ab_gate.sh` in the PR body. Fix B1 first:
  until it lands, the gate cannot return neutral.
- CHANGELOG `### Fixed` bullet per shipped item. README only if a documented command changes.

## Batch 1: gate and harness correctness (do first: everything else is measured with it)

### B1. A/B decision: a quality tie is reported as "beneficial"
- Claim: `decide_outcome` returns `beneficial`/exit 0 when quality ties and latency
  is within tolerance (`ledger/ab.py:621-630`). It returns `neutral`/3 only when latency
  *fails* (`:632-640`). AGENTS.md, `ab_gate.sh` and `ai-memory/00-INDEX.md` define
  3 as "quality tie, latency ok, merge only if off-by-default". Net effect: every
  no-op passes as a win, and an off-by-default change that *slows* retrieval gets
  the "ok if off-by-default" exit.
- Design (in since `b068c6b`, so this is a contract change, not a typo):
  quality tie + latency ok → `neutral` (3); quality tie + latency fail → `regression`
  (2) with `regressed_metrics=["latency_eval_p95"|"latency_query_p95"]`.
  Improved quality still wins regardless of latency (unchanged).
- Tests:
  - Update the unit expectations at `tests/test_ledger_ab.py:95-122` to cover all four branches: regressed, improved, tie with latency ok, tie with latency failing.
  - Fix `test_head_vs_head_smoke` (:493), `_with_cold_query` (:545) and `_with_runs_shortcut` (:583) to assert exit 3.
  - Update the policy prose at `ledger/ab.py:968` and the reason strings.
- Accept: `bash scripts/ab_gate.sh` on a branch identical to main (fixture corpus) → prints NEUTRAL.

### B2. Live eval cases: one dangling path blocks `--strict-cases`
- Claim: case `dev_gth_next_action` in `~/brain/ledger/08_indices/retrieval_eval_cases.yaml`
  expects `notes/05_open_loops/loop__gth_prod_deployment_and_julia_update.md`, which no longer exists.
- Fix (live-corpus edit, owner runs it): repoint to the current GTH loop or drop the case.
- Code fix: `ledger eval` without `--strict-cases` should *warn* on missing expected paths. Today
  non-strict validation skips the existence check (`ledger/eval.py:241-242`) and scores them as silent misses.
  - Anchor: `validate_eval_cases`, not the YAML parser.
  - The warning goes to stderr; the `--json` output shape does not change.
- Test: fixture case file with one missing path → warning on stderr, exit 0, JSON unchanged.

### B3. Archive-expecting eval cases fail by design
- Claim: 5 live cases (`dev_norwegian_llm_loop`, `history_v019_deployment`,
  `history_une_script`, `history_brkh_visit`, `loop_verditakst`) expect
  `09_archive/` notes, which default retrieval hides. They are 5 of the 10 misses.
- Fix: add an optional `as_of: YYYY-MM-DD` to the case schema and pass it to `rank_query(as_of=...)`.
  No `as_of` handling exists in eval today.
  - Anchors in `ledger/eval.py`: case dict :91-97, validation :289-319, ranking call :387-395.
  - Parse to a tz-aware UTC datetime and reject malformed dates in validation.
  - Make sure no compatibility fallback path drops the temporal filter silently.
- Test: an archived fixture note with `valid_from`/`valid_to`:
  - a case with an `as_of` inside the window hits;
  - an `as_of` after `valid_to` misses;
  - no `as_of` misses.

  This tests validity, not just archive inclusion.
- Accept: live eval hit@3 rises with no other case changing (owner adds `as_of` to the 5 cases).

## Batch 2: privacy and data safety

### B4. Documented private-fence syntax is not stripped
- Claim: `docs/privacy.md:18,24` and `docs/trust-boundaries.md:60` promise
  ```` ```private ```` code fences are stripped. `strip_private_tags`
  (`ledger/parsing/privacy.py:11-12`) only strips `<private>…</private>` (the README:334 syntax).
- Every egress path goes through that one function:
  - embeddings (:237) and retrieval (:479);
  - web corpus (:258) and render (:103);
  - MCP `scrub_for_egress`.

  A note written per the docs would therefore leak into embeddings, MCP output and judge prompts.
- Exposure today: 0 notes use either syntax in `~/brain/ledger` or `~/brain/notes`.
- Fix: fail closed.
  - Make `strip_private_tags` also strip ```` ```private ```` … ```` ``` ```` blocks (one regex, multiline).
  - The early-return guard at `privacy.py:22` (`"<private>" not in text`) must also let the fence form through.
  - Make both docs list both syntaxes.
  - Say in the docs that an index built before the fix needs `ledger embed build --target ledger`.
- Test: in `tests/test_privacy_boundaries.py`:
  - Cases: the fence form, the tag form, both in one note, an unterminated fence (strip to EOF), and a normal ```` ```python ```` fence (kept).
  - Assert through a real egress function (`strip_private_tags` + `scrub_for_egress`), not the test-local regex at :75-98.
  - Add a fence-form note to the private-fence corpus in `tests/test_mcp_server.py`.

### B5. Lock files are unlinked without proving nobody holds them
- Claim: `ledger/doctor.py:161-173` collects `rglob("*.lock")` and `--fix` unlinks all of
  them. AGENTS.md: unlinking a lock lets a waiter and a newcomer both hold it.
- Second instance (copilot): `cleanup_inbox` unlinks inbox locks whose `.md` sibling is missing,
  with no flock (`ledger/inbox.py:255-259`). Same race.
- Fix:
  - Doctor calls `reap_unheld_locks(notes_dir=..., apply=fix)` (`ledger/inbox.py:162`) and reports "N unheld lock files" rather than "stale".
  - Delete the orphan-lock loop in `cleanup_inbox`. `reap_unheld_locks` already covers those files, safely.
- Test: in `tests/test_doctor_checks.py` and the inbox cleanup tests, hold a lock in-process on a
  fixture lock, run doctor `--fix` and `inbox cleanup --apply`, and assert the held lock survives while an unheld one is removed.

### B6. MCP `ledger_remember` writes notes that fail lint
- Claim: `_write_inbox_note` (`ledger/mcp/server.py:141-162`) has four problems:
  - it defaults `scope="all"`, which is not a scope enum value;
  - it writes no `lang`, which is required;
  - it sets `via: "mcp"`, which is not in `VIA_VALUES` (`ledger/schema_values.py:21`);
  - it uses an unprefixed filename.

  The slug-collision loop is also unlocked (racy, inbox only), and no timeline entry is written.
- Fix:
  - Default `scope="meta"` and validate with `validate_scope(scope, allow_all=False)` (`ledger/validation.py:83`).
  - Write `lang: infer_lang(text)` (`ledger/text.py:47`).
  - Add `mcp` to `VIA_VALUES` and `schema.yaml`.
  - Name files `note__<slug>.md` like other inbox captures.
  - Take the slug under a `FileLock`, or create with `O_EXCL`, so concurrent captures cannot collide.
  - Append a `created` timeline entry via `append_timeline_entry`.
- Test: in `tests/test_mcp_server.py`, call `_write_inbox_note` against a tmp corpus and run the
  lint function from `ledger/validation.py` on the result. Assert zero issues and one timeline entry.

### B7. MCP inputs are unbounded
- Claim: there is no input validation (`ledger/mcp/server.py:69-125`):
  - `limit=100000` returned 630 KB and `limit=-1` returned 679 KB;
  - `query=""` returns results;
  - `as_of="garbage"` surfaces a raw `Invalid isoformat string`.
- Fix: one small validator used by every tool (`ledger_query`, `ledger_recall_as_of`,
  `ledger_changed_since`, `ledger_answer`, `yaams_query`):
  - Clamp `limit` to 1..50.
  - Reject an empty or whitespace query.
  - Wrap date parse errors with "use YYYY-MM-DD or ISO 8601".
  - Raise, so FastMCP returns a protocol-level `isError` result.
- Test: parametrized over the tools in `tests/test_mcp_server.py`: limit 0/-1/51/100000, empty query, malformed date.

### B8. `ledger_answer` advertises a cited answer but returns the dummy adapter
- Claim: no `synth_backend` configured, so `ledger_answer` returns
  `[dummy adapter] no synthesis backend configured` (`ledger/synthesize/llm.py:40-56`) with empty `cited_paths`.
- Fix: register `ledger_answer` only when `synth_backend` is not `dummy`, decided at server
  start. It is the simplest option, and an agent never sees a tool that cannot work. The backend
  choice is the same open decision as ai-memory plan 14 (D2 below).
- Test: `tests/test_mcp_server.py` asserts the tool list with and without a backend configured.

## Batch 3: lint and doctor tell the truth

### B9. Doctor reports a false `semantic_index_missing`
- Claim: `ledger/doctor.py:131` checks `08_indices/embeddings/`; the real index is
  under `<notes>/.smart-env/semantic/ledger/<backend>__<model>/` (built 2026-10-06).
- Fix: resolve with `semantic_index_path` (`ledger/embeddings.py:156`; root from `ledger/config.py:1248-1256`)
  and require the index and vector files to exist, not just a non-empty directory.
- Test: `tests/test_doctor_checks.py`: fixture with a built index → no finding; without → finding.

### B10. Inferred-confidence lint threshold is looser than the spec
- Claim: `ledger/validation.py:383` warns only at `confidence > 0.8`; schema.yaml and
  AGENTS.md say `< 0.7` = hypothesis. 101 inferred notes are ≥ 0.7, 90 of them in 0.7–0.8 and silent.
- Fix: warn at `>= 0.7`. Warning only, no auto-fix.
- Test: no inferred-confidence test exists yet; add boundary cases 0.69 (clean) and 0.7 (warn) to `tests/test_validation.py`.

### B11. The bitemporal back-fill exists but was never applied (591 notes pending)
- Claim: 81 `loop__` files sit in `09_archive/`, and typed archive notes lack `valid_to`.
  `collect_ledger_notes` (`ledger/embeddings.py:212-224`) embeds them, so `--as-of` reads treat
  a closed loop as valid forever.
- The back-fill already exists: `migrate bitemporal` sets `valid_to` from `updated` for archive notes
  (`ledger/bitemporal.py:563-567`, tests in `tests/test_bitemporal_migrate.py`). Copilot caught that
  the first draft planned to write it again.
- It has never been applied. `ledger migrate bitemporal --check` on 2026-10-08 reports **591 notes**
  to touch, up from 132 in June:
  - archive 317, facts 224, prefs 42, concepts 7, goals 1.
- Risk: `--apply` also bumps `updated` on all 591 notes (`bitemporal.py:570`). That shifts the
  recency prior for most of the corpus and stales the semantic index.
- Fix (code):
  - Lint warns on a typed archive note without `valid_to`.
  - Drop the migration's `updated` bump, or document why it stays. A validity back-fill is not a content edit. Decide this before the owner applies the migration.
- Fix (owner, after the code fix):
  1. Run `ledger eval` and save the numbers.
  2. Run `ledger migrate bitemporal --apply`.
  3. Run `ledger sleep index`.
  4. Run `ledger eval` again. Record any hit@1 drop in this plan.
- Test: `tests/test_bitemporal_migrate.py`:
  - an archived loop without `valid_to` gets one;
  - `updated` stays unchanged if the bump is dropped;
  - lint flags the pre-migration fixture.

### B12. Doctor/lint check `timeline.md`, not `timeline.jsonl`
- Claim: `doctor.py` `timeline_missing` (:74-97) and `maintenance.py:_lint_timeline` (:781-821)
  read the generated markdown; AGENTS.md makes `timeline.jsonl` the source of truth.
  Today both match (1029 entries).
- Fix: doctor and lint parse `timeline.jsonl`.
  - A malformed line is an error that names the line number.
  - Out-of-order timestamps are a warning.
  - A missing or out-of-date `timeline.md` is a warning, and `--fix` regenerates it from the jsonl with the existing generator.
  - No count comparison; regenerating covers it.
- Separate: a 0-byte `timeline.jsonl.tmp` from May sits in `08_indices`. It is an owner `rm` after
  checking the size. Do not add age-based tmp reaping, because age does not prove nobody is writing it.
- Test: `tests/test_doctor_checks.py`, with a fixture jsonl containing a bad line → error with line number.

## Ops the owner runs (live corpus, not code)

```
ledger sleep index                      # 3 loops edited after the 2026-10-06 index rank with no semantic component
ledger signal summarize                 # summary is from 2026-06-20 (131 vs 133 signals)
ledger inbox triage                     # 30 items, oldest 22.7 days
# fix or drop case dev_gth_next_action in 08_indices/retrieval_eval_cases.yaml (B2)
# B11 sequence, after the B11 code fix: eval, migrate bitemporal --apply, sleep index, eval
rm ~/brain/ledger/08_indices/timeline.jsonl.tmp   # 0-byte leftover from May; check size first
```

## Decisions needed (owner)

- D1. Register `ledger mcp` with Claude Code / Codex at all? The `yaams` MCP already does
  recall, and it has cited a ledger note only 3 times (ai-memory plan 10).
  - If yes, register it as `{"command": "/Users/damsleth/.local/bin/ledger", "args": ["mcp"]}`.
  - Registering it does not by itself log `retrieval_hit` signals, because no MCP tool writes signals today (`server.py:69-75`).
  - Plans 10/16 need one signal route. Options: a `ledger_signal` MCP tool behind `--allow-write`, `query --pick` (`cli.py:439`), web clicks, or the parked yaams citation bridge.
- D2. `synth_backend` (shared with ai-memory plan 14). Until one is set, `ledger_answer` stays out of the MCP tool list (B8).
- D3. Identity limit: AGENTS.md says max 5 (five types); `schema.yaml:134,224` adds `voice` and max 6. Pick one.
- D4. CI retrieval gate: install `.[test,embeddings]` and cache the model, or pin the gate to lexical
  mode explicitly (improvement plan I3).

## Second review

Copilot (`agent-bridge -m medium copilot`), 2026-10-08, answer-only. Every correction was
re-checked against the code before it went in.
- Confirmed against the code: B1, B4, B5, B6, B9, B10, B12.
- B11 was wrong in the draft. The back-fill already exists, so the item is now "apply it safely" (591 pending).
- New in B5: the second unsafe unlink in `cleanup_inbox` (`inbox.py:255-259`).
- Tightened:
  - B2: anchor at `validate_eval_cases`.
  - B3: tz-aware dates, and a test of validity windows.
  - B4: the early-return guard, a real egress test, and rebuild guidance.
  - B6: `validate_scope`, `infer_lang`, the slug race and the timeline entry.
  - B7: all tools covered, with protocol errors.
  - B8: register the tool conditionally.
  - B9: `semantic_index_path`.
  - B12: parse the jsonl and regenerate the md; no age-based tmp reaping.
- D1 corrected: MCP registration alone emits no signals.
- Copilot did not read `~/brain`. The live-corpus counts come from the first-pass audit.

# Improvement plan 2026-10-08

Companion to `codebase-review-2026-10-08.md` (bugs B1–B12, decisions D1–D4).
These items are not bugs; each one has a measurable reason to exist. Order is by
leverage: measurement first, then the spec, then the surface.

Gate rule (same as `ai-memory/00-INDEX.md`): retrieval-affecting changes run
`bash scripts/ab_gate.sh` and paste the result; exit 2 stops the merge. Needs B1
first, or a no-op reads as "beneficial".

## I1. Live eval suite that can see the corpus (depends on B2, B3)

Today the live file has 46 cases: 0 negatives, 0 queries with æøå, 0 identity
cases, 3 loop cases. bge-m3 was chosen for the Norwegian/English corpus, and the
eval cannot see a Norwegian regression.
- Add ≥ 8 negatives (`expected_none: true`), ≥ 10 Norwegian queries, ≥ 4 open-loop
  and ≥ 2 identity cases, and `as_of` history cases (B3) in a separate `history` id prefix.
- The owner writes or approves the cases (they are private and live in `~/brain/ledger/08_indices/`).
  An agent may draft candidates from note titles.
- Mirror the shape in `tests/fixtures/retrieval_eval_cases.yaml` with fixture-corpus
  Norwegian notes, so CI exercises the same categories.
- Accept: `ledger eval --cases <live> --k 3 --strict-cases` runs, with the per-category breakdown recorded in this plan.

## I2. Is the identity boost reachable in `semantic_hybrid`?

`01_identity` is not embedded (`ledger/embeddings.py:36-42`, intentional per
AGENTS.md), while `retrieval.py:1334` adds `identity_score_boost`. If the
semantic_hybrid candidate pool is index-first, identity notes can never be
candidates there and the boost is dead code on the default mode.
- Investigate with the I1 identity cases: does `ledger query "<identity topic>" --json` ever return an `01_identity` note in `semantic_hybrid`?
- If not: either embed identity (5 files, cheap) and A/B it, or delete the boost
  from the semantic path. Identity is in the boot payload anyway, so deleting is the default.

## I3. CI gate measures the mode it claims (decision D4)

`.github/workflows/retrieval-gate.yml` installs `.[test]` (no `embeddings` extra) and sets
`LEDGER_EMBEDDINGS_OFFLINE=1` with no model cache, while the harness reports
`semantic_hybrid`. Unverified whether it falls back to lexical.
- First: print the effective retrieval mode and whether a semantic index was used in the `ledger ab run` report; fail with exit 4 on a silent downgrade.
- Then either install `[embeddings]` + `actions/cache` the HF model, or pin the CI gate to the lexical mode explicitly and say so in the workflow.
- Wire `tests/fixtures/run_gate.sh` (strict eval on the fixture corpus) into `release.yml`; today it runs only `pytest -q`.

## I4. One schema, enforced (replaces the drift list)

Spec lives in five places that disagree: `schema.yaml`, `ledger/schema_values.py`,
`ledger/validation.py`, `templates/`, AGENTS.md tables. Make `schema.yaml` the single
source and have `schema_values.py` load enums from it (or a test that asserts they match).
Then lint enforces what the schema already declares:
- `identity_type` required on identity notes (`schema.yaml:53`); identity count limit (after D3).
- Slug pattern (`schema.yaml:205`): 2 live violations (`fact__sjæb_project.md`, `fact__nocos-sharepoint-editor-sql-over-cosmos.md`).
- `status` only on loops: 14 facts, 5 prefs carry `status: open`, 1 goal `status: active`.
- `provenance` enum (`schema.yaml:105`), `supersedes` targets exist, dangling `[[links]]` (17 per `ledger links`).
- `max_tags` (`schema.yaml:222`): 1 note over 10.
- Document in schema + AGENTS.md table: `note__` prefix (289 inbox/archive files), `things_list_id`
  (read by `cli.py:1726`, `things3_sync.py:114`), `identity_type`, `via`, `origin`, `external_id`,
  `things_uuid`, `provenance`, `attribute`. Decide `claude_type`/`id`/`type`/`due` (136/11/11/3 notes): add or migrate away.
- Signal types: add `supersession`, `contradiction_flagged` to AGENTS.md (in `schema.yaml:240-241`).
- `schema.yaml:182` timeline path → `timeline.jsonl`.
- `validation.py:299-302` maps phantom `/07_reflections/`, `/08_people/`; map `07_projects` instead.
- `templates/generic_note_template.md`: comment block between the `---` fences parses as
  empty frontmatter; "relative links only" contradicts `[[links]]`; add `valid_from`; add an identity template.
- Lint noise: exempt archive/inbox ingest summaries from "large file" (92 of 104 warnings).
- Accept: `ledger sleep lint` on the live corpus lists the new categories; a test asserts `schema_values` == `schema.yaml` enums.

## I5. MCP: smaller answers, same contract

- Default `ledger_query` response: `results` only (path, title, type, score, trust, snippet). Diagnostics
  (`timing`, `semantic`, pool sizes, `expanded_tokens`) behind `verbose: true`. Today it is ~5–6 KB for 3 results.
  Keep the CLI `--json` shape untouched: `tests/test_seam_golden.py` locks it for yaams.
- `view` parameter on `ledger_recall_as_of` and `ledger_changed_since` (hardcoded `context` today).
- Verify `yaams_query`'s `--top-k` / `--format json` flags against the current yaams CLI before anyone enables `--with-yaams`.
- Warm the model on server start (optional): the first call costs 8.1s for the bge-m3 load.
- Accept: `tests/test_mcp_server.py` asserts the compact shape; byte size of a 3-result reply < 2 KB.

## I6. Embedding collection and type visibility

- `collect_ledger_notes` globs non-recursively (`*.md`), so a future `07_projects/<p>/note.md`
  is invisible to semantic search. Decide: embed projects recursively, or lint that `07_projects` stays empty.
- Two conflict-note layouts: `contradiction.py:431` writes `00_inbox/conflict__*`, `inbox.py:685` also reads `_conflicts/`. Pick one.
- `conventions.py` is the CLI wire contract (exit codes, doctor payload), not note conventions; rename to `wire.py` with a re-export shim.

## I7. Coverage floor

pytest-cov is not installed. Add it to the `dev` extra, record per-module coverage
once, set a floor at the current total. Thin spots by test-file imports: `claude_memory.py`
(30 KB), `integrations/things3*.py`, `parsing/privacy.py`, `ingest.py`, `llm_judge.py`, `nli.py`.
Silence the 5 deliberate `UserWarning`s with `pytest.warns`.

## Ties into existing queue

- B1 + I1 + I3 are prerequisites for ai-memory plan 14's T6 gate and agentisk PR 4: both decide by `ledger ab run` exit codes.
- D1 (register `ledger mcp`) is the only lever in sight that produces `retrieval_hit` signals; plans 10/16 stay parked until it is decided.
- No `.plans/ab_results/` run since 2026-04-28. After B1, rerun the RRF and PRF comparisons once and record them, so the
  "keep weighted_sum / keep PRF off" lines in AGENTS.md rest on a current run.

## Second review

(filled in after the copilot review)

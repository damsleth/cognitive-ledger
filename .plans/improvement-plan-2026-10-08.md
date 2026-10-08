# Improvement plan 2026-10-08

Companion to `codebase-review-2026-10-08.md` (bugs B1–B12, decisions D1–D4).
These items are not bugs; each one has a measurable reason to exist. Order is by
leverage: make the gate real, then make it see the corpus, then everything else.

Gate rule (same as `ai-memory/00-INDEX.md`): retrieval-affecting changes run
`bash scripts/ab_gate.sh` and paste the result; exit 2 stops the merge. B1 has to land first,
or a no-op reads as "beneficial".

## I1. Make the gate actually run (CI), and make it honest about what it ran

The retrieval gate has **never run**:
- `.github/workflows/retrieval-gate.yml:3-9` triggers only on `pull_request`.
- The repo has had 1 PR ever (#1, 2026-04-07). All work lands on `main` by push.
- `gh run list` shows only `Release` runs.

The "mandatory eval gate" is therefore enforced only by agent discipline.
- Add `push: branches: [main]` and `workflow_dispatch` to the trigger, so a regression shows up the commit after it lands.
- Widen the path filter to cover `ab.py`, `eval.py`, `embeddings.py`, `semantic.py` and `tests/fixtures/corpus/**`.
- Once `workflow_dispatch` is in, dispatch it once (`gh workflow run retrieval-gate.yml`) before changing anything else, to see what it does without the
  `embeddings` extra (it installs `.[test]` only, :24-28). Per copilot, `ledger ab run` builds the index and turns a
  build failure into exit 4 (`ledger/ab.py:460-496`), so expect an INVALID SETUP, not a silent lexical fallback.
  Then either install `[embeddings]` and `actions/cache` the HF model, or pin the CI gate to lexical
  and say so in the workflow (decision D4).
- `tests/fixtures/run_gate.sh:6-10` swallows the eval exit code (it `echo`s it, then exits 0) and hardcodes
  `.venv/bin/python`. Make it `exec ledger eval ...`, then wire it into `release.yml`, which runs only `pytest -q` today.
- Accept: a deliberate regression commit on a scratch branch pushed with `workflow_dispatch` turns the gate red; an identical commit reads NEUTRAL (needs B1).

## I2. Negatives and language cohorts must be able to fail the gate

`QUALITY_KEYS = ("hit1", "hitk", "mrr")` (`ledger/ab.py:31`). The A/B decision ignores
`false_positive_rate` / `abstain_accuracy` (`ledger/eval.py:492-508`), so adding negative cases
(I3) cannot stop a change that starts returning junk for unanswerable queries.
- Add `false_positive_rate` to the decision as a lower-is-better key: a rise beyond `EPSILON` = regression. Skip it when a case file has no negatives.
- Report hit@k per `lang` cohort (taken from a case-level `lang:` field, default `en`) in the ab report. A cohort drop is printed, not gated, until there are ≥ 10 cases in the cohort.
- Test: `tests/test_ledger_ab.py`: a decision where FP rate rises and mrr ties → regression.

## I3. Live eval suite that can see the corpus (after B2, B3, I2)

The live file has 46 cases: 0 negatives, 0 queries containing æøå (not proof of no Norwegian, but
close), 0 identity cases, 3 loop cases. bge-m3 was chosen for the Norwegian/English corpus,
and the eval cannot see a Norwegian regression.
- Add ≥ 8 negatives, ≥ 10 Norwegian queries with `lang: no`, ≥ 4 open-loop and ≥ 2 identity cases, and `as_of` history cases (B3).
- The owner writes or approves the cases. They are private and live in `~/brain/ledger/08_indices/`.
  An agent may draft candidates from note titles.
- The fixture file `tests/fixtures/retrieval_eval_cases.yaml` already has 8 negatives and identity
  cases. Extend it with Norwegian fixture notes and cases, and keep the corpus-local copy that
  `ab_gate.sh` actually reads (`tests/fixtures/corpus/notes/08_indices/retrieval_eval_cases.yaml`) in sync.
- Accept: `ledger eval --cases <live> --k 3 --strict-cases` runs; per-category and per-lang numbers recorded here.

## I4. Identity notes in `semantic_hybrid`: measure the asymmetry

Corrected by copilot, re-verified:
- `01_identity` is not embedded (`ledger/embeddings.py:36-45`).
- Hybrid candidates are built from the filesystem (`ledger/query.py:464-467`, `build_candidates`), not from the index.
- So identity notes *are* reachable on lexical overlap, with `semantic=0`.
- The `identity_score_boost` (`ledger/retrieval.py:1333-1334`) is applied only in lexical scoring, never in hybrid.

The AGENTS.md "stale-index warning" claim that an un-embedded note "cannot be retrieved at all"
in semantic modes overstates it; fix that sentence.
- Measure with the I3 identity cases: rank of the identity note in `semantic_hybrid` vs `legacy`.
- Only if identity cases miss: embed the 5 identity files and A/B it (`ab_gate.sh`). Otherwise leave it.
  Identity is in the boot payload anyway.

## I5. Schema consistency: a test, then the lint rules the schema already declares

The spec is spread over `schema.yaml`, `ledger/schema_values.py`, `ledger/validation.py`,
`templates/` and the AGENTS.md tables. Do not load YAML at runtime: add one test asserting
`schema_values` enums == `schema.yaml` enums. Then add lint rules (warnings) for what the schema declares:
- `identity_type` on identity notes (`schema.yaml:53`); identity count limit after D3.
- `status` only on loops: 14 facts and 5 prefs carry `status: open`, and 1 goal has `status: active`.
- `provenance` enum (`schema.yaml:105`); `supersedes` targets exist; dangling `[[links]]` (17 per `ledger links`).
- `max_tags` (`schema.yaml:222`): 1 note over 10.
- Slug pattern (`schema.yaml:205`): 2 live files violate it (`fact__sjæb_project.md`,
  `fact__nocos-sharepoint-editor-sql-over-cosmos.md`). Decide first whether non-ASCII slugs
  are allowed; then either widen the pattern or rename the two files.

Docs and templates:
- AGENTS.md frontmatter table: add `identity_type`, `via`, `origin`, `external_id`, `things_uuid`, `provenance`, `attribute`. They are in `schema.yaml` but missing from the AGENTS.md table.
- Signal types: add `supersession` and `contradiction_flagged` to the AGENTS.md list.
- Schema and AGENTS.md: document the `note__` prefix (289 inbox/archive files) and `things_list_id` (`cli.py:1726`, `things3_sync.py:114`).
- Decide `claude_type`/`id`/`type`/`due` (136/11/11/3 notes): document them or migrate them away.
- `schema.yaml:182` timeline path → `timeline.jsonl`.
- `validation.py:299-302` maps phantom `/07_reflections/` and `/08_people/`. Remove them and map `07_projects`.
- `templates/generic_note_template.md`:
  - The comment block between the `---` fences parses as empty frontmatter; move it below.
  - "Relative links only" contradicts `[[links]]`.
  - Add `valid_from`.

Lint noise: exempt ingest summaries in archive/inbox from "large file" (92 of 104 warnings), but not archived atomic notes.

Accept: the enum test passes; `ledger sleep lint` on the live corpus shows the new categories.

## I6. MCP: compact replies

- `ledger_query` (and the other rank tools) return a projection by default: path, title, type, score, trust, and a snippet capped at ~300 chars. `timing`, `semantic`, pool sizes and `expanded_tokens` go behind `verbose: true`.
  - Today a 3-result reply is ~5–6 KB.
  - This is MCP-only. `tests/test_seam_golden.py` locks `embed search` and `paths`, not `query --json`, and existing MCP tests do not lock response keys, so no seam changes.
- Add a `view` parameter to `ledger_recall_as_of` and `ledger_changed_since` (hardcoded `context` today).
- Verify `yaams_query`'s `--top-k` / `--format json` flags against the current yaams CLI before anyone enables `--with-yaams`.
- Accept: `tests/test_mcp_server.py` asserts the compact keys; a 3-result reply is < 2 KB.

## I7. Fresh A/B baselines on record

`.plans/ab_results/` locally holds only 2026-04-28 runs (it is gitignored, so other machines may
differ). The AGENTS.md lines "keep weighted_sum" and "keep PRF off" cite numbers with no current
run behind them in the repo. After B1 and I2, rerun once and save the reports under `ab_results/2026-10-<dd>/`:
- `LEDGER_FUSION=rrf` vs `weighted_sum`;
- `LEDGER_PRF_ENABLED=1` vs `0`;
- `LEDGER_WEIGHT_SIGNAL=0.1` vs `0`.

Update AGENTS.md only if a verdict flips.

## Deferred / cut (copilot second review, agreed)

- Warm the bge-m3 model on MCP server start: first call is 8.1s once per session; not worth the code.
- Recursive embedding of `07_projects`: the folder is empty, and lexical collection is not recursive either.
  Revisit when the first project note appears.
- Merging the two conflict-note locations (`00_inbox/conflict__*`, `_conflicts/`): triage reads both
  on purpose (`inbox.py:816-819`); no failure shown.
- Renaming `conventions.py` → `wire.py`: churn without behaviour.
- Coverage floor: pytest-cov isn't a declared dependency. Measure once when touching a thin module
  (`claude_memory.py`, `integrations/things3*.py`) and add targeted tests there; don't freeze an aggregate.

## Ties into existing queue

- B1 → I1 → I2 → I3 is the spine. agentisk PR 4 (Phase 5/6) extends the A/B harness to memory
  admission, so it inherits B1/I2 directly. Its blocker is still the owner skim of the gold set.
- ai-memory plan 14 waits on `synth_backend` (D2). Its T6 gate is a retention ratio, not an A/B exit
  code, so it does not depend on B1. It does share D2 with B8.
- Plans 10/16 stay parked on signal volume. D1 alone does not fix that (see the D1 note): a signal route is the lever.

## Second review

Copilot (`agent-bridge -m medium copilot`), 2026-10-08, answer-only. Every correction was
re-checked against the code before it went in.
- New: negatives cannot fail the gate (I2, `ab.py:31`).
- New: `run_gate.sh` swallows its exit code (I1).
- Rewritten: the identity item (I4). Hybrid candidates come from the filesystem, so the planned deletion of the boost was cut.
- I1 diagnosis corrected: expect exit 4, not a silent fallback. The fact that the gate never ran at all came from `gh run list` here, not from copilot.
- Corrected in I6: the seam-golden scope.
- Corrected in the queue ties: T6 is a retention ratio, not an exit code; D1 alone gives no signals.
- Cut or deferred: model warm-up, recursive projects, conflict-layout merge, `wire.py` rename, coverage floor.

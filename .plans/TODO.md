# TODO

## Reviewed 2026-06-10

Full plan-queue refresh. Every non-finished plan under `.plans/` was verified
against the codebase + git history and rewritten in place to be implementable
by Sonnet-level agents (file:line anchors, signatures, named test cases,
acceptance commands). Plan 37 (batch triage) shipped in `96e1e07` and moved to
`done/`. A fresh codebase review was written to `codebase-review-2026-06-10.md`;
its privacy findings (raw private-fenced content reaching the embeddings API,
LLM-judge prompts, contradiction conflict notes, and web backlinks) jump the
queue because privacy stripping is a project non-negotiable. Earlier review
notes (2026-05-15, 2026-06-04) are summarized in git history of this file.

## Active plan order

1. `codebase-review-2026-06-10.md` — Batch 1 (privacy gate: embeddings,
   llm_judge, contradiction notes, notes/browse/context layer) and Batch 2
   (data-loss: fzf-cancel mass-promote, import-claude-memory infinite loop,
   obsidian queue state) before any feature work. Later batches (concurrency,
   retrieval quality, architecture) can interleave with items below.
2. `36-yaams-tier2-a-rejection-log.md` — small remainder: `ledger inbox
   rejected` CLI, `list_rejections`/`clear_rejections`/`is_rejected` helpers,
   cleanup logging. `is_rejected` is a prerequisite for plan 38.
3. `38-yaams-tier2-c-semantic-dedup.md` — `ledger embed search` (cogled) +
   `yaams/promote/dedup.py` (yaams). Unlocks plan 40 and the triage `m` command.
4. `39-yaams-tier2-d-cross-tier-query.md` — fully independent; can run in
   parallel with 36/38. `yaams query --json` already exists; plan pins its
   live envelope.
5. `41-web-interface-v1.md` — Phase 4 graph view (includes the
   `Corpus._rebuild_link_maps()` private-link-stripping fix; coordinate with
   review Batch 1) + Phase 5 polish.
6. `40-yaams-tier2-e-conflict-detection.md` — after 38; or earlier against the
   `DedupVerdict` stub interface pinned in the plan.
7. `unify-signal-handling-across-signals-and-review-mo.md` — small; fixes a
   real divergence (query.py vs retrieval.py activation-gate defaults differ).
8. `cognitive_ledger_improvement_tasks_2026-05-03.md` — R1–R8 in 6 PRs,
   starting with PR 1 (demo init) and PR 2 (doctor expansion + `--fix`).
9. `refactor-retrieval-scoring-into-composable-scorers.md` — pure refactor,
   bit-identical scoring; do after review Batch 4 (retrieval quality) to avoid
   rebasing the same code twice.
10. `things3-open-loops-sync.md` — independent feature; anytime.

`35-yaams-tier2-integration-roadmap.md` is the index/status page for 36–40,
not an implementable plan itself.

## Cross-cutting checks before executing any plan

- Use fixture corpora in tests; do not depend on Kim's live notes.
- Strip private fenced content from every index, prompt, JSON output, and web/search snippet.
- Update README/CHANGELOG only for shipped user-facing behavior, not for plan edits.
- Preserve existing uncommitted work unless the active task explicitly owns it.
- [ ] Decouple ObsidianBackend CLI-facing I/O: init/bootstrap/run_import/watch/daemon_*/run_doctor/queue_sync/related print directly and return int exit codes; move rendering into ledger/importers/cli.py and return typed results like import_once/doctor (TODO marker in backends/obsidian/__init__.py)
- [ ] Add tests for ledger.query rank modes: two_stage, scope_type_prefilter, progressive_disclosure (tests/test_query.py is thin vs query.py 682 LoC)
- [ ] Add --json output to ledger briefing (cli.py ~925); briefing currently text-only, blocks programmatic use
- [ ] Move _note_index_path / _candidates_path helpers into ledger/layout.py and replace duplicate path construction in maintenance.py and retrieval.py
- [ ] Extract _lint_note frontmatter heuristics from maintenance.py (~422-558) into ledger/validation.py so web /admin can reuse without circular imports
- [ ] Remove test-compat re-exports at top of ledger/cli.py (lines ~54-81); update tests to import from source modules directly
- [ ] Add module docstring to ledger/layout.py diagramming the index/artifact path layout (single source of truth referenced by maintenance, retrieval, web, obsidian)
- [ ] Add parametrized tests for ledger.inbox.triage_suggestions() type-inference heuristics (covers all 5 note types + fallback)
- [ ] Log full traceback in CLI top-level except handlers when LEDGER_DEBUG=1; today emit_action swallows the underlying exception
- [ ] Document LEDGER_EMBEDDINGS_OFFLINE env var in AGENTS.md (added 2026-06-04 in ledger/embeddings.py to silence HF Hub metadata round-trip)
- [ ] Normalize claude_memory_import_state.json under the shared adapter-state root (08_indices/importers/claude_memory/state.json) — left loose in 08_indices when plan 42 phase 4 normalized folder+obsidian; ledger import-claude-memory is not a plan-42 backend yet
- [ ] make sheep status/lint/sleep emit structured fields under --json (index/sync already do)
- [ ] Keep `contradiction_enabled: false` until the NLI model is downloaded and ~20 real Norwegian contradiction pairs are hand-checked.
- [ ] Keep `prf_enabled: false` until `ledger ab run` shows recall@k improvement without latency regression.
- [ ] Keep `fusion: weighted_sum`; only switch to `rrf` after a future A/B run beats weighted-sum on the real corpus.

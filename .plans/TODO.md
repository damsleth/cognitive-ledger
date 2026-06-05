# TODO

## Reviewed 2026-05-15

Top-level plan files under `.plans/` were reviewed and enriched. The `done/` folder was excluded. Historical A/B result artifacts under `.plans/ab_results/` were treated as experiment records, not active implementation plans. A second-pass review on 2026-05-15 (afternoon) refreshed plan 41 after Phase 2 search shipped in `2b085fe`. The 2026-06-04 review refreshed the queue after bitemporal validity, cold-start prior tie-breaks, NLI contradiction scan, and web Phase 3 shipped.

## Active plan order

1. `42-import-adapters-refactor.md` - `ledger/importers/` skeleton exists; next slice is the `ledger-obsidian` parity matrix plus `ledger import` CLI dispatch scaffolding.
2. `41-web-interface-v1.md` - Phases 1-3, `/healthz`, `/admin/reload`, search cache busting, and static provenance shipped; next slice is Phase 4 graph view.
3. `36-yaams-tier2-a-rejection-log.md` - add explicit `ledger inbox reject` and versioned rejection JSONL (current CLI only exposes `inbox {list|triage|cleanup}`).
4. `38-yaams-tier2-c-semantic-dedup.md` - implement `ledger embed search` before YAAMS wiring (current `embed` subcommands are `build|status|clean`).
5. `cognitive_ledger_improvement_tasks_2026-05-03.md` - split the first PR into demo init + doctor expansion, then review.

## Cross-cutting checks before executing any plan

- Use fixture corpora in tests; do not depend on Kim's live notes.
- Strip private fenced content from every index, prompt, JSON output, and web/search snippet.
- Update README/CHANGELOG only for shipped user-facing behavior, not for plan edits.
- Preserve existing uncommitted work unless the active task explicitly owns it.
- [ ] Add tests for ledger.query rank modes: two_stage, scope_type_prefilter, progressive_disclosure (tests/test_query.py is thin vs query.py 682 LoC)
- [ ] Add --json output to ledger briefing (cli.py ~925); briefing currently text-only, blocks programmatic use
- [ ] Move _note_index_path / _candidates_path helpers into ledger/layout.py and replace duplicate path construction in maintenance.py and retrieval.py
- [ ] Extract _lint_note frontmatter heuristics from maintenance.py (~422-558) into ledger/validation.py so web /admin can reuse without circular imports
- [ ] Remove test-compat re-exports at top of ledger/cli.py (lines ~54-81); update tests to import from source modules directly
- [ ] Add module docstring to ledger/layout.py diagramming the index/artifact path layout (single source of truth referenced by maintenance, retrieval, web, obsidian)
- [ ] Add parametrized tests for ledger.inbox.triage_suggestions() type-inference heuristics (covers all 5 note types + fallback)
- [ ] Log full traceback in CLI top-level except handlers when LEDGER_DEBUG=1; today emit_action swallows the underlying exception
- [ ] Document LEDGER_EMBEDDINGS_OFFLINE env var in AGENTS.md (added today in ledger/embeddings.py to silence HF Hub metadata round-trip)
- [ ] make sheep status/lint/sleep emit structured fields under --json (index/sync already do) — migrated from hugr/.plans
- [ ] Keep `contradiction_enabled: false` until the NLI model is downloaded and ~20 real Norwegian contradiction pairs are hand-checked.
- [ ] Keep `prf_enabled: false` until `ledger ab run` shows recall@k improvement without latency regression.
- [ ] Keep `fusion: weighted_sum`; only switch to `rrf` after a future A/B run beats weighted-sum on the real corpus.

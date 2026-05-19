# TODO

## Reviewed 2026-05-15

Top-level plan files under `.plans/` were reviewed and enriched. The `done/` folder was excluded. Historical A/B result artifacts under `.plans/ab_results/` were treated as experiment records, not active implementation plans. A second-pass review on 2026-05-15 (afternoon) refreshed plan 41 after Phase 2 search shipped in `2b085fe`.

## Active plan order

1. `41-web-interface-v1.md` - Phases 1-2 shipped; next slice is `/healthz` + `/admin/reload` (with search-cache bust) before Phase 3 backlinks.
2. `42-import-adapters-refactor.md` - rename implementation package target from `ledger/import` to `ledger/importers` before anyone starts coding (no `ledger/importers/` exists yet on disk).
3. `36-yaams-tier2-a-rejection-log.md` - add explicit `ledger inbox reject` and versioned rejection JSONL (current CLI only exposes `inbox {list|triage|cleanup}`).
4. `38-yaams-tier2-c-semantic-dedup.md` - implement `ledger embed search` before YAAMS wiring (current `embed` subcommands are `build|status|clean`).
5. `cognitive_ledger_improvement_tasks_2026-05-03.md` - split the first PR into demo init + doctor expansion, then review.

## Cross-cutting checks before executing any plan

- Use fixture corpora in tests; do not depend on Kim's live notes.
- Strip private fenced content from every index, prompt, JSON output, and web/search snippet.
- Update README/CHANGELOG only for shipped user-facing behavior, not for plan edits.
- Preserve existing uncommitted work unless the active task explicitly owns it.

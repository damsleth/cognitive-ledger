# TODO

Prioritized list of remaining work. Plans in `.doc/plans/`.

## Done (2026-03-30)

1. ~~**Fix duplicate EvalCaseValidationError**~~ — consolidated to errors.py
2. ~~**Make semantic retrieval respect LEDGER_ROOT_DIR**~~ — embedding paths + TUI discovery now use config
3. ~~**Wire alias discovery into sheep index**~~ — already implemented, confirmed working
4. ~~**Fix test suite issues**~~ — debug prints removed, stale cache fixed, all 317 tests pass
5. ~~**Add query telemetry log**~~ — opt-in JSONL logging via LEDGER_QUERY_LOG=1
6. ~~**Slim scripts/ledger**~~ — 666 → 595 lines, removed redundant delegation layer

## Remaining

7. **Unify TUI and library note models** — parallel type hierarchies (typed enums vs raw dicts), needs dedicated design session ([plan](plans/04-model-unification.md))
8. **Expand test coverage** — no tests for validation, obsidian adapter, TUI widgets ([plan](plans/08-test-coverage.md))
9. **Complete obsidian boundary refactor** — separate runtime surfaces, prune dead code, add tests ([plan](plans/09-obsidian-boundary.md))

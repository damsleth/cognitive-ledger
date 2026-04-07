# TODO

Remaining work. Plans in `.doc/plans/`.

1. **Unify TUI and library note models** — parallel type hierarchies (typed enums vs raw dicts), needs dedicated design session ([plan](plans/04-model-unification.md))
2. **Expand test coverage** — no tests for validation, obsidian adapter, TUI widgets ([plan](plans/08-test-coverage.md))
3. **Complete obsidian boundary refactor** — separate runtime surfaces, prune dead code, add tests ([plan](plans/09-obsidian-boundary.md))
4. ~~**TELOS-inspired identity layer** — new `identity` note type in `01_identity/` with retrieval boost and boot context integration ([plan](plans/10-telos-identity-layer.md))~~ **Done**
5. ~~**Session lifecycle hooks** — session-start/post-write/session-end scripts, `ledger context` subcommand ([plan](plans/11-session-lifecycle-hooks.md))~~ **Done**
6. ~~**Signal capture & feedback loop** — `ledger/signals.py` module, `ledger signal` CLI, retrieval scoring integration ([plan](plans/12-signal-capture-feedback-loop.md))~~ **Done**

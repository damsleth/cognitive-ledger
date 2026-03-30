# Complete Obsidian Boundary Refactor (Phases 3-4)

## Problem

Phases 1-2 (import audit, export boundary) are done. Phases 3-4 remain:
- Long-running concerns (watch, daemon) mixed with import logic
- No dedicated tests for obsidian adapter commands
- Potential dead code in obsidian helpers

## Plan

### Phase 3: Clarify Runtime Surfaces

1. Audit `ledger/obsidian/` module structure — identify which files own which concerns:
   - Import/bootstrap: `importer.py`, `extraction.py`
   - Queue: `queue.py`, `state.py`
   - Watch: `watch.py`
   - Daemon: `daemon.py`
   - Doctor: `doctor.py`
   - CLI: `cli.py`
   - Utils: `utils.py`, `models.py`, `config.py`, `layout.py`, `bases.py`
2. Ensure `doctor` only checks adapter-level concerns (vault health, queue state, daemon status) — not duplicating `sheep lint` validation
3. If import logic is tangled with watch/daemon, extract a clean `run_single_import()` function

### Phase 4: Test and Prune

1. Add targeted tests (see `08-test-coverage.md` Priority 3)
2. Trace each function in `utils.py`, `models.py`, `bases.py` to see if it's on a supported command path
3. Delete or mark as `_internal` anything not reachable from the supported CLI commands
4. Verify `__all__` in `__init__.py` stays at the 4 supported exports

## Verification

```bash
./.venv/bin/pytest tests/obsidian/ -q  # new tests pass
# verify no core imports from obsidian:
grep -r "from ledger.obsidian" ledger/ --include="*.py" | grep -v "ledger/obsidian/"
# should return nothing
```

## Effort

~2 sessions. Phase 3 is audit + minor refactoring. Phase 4 is testing + pruning.

# Expand Test Coverage

## Problem

No dedicated tests for: `ledger/validation.py`, `ledger/ab_probe.py`, `ledger/venv.py`, `ledger/retrieval_types.py`, 11 `ledger/obsidian/` modules, `tui/services/sheep_runner.py`, most TUI widgets/screens.

Also: `LedgerIntegrationTests` (T1) and `test_context_profiles` (T2) run against the real notes directory with no isolation.

## Plan

### Priority 1: Fix flaky tests (T1, T2)

1. `tests/test_ledger.py:577` — `LedgerIntegrationTests` should use a temp directory with fixture notes, not real `notes/`
2. `tests/test_context_profiles.py:13` — subprocess test should point at a temp note tree

### Priority 2: Core library coverage

1. `tests/test_validation.py` — test each validator (query, scope, path) with valid/invalid inputs
2. `tests/test_retrieval_types.py` — test dataclass construction, serialization round-trips

### Priority 3: Obsidian adapter coverage

Focus on the 4 supported command paths:
1. `tests/obsidian/test_queue.py` — queue sync transitions (pending → promoted → done)
2. `tests/obsidian/test_importer.py` — root bootstrap, vault bootstrap, collision handling
3. `tests/obsidian/test_doctor.py` — health check output
4. `tests/obsidian/test_cli.py` — CLI argument wiring and exit codes

### Priority 4: TUI coverage

1. `tests/tui/test_sheep_runner.py` — mock subprocess, verify lint output parsing
2. Basic widget tests for note tree, graph panel

## Verification

```bash
./.venv/bin/pytest -q
./.venv/bin/pytest --co -q | wc -l  # should increase from 317
```

## Effort

~3-4 sessions. Priority 1-2 are ~1 session; Priority 3-4 are ~2-3 sessions.

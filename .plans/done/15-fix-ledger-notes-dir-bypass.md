# Plan 15: Fix LEDGER_NOTES_DIR Bypass in Retrieval and Browse

## Problem

`LedgerConfig.notes_dir` documents `LEDGER_NOTES_DIR` as the mechanism for
decoupling the note corpus from the repo root (e.g. for A/B testing). The
retrieval and browse implementations ignore that contract and resolve note
folders from `config.root_dir / info["dir"]` instead of `config.notes_dir`.

This means `./scripts/ledger query`, `notes`, `loops`, and `semantic_hybrid`
all silently read the wrong corpus when `LEDGER_NOTES_DIR` is set.
Additionally, `candidate_from_note()` raises `ValueError` for notes that
live outside the repo root because it calls `path.resolve().relative_to(root_dir)`.

Reproduced locally: `build_candidates(use_cache=False)` returned 0 candidates
with a valid external corpus, and `candidate_from_note()` raised on an
external note path.

## Priority

P1 - Advertised feature is broken, affects A/B testing reliability.

## Plan

### 1. Fix `_note_types()` in retrieval.py

File: `ledger/retrieval.py`, lines 70-75.

The `_note_types()` helper resolves dirs via `config.root_dir / info["dir"]`.
The note_types dict values have paths like `"notes/01_identity"` which are
relative to root. But when LEDGER_NOTES_DIR is set, notes live elsewhere.

**Fix:** The note_types config stores paths like `notes/01_identity`. Strip
the `notes/` prefix and resolve against `config.notes_dir` instead:

```python
def _note_types() -> dict[str, dict[str, Any]]:
    config = _cfg()
    return {
        name: {
            "dir": config.notes_dir / info["dir"].removeprefix("notes/"),
            "label": info["label"],
        }
        for name, info in config.note_types.items()
    }
```

### 2. Fix `_note_types()` in browse.py

File: `ledger/browse.py`, lines 41-46. Identical bug, identical fix.

### 3. Fix relative path serialization in retrieval.py

`candidate_from_note()` calls `path.resolve().relative_to(config.root_dir.resolve())`
which raises when the note lives outside root_dir. Fix: try `notes_dir` first,
fall back to `root_dir`, and if neither works use the absolute path.

### 4. Fix ab_probe.py fallback

File: `ledger/ab_probe.py`, line 141. The fallback `worktree / "notes"` should
use `get_config().notes_dir` instead, so it respects whatever the config says.

### 5. Add tests

- Test that `build_candidates()` finds notes when `LEDGER_NOTES_DIR` points
  to a tmpdir with valid notes.
- Test that `sorted_items()` returns items from the external dir.
- Test that `candidate_from_note()` does not raise for notes outside root_dir.

## Key Files

- `ledger/retrieval.py` (lines 70-75, candidate_from_note rel_path logic)
- `ledger/browse.py` (lines 41-46)
- `ledger/ab_probe.py` (line 141)
- `tests/test_retrieval_notes_dir.py` (new)

## Verification

- `pytest -q --tb=short` passes
- Set `LEDGER_NOTES_DIR=/tmp/test-corpus`, create a valid note there,
  run `./scripts/ledger query "test"` - verify it finds the note
- Run `./scripts/ledger_ab --corpus /tmp/test-corpus` - verify no ValueError

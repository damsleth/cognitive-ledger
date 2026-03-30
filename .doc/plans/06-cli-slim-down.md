# Slim Down scripts/ledger to < 500 Lines

## Problem

`scripts/ledger` is 666 lines (target: < 500). Context extraction is done, A/B probe is in library, but inline CLI logic remains.

## Plan

### Step 1: Audit what's left in the script

1. Read `scripts/ledger` and categorize remaining code:
   - Argument parsing / subparser setup
   - Output formatting / display
   - Business logic that should be in library

### Step 2: Move remaining business logic to library

Likely candidates:
- Any remaining inline scoring/ranking code
- eval-case loading that isn't already in `ledger/eval.py`
- Bundle/format logic that could live in `ledger/query.py`

### Step 3: Simplify argument parsing

1. Consolidate subparser setup into a dispatch table pattern
2. Each command handler should be ~5-10 lines calling library functions

### Step 4: Verify

```bash
wc -l scripts/ledger  # target: < 500
./scripts/ledger query "test" --scope all
./scripts/ledger eval --cases notes/08_indices/retrieval_eval_cases.yaml --k 3
./scripts/ledger loops
./.venv/bin/pytest -q
```

## Effort

~1 session. Incremental extraction, no behavioral changes.

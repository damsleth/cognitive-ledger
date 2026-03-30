# Fix Remaining Critical Bug: Duplicate EvalCaseValidationError (C6)

## Problem

`EvalCaseValidationError` is defined in both `ledger/eval.py:40` and `ledger/errors.py:219` with incompatible interfaces. Cross-module `except` won't catch both.

## Plan

1. Read both definitions to understand the interface difference
2. Keep the one in `ledger/errors.py` as the canonical definition (errors.py owns the exception hierarchy)
3. Delete the definition from `ledger/eval.py`
4. Update `ledger/eval.py` to import from `ledger.errors`
5. Grep for any other imports of the eval.py version and update them
6. Run tests to confirm no breakage

## Verification

```bash
./.venv/bin/pytest -q
grep -r "EvalCaseValidationError" ledger/ tests/
```

Should show exactly one definition site (errors.py) and only imports elsewhere.

## Effort

~15 minutes. Single-file change + import updates.

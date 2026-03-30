# Wire Alias Discovery Into sheep index

## Problem

`_generate_alias_suggestions()` exists in `ledger/maintenance.py` but `aliases_suggested.json` is never actually generated. The logic for tag co-occurrence and phrase extraction is implemented but not connected to `cmd_index()`.

## Plan

1. Read `_generate_alias_suggestions()` in `ledger/maintenance.py` to understand current logic
2. Verify `cmd_index()` calls it (or add the call if missing)
3. Ensure output writes to `notes/08_indices/aliases_suggested.json`
4. Add a diff/filter step: exclude suggestions that already exist in `aliases.json`
5. Add a summary line to `cmd_index()` output: "N new alias suggestions written"

## Verification

```bash
./scripts/sheep index
cat notes/08_indices/aliases_suggested.json
# should contain suggested alias mappings not already in aliases.json
```

Run A/B eval to confirm no retrieval regression:
```bash
./scripts/ledger eval --cases notes/08_indices/retrieval_eval_cases.yaml --k 3
```

## Effort

~30 minutes. The logic exists; this is wiring and output formatting.

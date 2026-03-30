# Make Semantic Retrieval Fully Pluggable

## Problem

Lexical retrieval respects `LEDGER_ROOT_DIR`, but embedding paths in `scripts/ledger_embeddings.py` compute `SEMANTIC_ROOT` and `LEDGER_NOTES_ROOT` relative to the script's location, not the configured ledger root. A bootstrapped external ledger silently reads/writes indices from the repo clone.

## Plan

### Step 1: Route embedding paths through LedgerConfig

1. Add `semantic_root` and `notes_root` properties to `LedgerConfig` (in `ledger/config.py`)
2. These should derive from `root_dir`, not from `__file__`

### Step 2: Update scripts/ledger_embeddings.py

1. Replace hardcoded `ROOT_DIR = Path(__file__).resolve().parent.parent` with `get_config().root_dir`
2. Replace `SEMANTIC_ROOT = ROOT_DIR / ".smart-env" / "semantic"` with `get_config().semantic_root`
3. Replace `LEDGER_NOTES_ROOT = ROOT_DIR / "notes"` with `get_config().notes_root`

### Step 3: Update ledger/query.py

1. Check all path construction in query.py for similar hardcoding
2. Route through config

### Step 4: Fix TUI auto-discovery

1. In `tui/__main__.py`, add `LEDGER_ROOT_DIR` env var check to the discovery chain
2. Current order: CLI arg → cwd → ~/cognitive-ledger
3. New order: CLI arg → `LEDGER_ROOT_DIR` env → cwd → ~/cognitive-ledger

### Step 5: Fix CLI side-effect paths

1. Telemetry path (`query_log.jsonl`) should use `get_config().root_dir / "notes" / "08_indices"`
2. `eval --write-baseline` output path should default relative to configured root

## Verification

```bash
export LEDGER_ROOT_DIR=/tmp/test-ledger
# bootstrap a test ledger
ledger-obsidian bootstrap --root /tmp/test-ledger
# verify embed build writes to /tmp/test-ledger/.smart-env/, not repo clone
./scripts/ledger embed build --target ledger --backend local
ls /tmp/test-ledger/.smart-env/semantic/
```

## Effort

~1 session. Config changes are small; main work is finding and replacing all hardcoded paths.

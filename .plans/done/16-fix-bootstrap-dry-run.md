# Plan 16: Fix bootstrap --dry-run Being Destructive

## Problem

`ledger-obsidian bootstrap --dry-run` is supposed to preview what would happen
without modifying the vault. But `cmd_bootstrap()` unconditionally calls
`ensure_layout(config)`, `save_config(config)`, and `write_bases(config)`
before passing `dry_run=True` to `run_import()`.

Reproduced locally: after running `bootstrap --dry-run` on a fresh tmpdir,
the vault contained `cognitive-ledger/config.json`, two `.base` files, and
`.lock` artifacts.

## Priority

P1 - `--dry-run` is actively destructive, opposite of its documented behavior.

## Plan

### 1. Guard destructive calls in cmd_bootstrap()

File: `ledger/obsidian/cli.py`, lines 88-120.

Current code:
```python
def cmd_bootstrap(args: argparse.Namespace) -> int:
    root = _parse_root(args.root)
    config = default_config(root)

    ensure_layout(config)      # always runs
    save_config(config)        # always runs
    write_bases(config)        # always runs
    validate_config(config)    # always runs

    result = run_import(config, dry_run=bool(args.dry_run), ...)
```

Fix: wrap the mutating calls in `if not args.dry_run`:

```python
def cmd_bootstrap(args: argparse.Namespace) -> int:
    root = _parse_root(args.root)
    config = default_config(root)

    if not args.dry_run:
        ensure_layout(config)
        save_config(config)
        write_bases(config)

    validate_config(config)

    result = run_import(config, dry_run=bool(args.dry_run), ...)
```

`validate_config` is read-only so it stays outside the guard. Note that
`validate_config` may fail if layout doesn't exist yet - if so, skip
validation in dry-run mode too, or catch and report.

### 2. Add test for bootstrap --dry-run

Create a test that runs `cmd_bootstrap` with `dry_run=True` against a
tmpdir and asserts no files were created in the vault.

## Key Files

- `ledger/obsidian/cli.py` (cmd_bootstrap, lines 88-120)
- `tests/test_obsidian_bootstrap_dry_run.py` (new)

## Verification

- `pytest -q --tb=short` passes
- `ledger-obsidian bootstrap --root /tmp/test-vault --dry-run` creates no files
- `ledger-obsidian bootstrap --root /tmp/test-vault` (without --dry-run) still works

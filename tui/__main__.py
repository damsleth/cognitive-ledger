#!/usr/bin/env python3
"""Entry point for the Cognitive Ledger TUI."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledger.venv import maybe_reexec_in_repo_venv

DEFAULT_ROOT = Path.home() / "cognitive-ledger"


def _fail_on_removed_env_vars() -> None:
    removed = {
        "LEDGER_ROOT_DIR": "LEDGER_ROOT",
        "LEDGER_SOURCE_ROOT": "LEDGER_SOURCE_NOTES_DIR",
    }
    for old, new in removed.items():
        if os.getenv(old) is not None:
            print(f"Error: {old} has been removed. Use {new} instead.", file=sys.stderr)
            sys.exit(2)


def find_paths() -> tuple[Path, Path]:
    """Find the ledger root and ledger notes directory."""
    _fail_on_removed_env_vars()

    # 1. CLI argument
    if len(sys.argv) > 1:
        path = Path(sys.argv[1]).expanduser().resolve()
        if not path.exists():
            print(f"Error: Ledger root does not exist: {path}", file=sys.stderr)
            sys.exit(1)
        root = path
    elif env_root := os.getenv("LEDGER_ROOT"):
        root = Path(env_root).expanduser().resolve()
    else:
        cwd = Path.cwd()
        if (cwd / "scripts" / "ledger").is_file():
            root = cwd
        elif DEFAULT_ROOT.exists():
            root = DEFAULT_ROOT
        else:
            print("Error: Cannot find cognitive ledger", file=sys.stderr)
            print("Run from ledger directory, or pass path as argument:", file=sys.stderr)
            print("  ledger-tui <ledger-root>", file=sys.stderr)
            sys.exit(1)

    notes_env = os.getenv("LEDGER_NOTES_DIR")
    notes_dir = Path(notes_env).expanduser().resolve() if notes_env else (root / "notes").resolve()
    if notes_dir.is_dir():
        return root, notes_dir

    print(f"Error: Ledger notes directory does not exist: {notes_dir}", file=sys.stderr)
    if notes_env:
        print("Set LEDGER_NOTES_DIR to a valid notes directory.", file=sys.stderr)
    else:
        print("Set LEDGER_NOTES_DIR or create notes/ under the ledger root.", file=sys.stderr)
    sys.exit(1)


def main():
    """Run the TUI."""
    maybe_reexec_in_repo_venv(REPO_ROOT, module="tui")
    root, notes_dir = find_paths()

    from tui.app import LedgerApp

    app = LedgerApp(ledger_root=root, ledger_notes_dir=notes_dir)
    app.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Entry point for the Cognitive Ledger TUI."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ledger.venv import maybe_reexec_in_repo_venv

DEFAULT_ROOT = Path.home() / "cognitive-ledger"


def find_root() -> Path:
    """Find the ledger root directory."""
    # 1. CLI argument
    if len(sys.argv) > 1:
        path = Path(sys.argv[1]).expanduser().resolve()
        if (path / "notes").is_dir():
            return path
        print(f"Error: No notes/ directory at: {path}", file=sys.stderr)
        sys.exit(1)

    # 2. Current working directory
    cwd = Path.cwd()
    if (cwd / "notes").is_dir():
        return cwd

    # 3. Default location
    if (DEFAULT_ROOT / "notes").is_dir():
        return DEFAULT_ROOT

    print("Error: Cannot find cognitive ledger", file=sys.stderr)
    print("Run from ledger directory, or pass path as argument:", file=sys.stderr)
    print("  ledger-tui <ledger-root>", file=sys.stderr)
    sys.exit(1)


def main():
    """Run the TUI."""
    maybe_reexec_in_repo_venv(REPO_ROOT, module="tui")
    root = find_root()

    from tui.app import LedgerApp

    app = LedgerApp(root_dir=root)
    app.run()


if __name__ == "__main__":
    main()

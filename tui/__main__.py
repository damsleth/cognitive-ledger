#!/usr/bin/env python3
"""Entry point for the Cognitive Ledger TUI."""

import os
import sys
from pathlib import Path

DEFAULT_ROOT = Path.home() / "cognitive-ledger"
REPO_ROOT = Path(__file__).resolve().parents[1]


def maybe_reexec_in_repo_venv() -> None:
    if os.environ.get("COG_LEDGER_VENV_REEXEC") == "1":
        return
    venv_dir = REPO_ROOT / ".venv"
    venv_python = venv_dir / "bin" / "python"
    if not venv_python.is_file():
        return
    try:
        in_target_venv = Path(sys.prefix).resolve() == venv_dir.resolve()
    except Exception:
        return
    if in_target_venv:
        return
    env = os.environ.copy()
    env["COG_LEDGER_VENV_REEXEC"] = "1"
    env["VIRTUAL_ENV"] = str(venv_dir.resolve())
    env["PATH"] = f"{venv_dir / 'bin'}:{env.get('PATH', '')}"
    os.execve(
        str(venv_python),
        [str(venv_python), "-m", "tui", *sys.argv[1:]],
        env,
    )


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
    maybe_reexec_in_repo_venv()
    root = find_root()

    from tui.app import LedgerApp

    app = LedgerApp(root_dir=root)
    app.run()


if __name__ == "__main__":
    main()

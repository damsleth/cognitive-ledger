#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ledger.context import build_context, write_context
from ledger.venv import maybe_reexec_in_repo_venv


def main():
    parser = argparse.ArgumentParser(description="Generate deterministic boot context index from ledger notes")
    parser.add_argument("--notes-dir", required=True, help="Path to notes directory")
    parser.add_argument("--output", required=True, help="Path to output markdown file")
    args = parser.parse_args()

    notes_dir = Path(args.notes_dir)
    output = Path(args.output)
    write_context(output, notes_dir)


if __name__ == "__main__":
    maybe_reexec_in_repo_venv(ROOT_DIR, script_path=Path(__file__).resolve())
    main()

#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from ledger.context import (
    SCOPES,
    collect_profile_items as collect_notes,
    render_profile,
    write_context_profiles,
)
from ledger.venv import maybe_reexec_in_repo_venv


def main():
    parser = argparse.ArgumentParser(description="Generate scoped context profiles from ledger notes")
    parser.add_argument("--notes-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    notes_dir = Path(args.notes_dir)
    output_dir = Path(args.output_dir)
    write_context_profiles(output_dir, notes_dir)


if __name__ == "__main__":
    maybe_reexec_in_repo_venv(ROOT_DIR, script_path=Path(__file__).resolve())
    main()

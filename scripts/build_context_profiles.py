#!/usr/bin/env python3
"""Compatibility shim - use `ledger context profiles` instead."""
import sys
from pathlib import Path

try:
    from ledger.cli import main
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ledger.cli import main

if __name__ == "__main__":
    sys.exit(main(["context", "profiles"] + sys.argv[1:]))

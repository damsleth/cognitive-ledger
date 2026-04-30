#!/usr/bin/env python3
"""Compatibility shim - use `ledger context build` instead."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ledger.cli import main

if __name__ == "__main__":
    sys.exit(main(["context", "build"] + sys.argv[1:]))

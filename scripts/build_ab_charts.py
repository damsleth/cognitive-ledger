#!/usr/bin/env python3
"""Compatibility shim - use `ledger ab charts` instead."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ledger.ab_charts import main

if __name__ == "__main__":
    main()

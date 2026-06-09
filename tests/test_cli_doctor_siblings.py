"""`sheep --doctor` CLI contract checks.

The sheep binary must expose the same data-class doctor schema as `ledger
--doctor` so a caller can fan out over a stable shape (tool,
version, config_path?, data_path?, findings[]).
"""
from __future__ import annotations

import json
import subprocess
import sys


def _run(module: str, *args):
  return subprocess.run(
    [sys.executable, "-m", module, *args],
    capture_output=True, text=True,
  )


# --- sheep -----------------------------------------------------------------

def test_sheep_doctor_json_shape():
  result = _run("ledger.maintenance", "--doctor", "--json")
  payload = json.loads(result.stdout.strip())
  assert payload["tool"] == "sheep"
  assert "version" in payload
  assert isinstance(payload["findings"], list)
  # Data-class reserved-key contract.
  assert "ok" not in payload


def test_sheep_doctor_human_default():
  result = _run("ledger.maintenance", "--doctor")
  assert "sheep doctor" in result.stdout


def test_sheep_doctor_redaction_sentinel():
  result = _run("ledger.maintenance", "--doctor", "--json")
  ids = [f["id"] for f in json.loads(result.stdout.strip())["findings"]]
  assert "redact_sentinel_leak" not in ids

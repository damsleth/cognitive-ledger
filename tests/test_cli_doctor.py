"""`ledger --doctor` data-class health check.

Pins the CLI contract doctor JSON schema for the ledger tool.
"""
from __future__ import annotations

import json
import subprocess
import sys


def _run(*args, env=None):
  """Invoke the ledger CLI via -m so we don't need the venv to be on PATH."""
  return subprocess.run(
    [sys.executable, "-m", "ledger", *args],
    capture_output=True, text=True, env=env,
  )


def test_doctor_json_shape():
  result = _run("--doctor", "--json")
  payload = json.loads(result.stdout.strip())
  assert payload["tool"] == "ledger"
  assert "version" in payload
  assert isinstance(payload["findings"], list)
  # Reserved-key contract: doctor is data class -> no top-level `ok`.
  assert "ok" not in payload


def test_doctor_human_default():
  result = _run("--doctor")
  assert "ledger doctor" in result.stdout


def test_doctor_redaction_sentinel_does_not_fire():
  result = _run("--doctor", "--json")
  payload = json.loads(result.stdout.strip())
  ids = [f["id"] for f in payload["findings"]]
  assert "redact_sentinel_leak" not in ids
  assert "redact_unavailable" not in ids


def test_doctor_exit_code_is_well_defined():
  result = _run("--doctor", "--json")
  # Either 0 (all clean / warnings only) or 1 (error findings). Never
  # an unhandled crash code or 2 (which would mean transient).
  assert result.returncode in (0, 1)

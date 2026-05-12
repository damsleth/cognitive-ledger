"""`sheep --doctor` and `ledger-obsidian --doctor` mnem contract checks.

Both sibling binaries must expose the same data-class doctor schema
as `ledger --doctor` so that `mnem doctor` can fan out over a stable
shape (tool, version, config_path?, data_path?, findings[]).
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


# --- ledger-obsidian -------------------------------------------------------

def test_obsidian_doctor_json_shape():
  result = _run("ledger.obsidian.cli", "--doctor", "--json")
  payload = json.loads(result.stdout.strip())
  assert payload["tool"] == "ledger-obsidian"
  assert "version" in payload
  assert isinstance(payload["findings"], list)
  assert "ok" not in payload


def test_obsidian_doctor_human_default():
  result = _run("ledger.obsidian.cli", "--doctor")
  assert "ledger-obsidian doctor" in result.stdout


def test_obsidian_subcommand_doctor_still_works():
  """The original `ledger-obsidian doctor` subcommand stays for back-compat."""
  # We can't reliably test the full doctor subcommand without a vault,
  # but the subcommand still has to parse and route. Just smoke-test
  # the help output mentions it.
  result = _run("ledger.obsidian.cli", "--help")
  assert "doctor" in result.stdout

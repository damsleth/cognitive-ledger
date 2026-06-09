"""`sheep --json <cmd>` smoke tests.

Each sheep subcommand emits structured JSON on stdout under --json,
following the CLI contract output classes:

- status:   data    -> raw status doc, no top-level `ok`
- lint:     data    -> {tool, command, exit_code, human_lines}
- sleep:    data    -> {tool, command, exit_code, human_lines}
- sync (no --apply): data -> {applied: false, ...}
- sync --apply:      action -> envelope (ok/error/stats)
- index:    action  -> envelope (ok/error/stats)
"""
from __future__ import annotations

import json
import subprocess
import sys


def _run(*args):
  return subprocess.run(
    [sys.executable, "-m", "ledger.maintenance", *args],
    capture_output=True, text=True,
  )


def test_sheep_status_json_data_shape():
  result = _run("--json", "status")
  payload = json.loads(result.stdout.strip())
  # Data class: no top-level `ok`.
  assert "ok" not in payload
  assert "timeline_exists" in payload
  assert "sleep_recommended" in payload
  assert "sync_drift" in payload


def test_sheep_status_human_default_still_works():
  result = _run("status")
  assert "Sleep Status" in result.stdout


def test_sheep_lint_json_wraps_human_lines():
  result = _run("--json", "lint")
  payload = json.loads(result.stdout.strip())
  assert payload["tool"] == "sheep"
  assert payload["command"] == "sheep lint"
  assert "exit_code" in payload
  assert isinstance(payload["human_lines"], list)


def test_sheep_sleep_json_wraps_human_lines():
  result = _run("--json", "sleep")
  payload = json.loads(result.stdout.strip())
  assert payload["command"] == "sheep sleep"
  assert isinstance(payload["human_lines"], list)


def test_sheep_sync_check_is_data_class():
  result = _run("--json", "sync", "--check")
  payload = json.loads(result.stdout.strip())
  # Data class: no top-level `ok`; carries applied=false.
  assert "ok" not in payload
  assert payload["applied"] is False

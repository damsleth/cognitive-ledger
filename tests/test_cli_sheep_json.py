"""`sheep --json <cmd>` smoke tests.

Each sheep subcommand emits structured JSON on stdout under --json,
following the CLI contract output classes:

- status:   data    -> raw status doc, no top-level `ok`
- lint:     data    -> {issues: [...], errors, warnings, warning_summary}
- sleep:    data    -> {items: [...]}
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


def test_sheep_lint_json_structured_fields():
  result = _run("--json", "lint")
  payload = json.loads(result.stdout.strip())
  assert "issues" in payload
  assert isinstance(payload["issues"], list)
  assert "errors" in payload
  assert "warnings" in payload
  assert "warning_summary" in payload
  # Each issue dict (if any) must have level/path/message fields.
  for issue in payload["issues"]:
    assert "level" in issue
    assert "path" in issue
    assert "message" in issue


def test_sheep_sleep_json_structured_fields():
  result = _run("--json", "sleep")
  payload = json.loads(result.stdout.strip())
  assert "items" in payload
  assert isinstance(payload["items"], list)
  assert len(payload["items"]) > 0
  # Each item must have step/title/command fields.
  for item in payload["items"]:
    assert "step" in item
    assert "title" in item
    assert "command" in item


def test_sheep_sync_check_is_data_class():
  result = _run("--json", "sync", "--check")
  payload = json.loads(result.stdout.strip())
  # Data class: no top-level `ok`; carries applied=false.
  assert "ok" not in payload
  assert payload["applied"] is False


def test_sheep_sync_accept_drift_flag_is_parsed():
  from ledger.maintenance import build_parser

  args = build_parser().parse_args(["sync", "--apply", "--accept-drift"])
  assert args.apply is True
  assert args.accept_drift is True

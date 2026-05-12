"""`ledger init --json` action envelope."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(args, env=None):
  return subprocess.run(
    [sys.executable, "-m", "ledger", *args],
    capture_output=True, text=True, env=env,
  )


def _last_json_line(text: str) -> dict:
  for line in reversed((text or "").strip().splitlines()):
    try:
      return json.loads(line)
    except json.JSONDecodeError:
      continue
  raise AssertionError(f"No JSON in stdout: {text!r}")


def test_init_json_envelope_shape(tmp_path: Path):
  """init --json emits a well-formed action envelope.

  init touches a number of environment-dependent paths (templates,
  index scripts) that may or may not be reachable in CI. The envelope
  contract is what we pin here: shape, key set, ok/exit-code
  invariant.
  """
  root = tmp_path / "ledger"
  notes = tmp_path / "notes"
  notes.mkdir()
  source = tmp_path / "source"
  source.mkdir()
  result = _run([
    "init",
    "--root", str(root),
    "--ledger-notes-dir", str(notes),
    "--source-notes-dir", str(source),
    "--json",
  ])
  env = _last_json_line(result.stdout)
  assert env["tool"] == "ledger"
  assert env["command"] == "init"
  assert isinstance(env["ok"], bool)
  assert isinstance(env["stats"]["created"], list)
  assert isinstance(env["stats"]["skipped"], list)
  assert isinstance(env["stats"]["errors"], list)
  # ok/exit-code invariant: they must agree.
  if env["ok"]:
    assert result.returncode == 0
    assert env["error"] is None
  else:
    assert result.returncode != 0
    assert env["error"] is not None
    assert env["error"]["code"]


def test_init_human_default_unchanged(tmp_path: Path):
  root = tmp_path / "ledger"
  notes = tmp_path / "notes"
  notes.mkdir()
  source = tmp_path / "source"
  source.mkdir()
  result = _run([
    "init",
    "--root", str(root),
    "--ledger-notes-dir", str(notes),
    "--source-notes-dir", str(source),
  ])
  assert "Next steps" in result.stdout
  assert result.returncode == 0

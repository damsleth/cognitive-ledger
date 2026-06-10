from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ledger.cli import main as ledger_main
from ledger.importers.backends.obsidian import ObsidianBackend


def _make_vault(tmp: Path) -> Path:
  vault = tmp / "vault"
  vault.mkdir(parents=True)
  obsidian_dir = vault / ".obsidian"
  obsidian_dir.mkdir()
  (obsidian_dir / "core-plugins.json").write_text(
    json.dumps({"bases": True}), encoding="utf-8"
  )
  return vault


class TestCLIExitCodes(unittest.TestCase):
  def test_init_returns_zero_on_success(self):
    with TemporaryDirectory() as tmp:
      vault = _make_vault(Path(tmp))
      rc = ledger_main(["import", "obsidian", "init", "--vault", str(vault), "--no-auto-start"])
      self.assertEqual(rc, 0)

  def test_doctor_returns_zero_for_healthy_vault(self):
    with TemporaryDirectory() as tmp:
      vault = _make_vault(Path(tmp))
      ledger_main(["import", "obsidian", "init", "--vault", str(vault), "--no-auto-start"])
      rc = ledger_main(["import", "obsidian", "doctor", "--vault", str(vault)])
      self.assertEqual(rc, 0)

  def test_doctor_returns_nonzero_for_missing_vault(self):
    with TemporaryDirectory() as tmp:
      vault = Path(tmp) / "a" / "b" / "nonexistent"
      rc = ledger_main(["import", "obsidian", "doctor", "--vault", str(vault)])
      self.assertNotEqual(rc, 0)

  def test_queue_sync_without_root_returns_none(self):
    """queue sync with no root should return None (no root sentinel)."""
    result = ObsidianBackend(None).queue_sync()
    self.assertIsNone(result)

  def test_queue_sync_without_root_cli_exits_two(self):
    """CLI queue sync with no root should produce exit code 2."""
    with self.assertRaises(SystemExit) as ctx:
      ledger_main(["import", "obsidian", "queue", "sync"])
    self.assertEqual(ctx.exception.code, 2)

  def test_daemon_without_root_returns_two(self):
    """daemon start with no root should return exit code 2."""
    rc = ObsidianBackend(None).daemon_start()
    self.assertEqual(rc, 2)

  def test_queue_sync_without_vault_arg_exits_with_error(self):
    """Calling 'queue sync' without --vault triggers argparse error."""
    with self.assertRaises(SystemExit) as ctx:
      ledger_main(["import", "obsidian", "queue", "sync"])
    self.assertEqual(ctx.exception.code, 2)

  def test_import_dry_run_returns_zero(self):
    with TemporaryDirectory() as tmp:
      vault = _make_vault(Path(tmp))
      ledger_main(["import", "obsidian", "init", "--vault", str(vault), "--no-auto-start"])

      source = vault / "04-dev" / "workflow.md"
      source.parent.mkdir(parents=True, exist_ok=True)
      source.write_text(
        "I prefer concise responses with explicit tradeoffs.\n",
        encoding="utf-8",
      )

      rc = ledger_main(["import", "obsidian", "import", "--vault", str(vault), "--dry-run"])
      self.assertEqual(rc, 0)

  def test_bootstrap_returns_zero(self):
    with TemporaryDirectory() as tmp:
      root = Path(tmp) / "note-root"
      root.mkdir(parents=True)
      source = root / "projects" / "workflow.md"
      source.parent.mkdir(parents=True, exist_ok=True)
      source.write_text(
        "I prefer concise responses with explicit tradeoffs.\n",
        encoding="utf-8",
      )

      rc = ledger_main(["import", "obsidian", "bootstrap", "--root", str(root)])
      self.assertEqual(rc, 0)


if __name__ == "__main__":
  unittest.main()

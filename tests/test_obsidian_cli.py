from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ledger.obsidian.cli import build_parser, main as obsidian_main


def _make_vault(tmp: Path) -> Path:
  vault = tmp / "vault"
  vault.mkdir(parents=True)
  obsidian_dir = vault / ".obsidian"
  obsidian_dir.mkdir()
  (obsidian_dir / "core-plugins.json").write_text(
    json.dumps({"bases": True}), encoding="utf-8"
  )
  return vault


class TestCLISubcommandParsing(unittest.TestCase):
  def test_init_subcommand_is_recognized(self):
    parser = build_parser()
    args = parser.parse_args(["init", "--vault", "/tmp/test-vault"])
    self.assertEqual(args.command, "init")
    self.assertEqual(args.root, "/tmp/test-vault")

  def test_import_subcommand_with_dry_run(self):
    parser = build_parser()
    args = parser.parse_args(["import", "--vault", "/tmp/test-vault", "--dry-run"])
    self.assertEqual(args.command, "import")
    self.assertTrue(args.dry_run)

  def test_bootstrap_subcommand_with_root_alias(self):
    parser = build_parser()
    args = parser.parse_args(["bootstrap", "--root", "/tmp/note-root"])
    self.assertEqual(args.command, "bootstrap")
    self.assertEqual(args.root, "/tmp/note-root")

  def test_watch_subcommand_with_debounce(self):
    parser = build_parser()
    args = parser.parse_args(["watch", "--vault", "/tmp/test-vault", "--debounce-seconds", "5.0"])
    self.assertEqual(args.command, "watch")
    self.assertEqual(args.debounce_seconds, 5.0)

  def test_doctor_subcommand_is_recognized(self):
    parser = build_parser()
    args = parser.parse_args(["doctor", "--vault", "/tmp/test-vault"])
    self.assertEqual(args.command, "doctor")

  def test_daemon_start_subcommand(self):
    parser = build_parser()
    args = parser.parse_args(["daemon", "start", "--vault", "/tmp/test-vault"])
    self.assertEqual(args.command, "daemon")
    self.assertEqual(args.daemon_command, "start")

  def test_daemon_stop_subcommand(self):
    parser = build_parser()
    args = parser.parse_args(["daemon", "stop", "--vault", "/tmp/test-vault"])
    self.assertEqual(args.command, "daemon")
    self.assertEqual(args.daemon_command, "stop")

  def test_daemon_status_subcommand(self):
    parser = build_parser()
    args = parser.parse_args(["daemon", "status", "--vault", "/tmp/test-vault"])
    self.assertEqual(args.command, "daemon")
    self.assertEqual(args.daemon_command, "status")

  def test_queue_sync_subcommand(self):
    parser = build_parser()
    args = parser.parse_args(["queue", "sync", "--vault", "/tmp/test-vault"])
    self.assertEqual(args.command, "queue")
    self.assertEqual(args.queue_command, "sync")

  def test_vault_and_root_are_mutually_exclusive(self):
    parser = build_parser()
    with self.assertRaises(SystemExit) as ctx:
      parser.parse_args(["init", "--vault", "/a", "--root", "/b"])
    self.assertEqual(ctx.exception.code, 2)

  def test_import_max_files_and_max_notes(self):
    parser = build_parser()
    args = parser.parse_args([
      "import", "--vault", "/tmp/v", "--max-files", "10", "--max-notes", "20"
    ])
    self.assertEqual(args.max_files, 10)
    self.assertEqual(args.max_notes, 20)

  def test_no_subcommand_returns_zero(self):
    """Calling main with no arguments prints help and returns 0."""
    rc = obsidian_main([])
    self.assertEqual(rc, 0)


class TestCLIExitCodes(unittest.TestCase):
  def test_init_returns_zero_on_success(self):
    with TemporaryDirectory() as tmp:
      vault = _make_vault(Path(tmp))
      rc = obsidian_main(["init", "--vault", str(vault), "--no-auto-start"])
      self.assertEqual(rc, 0)

  def test_doctor_returns_zero_for_healthy_vault(self):
    with TemporaryDirectory() as tmp:
      vault = _make_vault(Path(tmp))
      obsidian_main(["init", "--vault", str(vault), "--no-auto-start"])
      rc = obsidian_main(["doctor", "--vault", str(vault)])
      self.assertEqual(rc, 0)

  def test_doctor_returns_nonzero_for_missing_vault(self):
    with TemporaryDirectory() as tmp:
      # Point at a path that truly does not exist
      vault = Path(tmp) / "a" / "b" / "nonexistent"
      rc = obsidian_main(["doctor", "--vault", str(vault)])
      self.assertEqual(rc, 1)

  def test_queue_sync_without_root_returns_two(self):
    """queue sync with no --vault or --root should return exit code 2."""
    import argparse
    from ledger.obsidian.cli import cmd_queue_sync
    args = argparse.Namespace(root=None, queue_command="sync")
    rc = cmd_queue_sync(args)
    self.assertEqual(rc, 2)

  def test_daemon_without_root_returns_two(self):
    """daemon start with no --vault should return exit code 2."""
    import argparse
    from ledger.obsidian.cli import cmd_daemon
    args = argparse.Namespace(root=None, daemon_command="start")
    rc = cmd_daemon(args)
    self.assertEqual(rc, 2)

  def test_queue_sync_without_vault_arg_exits_with_error(self):
    """Calling 'queue sync' without --vault triggers argparse error."""
    parser = build_parser()
    with self.assertRaises(SystemExit) as ctx:
      parser.parse_args(["queue", "sync"])
    self.assertEqual(ctx.exception.code, 2)

  def test_import_dry_run_returns_zero(self):
    with TemporaryDirectory() as tmp:
      vault = _make_vault(Path(tmp))
      obsidian_main(["init", "--vault", str(vault), "--no-auto-start"])

      source = vault / "04-dev" / "workflow.md"
      source.parent.mkdir(parents=True, exist_ok=True)
      source.write_text(
        "I prefer concise responses with explicit tradeoffs.\n",
        encoding="utf-8",
      )

      rc = obsidian_main(["import", "--vault", str(vault), "--dry-run"])
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

      rc = obsidian_main(["bootstrap", "--root", str(root)])
      self.assertEqual(rc, 0)


if __name__ == "__main__":
  unittest.main()

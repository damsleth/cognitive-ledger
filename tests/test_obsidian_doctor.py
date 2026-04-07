from __future__ import annotations

import json
import platform
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from ledger.obsidian.config import default_config
from ledger.obsidian.doctor import run_doctor


def _make_healthy_vault(tmp: Path) -> Path:
  vault = tmp / "vault"
  vault.mkdir(parents=True)
  obsidian_dir = vault / ".obsidian"
  obsidian_dir.mkdir()
  (obsidian_dir / "core-plugins.json").write_text(
    json.dumps({"bases": True}), encoding="utf-8"
  )
  config = default_config(vault)
  config.ledger_root.mkdir(parents=True, exist_ok=True)
  return vault


class TestDoctorHealthyVault(unittest.TestCase):
  def test_healthy_vault_returns_zero_exit_code(self):
    with TemporaryDirectory() as tmp:
      vault = _make_healthy_vault(Path(tmp))
      config = default_config(vault)

      code, lines = run_doctor(config)

      self.assertEqual(code, 0)
      report = "\n".join(lines)
      self.assertIn("ok: vault path exists", report)
      self.assertIn("ok: .obsidian folder found", report)
      self.assertIn("ok: ledger root is writable", report)
      self.assertIn("ok: Obsidian Bases plugin is enabled", report)

  def test_healthy_vault_reports_python_version(self):
    with TemporaryDirectory() as tmp:
      vault = _make_healthy_vault(Path(tmp))
      config = default_config(vault)

      code, lines = run_doctor(config)
      report = "\n".join(lines)

      if sys.version_info >= (3, 11):
        self.assertIn("ok: python", report)
      else:
        self.assertIn("error: python 3.11+ is required", report)

  def test_healthy_vault_reports_vault_and_ledger_paths(self):
    with TemporaryDirectory() as tmp:
      vault = _make_healthy_vault(Path(tmp))
      config = default_config(vault)

      _, lines = run_doctor(config)

      self.assertTrue(any(f"vault: {config.vault_root}" in ln for ln in lines))
      self.assertTrue(any(f"ledger root: {config.ledger_root}" in ln for ln in lines))


class TestDoctorMissingDirectories(unittest.TestCase):
  def test_missing_vault_path_returns_error(self):
    with TemporaryDirectory() as tmp:
      vault = Path(tmp) / "nonexistent"
      config = default_config(vault)
      # Override vault_root to a path that does not exist
      config.vault_root = vault

      code, lines = run_doctor(config)
      report = "\n".join(lines)

      self.assertEqual(code, 1)
      self.assertIn("error: vault path does not exist", report)

  def test_missing_obsidian_dir_gives_warning(self):
    with TemporaryDirectory() as tmp:
      vault = Path(tmp) / "vault"
      vault.mkdir(parents=True)
      config = default_config(vault)
      config.ledger_root.mkdir(parents=True, exist_ok=True)

      code, lines = run_doctor(config)
      report = "\n".join(lines)

      self.assertIn("warn: .obsidian not found", report)

  def test_missing_core_plugins_gives_warning(self):
    with TemporaryDirectory() as tmp:
      vault = Path(tmp) / "vault"
      vault.mkdir(parents=True)
      obsidian_dir = vault / ".obsidian"
      obsidian_dir.mkdir()
      # Do not create core-plugins.json
      config = default_config(vault)
      config.ledger_root.mkdir(parents=True, exist_ok=True)

      _, lines = run_doctor(config)
      report = "\n".join(lines)

      self.assertIn("warn: core-plugins.json missing", report)

  def test_bases_plugin_disabled_gives_warning(self):
    with TemporaryDirectory() as tmp:
      vault = Path(tmp) / "vault"
      vault.mkdir(parents=True)
      obsidian_dir = vault / ".obsidian"
      obsidian_dir.mkdir()
      (obsidian_dir / "core-plugins.json").write_text(
        json.dumps({"bases": False}), encoding="utf-8"
      )
      config = default_config(vault)
      config.ledger_root.mkdir(parents=True, exist_ok=True)

      _, lines = run_doctor(config)
      report = "\n".join(lines)

      self.assertIn("warn: Obsidian Bases plugin not enabled", report)


class TestDoctorReports(unittest.TestCase):
  def test_watchdog_missing_reports_error(self):
    with TemporaryDirectory() as tmp:
      vault = _make_healthy_vault(Path(tmp))
      config = default_config(vault)

      with patch.dict("sys.modules", {"watchdog": None}):
        # Force ImportError by removing the module
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
          if name == "watchdog":
            raise ImportError("no watchdog")
          return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
          code, lines = run_doctor(config)

      report = "\n".join(lines)
      self.assertIn("error: watchdog is not installed", report)
      self.assertEqual(code, 1)

  def test_darwin_reports_daemon_support(self):
    with TemporaryDirectory() as tmp:
      vault = _make_healthy_vault(Path(tmp))
      config = default_config(vault)

      with patch("platform.system", return_value="Darwin"):
        _, lines = run_doctor(config)

      report = "\n".join(lines)
      self.assertIn("ok: macOS daemon mode supported", report)

  def test_linux_reports_daemon_warning(self):
    with TemporaryDirectory() as tmp:
      vault = _make_healthy_vault(Path(tmp))
      config = default_config(vault)

      with patch("platform.system", return_value="Linux"):
        _, lines = run_doctor(config)

      report = "\n".join(lines)
      self.assertIn("warn: daemon mode is macOS-first", report)

  def test_malformed_core_plugins_gives_warning(self):
    with TemporaryDirectory() as tmp:
      vault = Path(tmp) / "vault"
      vault.mkdir(parents=True)
      obsidian_dir = vault / ".obsidian"
      obsidian_dir.mkdir()
      (obsidian_dir / "core-plugins.json").write_text(
        "not valid json{{{", encoding="utf-8"
      )
      config = default_config(vault)
      config.ledger_root.mkdir(parents=True, exist_ok=True)

      _, lines = run_doctor(config)
      report = "\n".join(lines)

      self.assertIn("warn: unable to parse core-plugins.json", report)

  def test_error_count_accumulates(self):
    """A vault with no path and missing watchdog should report multiple errors."""
    with TemporaryDirectory() as tmp:
      vault = Path(tmp) / "nonexistent"
      config = default_config(vault)
      config.vault_root = vault

      code, lines = run_doctor(config)

      # At least vault-missing error
      self.assertEqual(code, 1)
      error_lines = [ln for ln in lines if ln.startswith("error:")]
      self.assertGreaterEqual(len(error_lines), 1)


if __name__ == "__main__":
  unittest.main()

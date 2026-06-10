import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.init import init_ledger


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_ledger_module():
    import ledger.cli as module
    return module


class InitTests(unittest.TestCase):
    def setUp(self):
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")

    def tearDown(self):
        reset_config()
        if self._orig_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg

    def test_init_persists_external_paths_to_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            repo_root = tmp / "repo"
            ledger_notes_dir = tmp / "ledger-notes"
            source_notes_dir = tmp / "notes"
            repo_root.mkdir()
            # Config is written to the canonical XDG location, not into --root.
            os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")

            report = init_ledger(
                root=repo_root,
                ledger_notes_dir=ledger_notes_dir,
                source_notes_dir=source_notes_dir,
            )

            config_path = tmp / "xdg" / "ledger" / "config.yaml"
            self.assertTrue(config_path.is_file())
            # Config does NOT land inside the repo/--root.
            self.assertFalse((repo_root / "config.yaml").exists())
            config_text = config_path.read_text(encoding="utf-8")
            self.assertIn("first_run: true", config_text)
            self.assertIn(f"ledger_notes_dir: {ledger_notes_dir.resolve()}", config_text)
            self.assertIn(f"source_notes_dir: {source_notes_dir.resolve()}", config_text)

            config = LedgerConfig.from_file(config_path)
            self.assertEqual(config.ledger_notes_dir, ledger_notes_dir.resolve())
            self.assertEqual(config.source_notes_dir, source_notes_dir.resolve())

            self.assertFalse((repo_root / "notes").exists())
            self.assertTrue((ledger_notes_dir / "08_indices" / "timeline.md").is_file())
            gitignore = (ledger_notes_dir / ".gitignore").read_text(encoding="utf-8")
            self.assertIn("*.lock", gitignore)
            self.assertIn("08_indices/note_index.json", gitignore)
            self.assertIn("08_indices/.session_baseline", gitignore)
            self.assertIn(str(config_path), report["created"])

    def test_paths_command_uses_resolved_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            config = LedgerConfig(
                ledger_root=tmp / "repo",
                ledger_notes_dir=tmp / "ledger-notes",
                source_notes_dir=tmp / "notes",
            )
            set_config(config)
            module = load_ledger_module()

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                module.handle_paths_command(SimpleNamespace(field="ledger_notes_dir", json=False))

            self.assertEqual(stdout.getvalue().strip(), str(config.ledger_notes_dir))


    def test_init_demo_creates_sample_notes(self):
        """--demo flag writes 5 sample notes into the ledger."""
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            repo_root = tmp / "repo"
            ledger_notes_dir = tmp / "ledger-notes"
            source_notes_dir = tmp / "notes"
            repo_root.mkdir()
            os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")

            report = init_ledger(
                root=repo_root,
                ledger_notes_dir=ledger_notes_dir,
                source_notes_dir=source_notes_dir,
                demo=True,
            )

            demo_created = [i for i in report["created"] if i.startswith("demo: ")]
            self.assertEqual(len(demo_created), 5)
            # All 5 demo files should exist on disk.
            from ledger.init import DEMO_NOTES
            for rel_path, _ in DEMO_NOTES:
                self.assertTrue((ledger_notes_dir / rel_path).is_file(), rel_path)

    def test_init_demo_false_creates_no_demo_notes(self):
        """Without --demo, no demo notes are written."""
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            repo_root = tmp / "repo"
            ledger_notes_dir = tmp / "ledger-notes"
            source_notes_dir = tmp / "notes"
            repo_root.mkdir()
            os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")

            report = init_ledger(
                root=repo_root,
                ledger_notes_dir=ledger_notes_dir,
                source_notes_dir=source_notes_dir,
                demo=False,
            )

            demo_items = [i for i in report["created"] + report["skipped"] if "demo" in i]
            self.assertEqual(demo_items, [])

    def test_init_demo_idempotent(self):
        """Running init with --demo twice skips already-existing demo notes."""
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            repo_root = tmp / "repo"
            ledger_notes_dir = tmp / "ledger-notes"
            source_notes_dir = tmp / "notes"
            repo_root.mkdir()
            os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")

            # First run creates them.
            report1 = init_ledger(
                root=repo_root,
                ledger_notes_dir=ledger_notes_dir,
                source_notes_dir=source_notes_dir,
                demo=True,
            )
            created1 = [i for i in report1["created"] if i.startswith("demo: ")]
            self.assertEqual(len(created1), 5)

            # Second run skips them.
            report2 = init_ledger(
                root=repo_root,
                ledger_notes_dir=ledger_notes_dir,
                source_notes_dir=source_notes_dir,
                demo=True,
            )
            skipped2 = [i for i in report2["skipped"] if i.startswith("demo: ")]
            created2 = [i for i in report2["created"] if i.startswith("demo: ")]
            self.assertEqual(len(skipped2), 5)
            self.assertEqual(created2, [])


if __name__ == "__main__":
    unittest.main()

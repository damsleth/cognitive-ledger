import contextlib
import importlib.machinery
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.init import init_ledger


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "scripts" / "ledger"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_ledger_module():
    loader = importlib.machinery.SourceFileLoader("ledger_paths_module", str(LEDGER_PATH))
    spec = importlib.util.spec_from_file_location("ledger_paths_module", str(LEDGER_PATH), loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InitTests(unittest.TestCase):
    def tearDown(self):
        reset_config()

    def test_init_persists_external_paths_to_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            repo_root = tmp / "repo"
            ledger_notes_dir = tmp / "ledger-notes"
            source_notes_dir = tmp / "notes"
            repo_root.mkdir()

            report = init_ledger(
                root=repo_root,
                ledger_notes_dir=ledger_notes_dir,
                source_notes_dir=source_notes_dir,
            )

            config_path = repo_root / "config.yaml"
            self.assertTrue(config_path.is_file())
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
            self.assertIn("config.yaml", report["created"])

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


if __name__ == "__main__":
    unittest.main()

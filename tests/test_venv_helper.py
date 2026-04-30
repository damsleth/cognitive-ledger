"""Coverage for ledger/venv.py - the venv-reexec helper.

We test the early-return paths (the only ones we can reach without
actually exec'ing into a different process).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class VenvReexecTests(unittest.TestCase):
    def test_returns_when_already_reexeced(self):
        from ledger.venv import maybe_reexec_in_repo_venv
        with patch.dict(os.environ, {"COG_LEDGER_VENV_REEXEC": "1"}, clear=False):
            # Should return without raising; we don't actually exec
            self.assertIsNone(maybe_reexec_in_repo_venv(Path("/nonexistent")))

    def test_returns_when_no_venv_exists(self):
        from ledger.venv import maybe_reexec_in_repo_venv
        env = {k: v for k, v in os.environ.items() if k != "COG_LEDGER_VENV_REEXEC"}
        with patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                # tmp has no .venv inside, so the function should return early
                self.assertIsNone(maybe_reexec_in_repo_venv(Path(tmp)))

    def test_returns_when_already_in_target_venv(self):
        from ledger.venv import maybe_reexec_in_repo_venv
        env = {k: v for k, v in os.environ.items() if k != "COG_LEDGER_VENV_REEXEC"}
        with patch.dict(os.environ, env, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                # Set up a fake .venv that contains a python file
                venv_dir = Path(tmp) / ".venv"
                (venv_dir / "bin").mkdir(parents=True)
                py = venv_dir / "bin" / "python"
                py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                py.chmod(0o755)

                # Make sys.prefix look like the target venv
                with patch.object(sys, "prefix", str(venv_dir)):
                    self.assertIsNone(maybe_reexec_in_repo_venv(Path(tmp)))


if __name__ == "__main__":
    unittest.main()

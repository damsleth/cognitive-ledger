import json
import subprocess
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_context_profiles.py"


class ContextProfileTests(unittest.TestCase):
    def test_generates_scope_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "indices"
            cmd = [
                "python3",
                str(SCRIPT),
                "--notes-dir",
                str(ROOT / "notes"),
                "--output-dir",
                str(out_dir),
            ]
            subprocess.check_call(cmd, cwd=str(ROOT))

            for scope in ("personal", "work", "dev"):
                md = out_dir / f"context_profile_{scope}.md"
                js = out_dir / f"context_profile_{scope}.json"
                self.assertTrue(md.exists(), msg=f"Missing {md}")
                self.assertTrue(js.exists(), msg=f"Missing {js}")
                payload = json.loads(js.read_text(encoding="utf-8"))
                self.assertEqual(payload["scope"], scope)
                self.assertIn("facts", payload)
                self.assertIn("preferences", payload)
                self.assertIn("active_loops", payload)


if __name__ == "__main__":
    unittest.main()

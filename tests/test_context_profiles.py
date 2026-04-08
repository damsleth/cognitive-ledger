import json
import subprocess
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_context_profiles.py"


def _build_profile_fixture_tree(root: Path) -> Path:
    """Create a temp notes tree with fixture notes for context profile tests.

    Returns the notes directory path inside root.
    """
    notes = root / "notes"
    for folder in (
        "01_identity", "02_facts", "03_preferences",
        "04_goals", "05_open_loops", "06_concepts", "08_indices",
    ):
        (notes / folder).mkdir(parents=True, exist_ok=True)

    # Fact note (scope: work)
    (notes / "02_facts" / "fact__team_standup.md").write_text(
        "---\n"
        "created: 2026-01-10T00:00:00Z\n"
        "updated: 2026-03-15T00:00:00Z\n"
        "tags: [meetings, team]\n"
        "confidence: 0.95\n"
        "source: user\n"
        "scope: work\n"
        "lang: en\n"
        "---\n\n"
        "# Team Standup\n\n"
        "## Statement\n\n"
        "Daily standup at 09:00 CET.\n",
        encoding="utf-8",
    )

    # Preference note (scope: dev)
    (notes / "03_preferences" / "pref__editor_settings.md").write_text(
        "---\n"
        "created: 2026-01-05T00:00:00Z\n"
        "updated: 2026-02-20T00:00:00Z\n"
        "tags: [editor, tooling]\n"
        "confidence: 0.9\n"
        "source: user\n"
        "scope: dev\n"
        "lang: en\n"
        "---\n\n"
        "# Editor Settings\n\n"
        "## Statement\n\n"
        "Use 2-space indentation, no tabs, Prettier as formatter.\n",
        encoding="utf-8",
    )

    # Open loop (scope: personal)
    (notes / "05_open_loops" / "loop__plan_vacation.md").write_text(
        "---\n"
        "created: 2026-02-15T00:00:00Z\n"
        "updated: 2026-03-10T00:00:00Z\n"
        "tags: [vacation, planning]\n"
        "confidence: 0.8\n"
        "source: user\n"
        "scope: personal\n"
        "lang: en\n"
        "status: open\n"
        "---\n\n"
        "# Loop: Plan vacation\n\n"
        "## Question or Task\n\n"
        "Decide on summer vacation destination.\n\n"
        "## Why it matters\n\n"
        "Need to book flights before prices go up.\n\n"
        "## Next Action\n\n"
        "- [ ] Compare flight prices\n",
        encoding="utf-8",
    )

    return notes


class ContextProfileTests(unittest.TestCase):
    def test_generates_scope_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes_root = Path(tmp) / "fixture"
            notes_dir = _build_profile_fixture_tree(notes_root)
            out_dir = Path(tmp) / "indices"
            cmd = [
                "python3",
                str(SCRIPT),
                "--ledger-notes-dir",
                str(notes_dir),
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

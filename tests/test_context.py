import json
import tempfile
import unittest
from pathlib import Path

from ledger.context import (
    SCOPES,
    build_context,
    collect_profile_items,
    render_profile,
    write_context,
    write_context_profiles,
)


def _write_note(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class ContextModuleTests(unittest.TestCase):
    def test_build_context_uses_notes_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            notes_dir = Path(tmp) / "notes"
            _write_note(
                notes_dir / "02_facts" / "fact__one.md",
                """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: personal
lang: en
---

# One Fact

## Statement

One durable fact.
""",
            )

            output = build_context(notes_dir)
            self.assertIn("Ledger Context", output)
            self.assertIn("fact__one.md", output)

    def test_render_profile_and_writers_emit_expected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            notes_dir = root / "notes"
            output_dir = root / "indices"
            _write_note(
                notes_dir / "03_preferences" / "pref__one.md",
                """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: personal
lang: en
---

# One Preference

## Statement

Prefer explicit tradeoffs.
""",
            )
            _write_note(
                notes_dir / "05_open_loops" / "loop__one.md",
                """---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
status: open
---

# Loop: One Loop

## Question or Task

What should we ship?

## Next Action

- [ ] Write release notes
""",
            )

            items = collect_profile_items(notes_dir)
            self.assertTrue(items)

            markdown, payload = render_profile("personal", items)
            self.assertIn("Context Profile", markdown)
            self.assertEqual(payload["scope"], "personal")

            write_context(output_dir / "context.md", notes_dir)
            write_context_profiles(output_dir, notes_dir)

            self.assertTrue((output_dir / "context.md").exists())
            for scope in SCOPES:
                profile_path = output_dir / f"context_profile_{scope}.json"
                self.assertTrue(profile_path.exists())
                data = json.loads(profile_path.read_text(encoding="utf-8"))
                self.assertEqual(data["scope"], scope)


if __name__ == "__main__":
    unittest.main()

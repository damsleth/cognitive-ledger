"""Regression tests for QA hardening fixes (QA_TODO.md).

These tests cover specific failure modes discovered during QA review:
- Notes containing `---` in body (horizontal rules)
- Repeated section headers preserved
- Config file applied + env overrides
- Timeline append consistency
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger.config import LedgerConfig, get_config, reset_config, set_config
from ledger.parsing import parse_frontmatter_text, parse_sections
from ledger.io import safe_append_line, append_timeline_entry, FileLock


class TestHorizontalRuleInBody(unittest.TestCase):
    """Test that horizontal rules (---) in body don't break frontmatter parsing."""

    def test_horizontal_rule_not_treated_as_frontmatter_delimiter(self):
        """Horizontal rules in body should be preserved, not split."""
        note_content = """---
created: 2026-02-13T10:00:00Z
tags: [test]
---

# Title

Some content here.

---

More content after horizontal rule.

---

Yet more content.
"""
        frontmatter, body = parse_frontmatter_text(note_content)

        # Frontmatter should be parsed correctly
        self.assertEqual(frontmatter["created"], "2026-02-13T10:00:00Z")
        self.assertEqual(frontmatter["tags"], ["test"])

        # Body should contain the horizontal rules
        self.assertIn("---", body)
        self.assertIn("More content after horizontal rule", body)
        self.assertIn("Yet more content", body)

    def test_multiple_horizontal_rules_preserved(self):
        """Multiple --- lines in body should all be preserved."""
        note_content = """---
created: 2026-02-13T10:00:00Z
---

Section 1

---

Section 2

---

Section 3
"""
        _, body = parse_frontmatter_text(note_content)

        # Count horizontal rules in body
        hr_count = body.count("\n---\n")
        self.assertEqual(hr_count, 2)


class TestRepeatedSectionHeaders(unittest.TestCase):
    """Test that repeated section headers append rather than overwrite."""

    def test_repeated_h2_headers_concatenate(self):
        """Repeated ## headers should append content."""
        body = """
## Links

First link content

## Context

Some context

## Links

Second link content
"""
        sections = parse_sections(body)

        # Links section should contain both blocks
        links_content = "\n".join(sections.get("links", []))
        self.assertIn("First link content", links_content)
        self.assertIn("Second link content", links_content)

    def test_repeated_h3_headers_concatenate(self):
        """Repeated ### headers should append content."""
        body = """
### Note

First note

### Note

Second note
"""
        sections = parse_sections(body)

        note_content = "\n".join(sections.get("note", []))
        self.assertIn("First note", note_content)
        self.assertIn("Second note", note_content)


class TestConfigFileWithEnvOverrides(unittest.TestCase):
    """Test that config file values are applied and env vars override them."""

    def setUp(self):
        reset_config()

    def tearDown(self):
        reset_config()
        # Clean up env vars
        for key in ["LEDGER_SHORTLIST_MIN", "LEDGER_SHORTLIST_MAX"]:
            if key in os.environ:
                del os.environ[key]

    def test_config_from_file_applies_values(self):
        """Config values from JSON file should be applied."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write('{"shortlist_min_candidates": 50}')
            config_path = Path(f.name)

        try:
            config = LedgerConfig.from_file(config_path)
            self.assertEqual(config.shortlist_min_candidates, 50)
        finally:
            config_path.unlink()

    def test_env_overrides_file_config(self):
        """Environment variables should override file config values."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write('{"shortlist_min_candidates": 50}')
            config_path = Path(f.name)

        try:
            os.environ["LEDGER_SHORTLIST_MIN"] = "100"
            config = LedgerConfig.from_file(config_path)
            # Env var should override file value
            self.assertEqual(config.shortlist_min_candidates, 100)
        finally:
            config_path.unlink()
            del os.environ["LEDGER_SHORTLIST_MIN"]

    def test_missing_config_file_uses_defaults(self):
        """Missing config file should use defaults without error."""
        config = LedgerConfig.from_file(Path("/nonexistent/path/config.json"))
        # Should have default value
        self.assertEqual(config.shortlist_min_candidates, 24)


class TestTimelineAppendConsistency(unittest.TestCase):
    """Test that timeline appends use locking consistently."""

    def test_safe_append_line_adds_newline(self):
        """safe_append_line should add newline if missing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("existing content\n")
            path = Path(f.name)

        try:
            safe_append_line(path, "new line without newline")
            content = path.read_text()
            self.assertTrue(content.endswith("new line without newline\n"))
        finally:
            path.unlink()
            path.with_suffix(".md.lock").unlink(missing_ok=True)

    def test_safe_append_line_preserves_existing_newline(self):
        """safe_append_line should not double newlines."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("existing\n")
            path = Path(f.name)

        try:
            safe_append_line(path, "with newline\n")
            content = path.read_text()
            # Should not have double newlines
            self.assertNotIn("\n\n", content.replace("existing\n", ""))
        finally:
            path.unlink()
            path.with_suffix(".md.lock").unlink(missing_ok=True)

    def test_append_timeline_entry_formats_correctly(self):
        """append_timeline_entry should format entry correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            timeline = root / "notes" / "08_indices" / "timeline.md"
            timeline_jsonl = root / "notes" / "08_indices" / "timeline.jsonl"

            append_timeline_entry(
                timeline_path=timeline,
                action="created",
                note_path=root / "notes" / "02_facts" / "fact__test.md",
                description="test note",
                root_dir=root,
            )

            content = timeline.read_text()
            self.assertIn("created", content)
            self.assertIn("notes/02_facts/fact__test.md", content)
            self.assertIn("test note", content)
            # Check ISO timestamp format
            self.assertRegex(content, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

            jsonl_content = timeline_jsonl.read_text()
            self.assertIn('"action": "created"', jsonl_content)
            self.assertIn('"path": "notes/02_facts/fact__test.md"', jsonl_content)

    def test_concurrent_timeline_appends_no_corruption(self):
        """Concurrent appends should not corrupt timeline (best-effort test)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            timeline = Path(tmpdir) / "timeline.md"
            timeline.write_text("")

            errors = []
            lines_written = []

            def append_line(thread_id, count):
                try:
                    for i in range(count):
                        line = f"thread-{thread_id}-line-{i}"
                        safe_append_line(timeline, line)
                        lines_written.append(line)
                except Exception as e:
                    errors.append(e)

            threads = [
                threading.Thread(target=append_line, args=(i, 10))
                for i in range(5)
            ]

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [])

            content = timeline.read_text()
            lines = [l for l in content.strip().split("\n") if l]

            # All lines should be present (though order may vary)
            self.assertEqual(len(lines), 50)


class TestFsyncDurability(unittest.TestCase):
    """Test that writes include fsync for durability."""

    def test_atomic_write_includes_fsync(self):
        """Atomic write should call fsync for crash durability."""
        from ledger.io.safe_write import atomic_write

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.md"

            # Patch os.fsync to verify it's called
            fsync_calls = []
            original_fsync = os.fsync

            def mock_fsync(fd):
                fsync_calls.append(fd)
                return original_fsync(fd)

            with patch("os.fsync", side_effect=mock_fsync):
                with atomic_write(path) as f:
                    f.write("test content")

            # fsync should be called at least once (for temp file)
            # and once for directory
            self.assertGreaterEqual(len(fsync_calls), 1)


if __name__ == "__main__":
    unittest.main()

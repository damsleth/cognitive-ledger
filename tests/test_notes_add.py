"""Coverage for ledger/notes/add.py and the `ledger notes add` CLI verb."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.notes.add import (
    AddNoteError,
    add_note,
    derive_slug,
    normalize_type,
)


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _scaffold_notes(tmp: Path) -> Path:
    notes_dir = tmp / "notes"
    for folder in (
        "00_inbox", "01_identity", "02_facts", "03_preferences",
        "04_goals", "05_open_loops", "06_concepts", "07_projects",
        "08_indices", "09_archive",
    ):
        (notes_dir / folder).mkdir(parents=True)
    (notes_dir / "08_indices" / "timeline.md").write_text("# Timeline\n", encoding="utf-8")
    return notes_dir


def _set_temp_config(tmp_root: Path) -> LedgerConfig:
    notes_dir = _scaffold_notes(tmp_root)
    cfg = LedgerConfig(
        ledger_root=tmp_root,
        ledger_notes_dir=notes_dir,
        source_notes_dir=tmp_root / "source",
    )
    set_config(cfg)
    return cfg


class NormalizeTypeTests(unittest.TestCase):
    def test_canonical_passes_through(self):
        self.assertEqual(normalize_type("facts"), "facts")
        self.assertEqual(normalize_type("preferences"), "preferences")
        self.assertEqual(normalize_type("identity"), "identity")

    def test_singular_aliases(self):
        self.assertEqual(normalize_type("fact"), "facts")
        self.assertEqual(normalize_type("pref"), "preferences")
        self.assertEqual(normalize_type("preference"), "preferences")
        self.assertEqual(normalize_type("loop"), "loops")
        self.assertEqual(normalize_type("id"), "identity")

    def test_case_insensitive(self):
        self.assertEqual(normalize_type("FACT"), "facts")
        self.assertEqual(normalize_type("Preference"), "preferences")

    def test_unknown_raises(self):
        with self.assertRaises(AddNoteError) as ctx:
            normalize_type("not-a-type")
        self.assertIn("choose from", str(ctx.exception))


class DeriveSlugTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(derive_slug("Hello World"), "hello_world")

    def test_punctuation_collapses(self):
        self.assertEqual(derive_slug("Foo, bar! Baz?"), "foo_bar_baz")

    def test_trims_underscores(self):
        self.assertEqual(derive_slug("__padding__"), "padding")

    def test_empty_falls_back(self):
        self.assertEqual(derive_slug(""), "untitled")
        self.assertEqual(derive_slug("!!!"), "untitled")

    def test_truncates(self):
        long = "x" * 200
        out = derive_slug(long)
        self.assertLessEqual(len(out), 60)


class AddNoteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_default_lands_in_inbox(self):
        result = add_note(body="This is a thing I want to remember.", note_type="fact")
        self.assertTrue(result.inbox)
        self.assertEqual(result.note_type, "facts")
        self.assertTrue(result.path.exists())
        # Lives in 00_inbox/, prefixed with fact__
        self.assertEqual(result.path.parent.name, "00_inbox")
        self.assertTrue(result.path.name.startswith("fact__"))

    def test_no_inbox_writes_to_typed_folder(self):
        result = add_note(
            body="I prefer terse responses.",
            note_type="preferences",
            inbox=False,
        )
        self.assertFalse(result.inbox)
        self.assertEqual(result.path.parent.name, "03_preferences")
        self.assertTrue(result.path.name.startswith("pref__"))

    def test_frontmatter_shape(self):
        now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)
        result = add_note(
            body="Concrete content here.",
            note_type="fact",
            tags=["axiom", "suite"],
            source="user",
            scope="work",
            lang="en",
            confidence=0.95,
            now=now,
        )
        text = result.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("created: 2026-05-28T12:00:00Z", text)
        self.assertIn("updated: 2026-05-28T12:00:00Z", text)
        self.assertIn("tags: [axiom, suite]", text)
        self.assertIn("confidence: 0.95", text)
        self.assertIn("source: user", text)
        self.assertIn("scope: work", text)
        self.assertIn("lang: en", text)

    def test_body_gets_h1_prepended_if_missing(self):
        result = add_note(body="A statement without a heading.", note_type="fact")
        text = result.path.read_text(encoding="utf-8")
        self.assertIn("# A statement without a heading.", text)

    def test_body_with_existing_h1_is_preserved(self):
        result = add_note(
            body="# Custom Heading\n\nBody text.",
            note_type="fact",
        )
        text = result.path.read_text(encoding="utf-8")
        # Don't double-prepend.
        self.assertEqual(text.count("# Custom Heading"), 1)
        self.assertIn("Body text.", text)

    def test_explicit_title_overrides_derivation(self):
        result = add_note(
            body="Plain content.",
            note_type="fact",
            title="A Specific Title",
        )
        text = result.path.read_text(encoding="utf-8")
        self.assertIn("# A Specific Title", text)

    def test_explicit_slug_overrides_derivation(self):
        result = add_note(
            body="Anything in the body.",
            note_type="fact",
            slug="my_chosen_slug",
        )
        self.assertEqual(result.path.name, "fact__my_chosen_slug.md")

    def test_links_section_appended(self):
        result = add_note(
            body="Body.",
            note_type="fact",
            links=["[[other-note]]", "https://example.com"],
        )
        text = result.path.read_text(encoding="utf-8")
        self.assertIn("## Links", text)
        self.assertIn("[[other-note]]", text)
        self.assertIn("https://example.com", text)

    def test_collision_counter(self):
        first = add_note(body="Same.", note_type="fact", slug="dup")
        second = add_note(body="Same.", note_type="fact", slug="dup")
        self.assertEqual(first.path.name, "fact__dup.md")
        self.assertEqual(second.path.name, "fact__dup_1.md")

    def test_timeline_entry_appended(self):
        add_note(body="A captured thought.", note_type="fact", slug="captured")
        timeline_jsonl = self.cfg.timeline_path.with_name("timeline.jsonl")
        self.assertTrue(timeline_jsonl.exists())
        lines = timeline_jsonl.read_text(encoding="utf-8").strip().splitlines()
        self.assertTrue(any("fact__captured.md" in line for line in lines))

    def test_empty_body_rejected(self):
        with self.assertRaises(AddNoteError):
            add_note(body="   ", note_type="fact")

    def test_unknown_type_rejected(self):
        with self.assertRaises(AddNoteError):
            add_note(body="x", note_type="bogus")

    def test_all_canonical_types_work(self):
        for canonical, prefix in [
            ("facts", "fact__"),
            ("preferences", "pref__"),
            ("goals", "goal__"),
            ("loops", "loop__"),
            ("concepts", "concept__"),
            ("identity", "id__"),
        ]:
            result = add_note(
                body=f"Body for {canonical}.",
                note_type=canonical,
                inbox=False,
                slug=f"sample_{canonical}",
            )
            self.assertEqual(result.note_type, canonical)
            self.assertTrue(result.path.name.startswith(prefix), f"{canonical} got {result.path.name}")


class CLIIntegrationTests(unittest.TestCase):
    """Drive the actual `ledger` console script, mirroring how hugr remember shells out."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        _scaffold_notes(self.tmp_root)
        self._env = {
            "LEDGER_ROOT": str(self.tmp_root),
            "LEDGER_NOTES_DIR": str(self.tmp_root / "notes"),
            "LEDGER_SOURCE_DIR": str(self.tmp_root / "source"),
            "PATH": __import__("os").environ.get("PATH", ""),
            "HOME": str(self.tmp_root),
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _ledger(self, *args: str) -> subprocess.CompletedProcess:
        # Drive via a fresh python -c rather than `python -m ledger.cli`
        # (the runpy double-import warning swallows stdout in some envs).
        code = (
            "import sys; "
            "from ledger.cli import main; "
            "sys.exit(main() or 0)"
        )
        cmd = [sys.executable, "-c", code, *args]
        return subprocess.run(
            cmd, capture_output=True, text=True, env=self._env, cwd=str(ROOT),
        )

    def test_add_json_envelope(self):
        proc = self._ledger(
            "notes", "add",
            "--type", "fact",
            "--no-inbox",
            "--slug", "envelope_test",
            "--json",
            "A test fact captured via CLI.",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["tool"], "ledger")
        self.assertEqual(payload["command"], "notes add")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["type"], "facts")
        self.assertFalse(payload["inbox"])
        self.assertEqual(payload["slug"], "envelope_test")

    def test_add_default_goes_to_inbox(self):
        proc = self._ledger(
            "notes", "add",
            "--type", "preference",
            "--slug", "inbox_default",
            "--json",
            "A thing.",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["inbox"])
        self.assertIn("00_inbox", payload["path"])

    def test_legacy_list_still_works(self):
        # No subcommand, --type required, should not error on the new wiring.
        proc = self._ledger("notes", "--type", "facts", "--limit", "1")
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()

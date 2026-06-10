"""Coverage for ledger/inbox.py, ledger/voice.py, ledger/briefing.py.

These three modules sit close to the user-facing surface and were
the lowest-coverage non-experimental modules in the package.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ledger.config import LedgerConfig, reset_config, set_config


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


class InboxModuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _write_inbox(self, name: str, body: str) -> Path:
        path = self.cfg.ledger_notes_dir / "00_inbox" / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_list_inbox_extracts_title_and_frontmatter(self):
        from ledger.inbox import list_inbox

        self._write_inbox(
            "fact__sample.md",
            "---\ncreated: 2026-04-30T00:00:00Z\ntags: [test]\nconfidence: 0.7\n---\n\n# Sample fact title\n\nBody.\n",
        )
        items = list_inbox()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Sample fact title")
        self.assertEqual(items[0]["filename"], "fact__sample.md")
        self.assertEqual(items[0]["confidence"], 0.7)

    def test_list_inbox_falls_back_to_filename_when_no_h1(self):
        from ledger.inbox import list_inbox

        self._write_inbox("orphan.md", "---\ncreated: 2026-04-30T00:00:00Z\n---\n\nNo heading.\n")
        items = list_inbox()
        self.assertEqual(items[0]["title"], "orphan")

    def test_list_inbox_returns_empty_when_dir_missing(self):
        # Wipe the inbox dir to test the early return
        import shutil
        shutil.rmtree(self.cfg.ledger_notes_dir / "00_inbox")

        from ledger.inbox import list_inbox
        self.assertEqual(list_inbox(), [])

    def test_triage_suggestions_detects_signals(self):
        from ledger.inbox import triage_suggestions

        self._write_inbox("a.md", "---\n---\n\n# Pref\n\nI prefer two-space indentation\n")
        self._write_inbox("b.md", "---\n---\n\n# Goal\n\nachieve the next milestone\n")
        self._write_inbox("c.md", "---\n---\n\n# Loop\n\nthings to investigate later\n")
        self._write_inbox("d.md", "---\n---\n\n# Concept\n\nA new framework for ranking\n")
        self._write_inbox("e.md", "---\n---\n\n# Plain\n\njust some neutral text\n")

        by_file = {s["filename"]: s for s in triage_suggestions()}
        self.assertEqual(by_file["a.md"]["suggested_type"], "preferences")
        self.assertEqual(by_file["b.md"]["suggested_type"], "goals")
        self.assertEqual(by_file["c.md"]["suggested_type"], "loops")
        self.assertEqual(by_file["d.md"]["suggested_type"], "concepts")
        # Plain content defaults to facts
        self.assertEqual(by_file["e.md"]["suggested_type"], "facts")
        self.assertIn("default", by_file["e.md"]["reason"])

    def test_cleanup_inbox_removes_orphaned_locks_dry_run(self):
        from ledger.inbox import cleanup_inbox

        # Orphan lock with no .md sibling
        (self.cfg.ledger_notes_dir / "00_inbox" / "orphan.md.lock").write_text("", encoding="utf-8")
        # Real .md + lock pair (should NOT be flagged)
        self._write_inbox("real.md", "# Real\n")
        (self.cfg.ledger_notes_dir / "00_inbox" / "real.md.lock").write_text("", encoding="utf-8")

        result = cleanup_inbox(apply=False)
        self.assertIn("orphan.md.lock", result["orphaned_locks"])
        self.assertEqual(result["stale_items"], [])
        # Dry-run: file still there
        self.assertTrue((self.cfg.ledger_notes_dir / "00_inbox" / "orphan.md.lock").exists())

    def test_cleanup_inbox_apply_actually_deletes(self):
        from ledger.inbox import cleanup_inbox

        (self.cfg.ledger_notes_dir / "00_inbox" / "orphan.md.lock").write_text("", encoding="utf-8")
        cleanup_inbox(apply=True)
        self.assertFalse((self.cfg.ledger_notes_dir / "00_inbox" / "orphan.md.lock").exists())

    def test_cleanup_inbox_skips_non_stale_auto_items(self):
        from ledger.inbox import cleanup_inbox

        # Fresh auto-generated note (mtime = now): below stale threshold, should be skipped
        self._write_inbox("uncommitted_note_changes.md", "# x\n")
        result = cleanup_inbox(stale_days=14, apply=False)
        self.assertEqual(result["stale_items"], [])

    def test_cleanup_inbox_flags_stale_auto_items(self):
        from ledger.inbox import cleanup_inbox

        path = self._write_inbox("uncommitted_note_changes.md", "# x\n")
        # Backdate mtime by 30 days
        old = time.time() - (30 * 86400)
        import os
        os.utime(path, (old, old))

        result = cleanup_inbox(stale_days=14, apply=True)
        self.assertIn("uncommitted_note_changes.md", result["stale_items"])
        self.assertFalse(path.exists())

    def test_cleanup_inbox_returns_empty_when_dir_missing(self):
        import shutil
        shutil.rmtree(self.cfg.ledger_notes_dir / "00_inbox")
        from ledger.inbox import cleanup_inbox
        self.assertEqual(cleanup_inbox(), {"orphaned_locks": [], "stale_items": [], "logged_rejections": []})

    def test_promote_moves_inbox_item_to_correct_type(self):
        from ledger.inbox import promote

        path = self._write_inbox("fact__example.md", "---\n---\n\n# Example\n")
        target = promote(path, "facts")
        self.assertTrue(target.exists())
        self.assertFalse(path.exists())
        self.assertEqual(target.parent.name, "02_facts")
        # Filename should retain the slug
        self.assertIn("example", target.name)

    def test_promote_rejects_unknown_type(self):
        from ledger.inbox import promote
        path = self._write_inbox("a.md", "# x\n")
        with self.assertRaises(ValueError):
            promote(path, "not-a-type")

    def test_promote_rejects_missing_file(self):
        from ledger.inbox import promote
        with self.assertRaises(FileNotFoundError):
            promote("/nope/missing.md", "facts")

    def test_promote_appends_counter_on_collision(self):
        from ledger.inbox import promote
        # Pre-create the target so promote has to add a suffix
        existing = self.cfg.ledger_notes_dir / "02_facts" / "fact__dup.md"
        existing.write_text("# already there\n", encoding="utf-8")

        src = self._write_inbox("dup.md", "# dup\n")
        target = promote(src, "facts")
        self.assertTrue(target.name.startswith("fact__dup_"))


class VoiceModuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_import_voice_dna_creates_identity_note(self):
        from ledger.voice import import_voice_dna
        json_path = Path(self._tmp.name) / "voice.json"
        json_path.write_text(json.dumps({"tone": "concise", "rhythm": "short-sentences"}), encoding="utf-8")

        dest = import_voice_dna(json_path)
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.name, "id__voice_dna.md")

        text = dest.read_text(encoding="utf-8")
        self.assertIn("Voice DNA", text)
        self.assertIn("concise", text)

    def test_import_voice_dna_preserves_created_on_update(self):
        from ledger.voice import import_voice_dna
        json_path = Path(self._tmp.name) / "voice.json"
        json_path.write_text(json.dumps({"v": 1}), encoding="utf-8")

        first = import_voice_dna(json_path)
        first_text = first.read_text(encoding="utf-8")
        first_created = [l for l in first_text.splitlines() if l.startswith("created:")][0]

        # Update with new profile
        json_path.write_text(json.dumps({"v": 2}), encoding="utf-8")
        time.sleep(1.1)  # ensure updated timestamp differs
        second = import_voice_dna(json_path)
        second_text = second.read_text(encoding="utf-8")
        second_created = [l for l in second_text.splitlines() if l.startswith("created:")][0]
        self.assertEqual(first_created, second_created)
        self.assertIn('"v": 2', second_text)

    def test_import_voice_dna_rejects_missing_file(self):
        from ledger.voice import import_voice_dna
        with self.assertRaises(FileNotFoundError):
            import_voice_dna(Path(self._tmp.name) / "nope.json")

    def test_import_voice_dna_rejects_invalid_json(self):
        from ledger.voice import import_voice_dna
        json_path = Path(self._tmp.name) / "bad.json"
        json_path.write_text("not json {{{", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_voice_dna(json_path)

    def test_import_voice_dna_rejects_non_object(self):
        from ledger.voice import import_voice_dna
        json_path = Path(self._tmp.name) / "list.json"
        json_path.write_text("[1, 2, 3]", encoding="utf-8")
        with self.assertRaises(ValueError):
            import_voice_dna(json_path)

    def test_export_voice_dna_round_trip(self):
        from ledger.voice import import_voice_dna, export_voice_dna
        json_path = Path(self._tmp.name) / "voice.json"
        profile = {"tone": "direct", "examples": ["foo", "bar"]}
        json_path.write_text(json.dumps(profile), encoding="utf-8")

        import_voice_dna(json_path)
        exported = export_voice_dna()
        self.assertEqual(exported, profile)

    def test_export_voice_dna_returns_none_when_no_note(self):
        from ledger.voice import export_voice_dna
        self.assertIsNone(export_voice_dna())

    def test_get_voice_profile_aliases_export(self):
        from ledger.voice import import_voice_dna, get_voice_profile
        json_path = Path(self._tmp.name) / "voice.json"
        json_path.write_text(json.dumps({"x": 1}), encoding="utf-8")
        import_voice_dna(json_path)
        self.assertEqual(get_voice_profile(), {"x": 1})


class BriefingModuleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _write_loop(self, slug: str, status: str = "open", days_old: int = 5) -> None:
        """Write a loop with a fake updated timestamp."""
        from datetime import datetime, timedelta, timezone
        ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
        path = self.cfg.ledger_notes_dir / "05_open_loops" / f"loop__{slug}.md"
        path.write_text(
            f"---\ncreated: {ts}\nupdated: {ts}\ntags: [test]\nconfidence: 0.8\nsource: user\nscope: dev\nlang: en\nstatus: {status}\n---\n\n# Loop: {slug}\n\n## Question or task\n\nDo a thing.\n\n## Next action\n\n- [ ] step 1\n",
            encoding="utf-8",
        )

    def test_daily_briefing_runs_with_no_notes(self):
        from ledger.briefing import daily_briefing
        out = daily_briefing()
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 0)

    def test_daily_briefing_surfaces_open_loops(self):
        from ledger.briefing import daily_briefing
        self._write_loop("alpha", days_old=10)
        out = daily_briefing()
        self.assertIn("alpha", out.lower())

    def test_weekly_review_runs(self):
        from ledger.briefing import weekly_review
        self._write_loop("beta", days_old=3)
        out = weekly_review()
        self.assertIsInstance(out, str)
        self.assertGreater(len(out), 0)


if __name__ == "__main__":
    unittest.main()

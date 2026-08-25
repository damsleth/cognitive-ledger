"""Coverage for ledger inbox reject (Phase A, YAAMS⇄cogled contract)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from ledger.config import LedgerConfig, reset_config, set_config


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURE_V1 = ROOT / "tests" / "fixtures" / "inbox_candidate_v1.md"


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


class RejectInboxItemTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))
        self.inbox = self.cfg.ledger_notes_dir / "00_inbox"
        self.rejected = self.cfg.ledger_notes_dir / "08_indices" / "rejected_candidates.jsonl"

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _copy_fixture(self, name: str = "fact__afr.md") -> Path:
        dest = self.inbox / name
        dest.write_text(FIXTURE_V1.read_text(encoding="utf-8"), encoding="utf-8")
        return dest

    def _read_lines(self) -> list[dict]:
        return [json.loads(l) for l in self.rejected.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_v1_full_provenance_logs_and_removes(self):
        from ledger.inbox import reject_inbox_item

        path = self._copy_fixture()
        result = reject_inbox_item(path)

        self.assertTrue(result["removed"])
        self.assertFalse(path.exists())

        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        rec = lines[0]
        self.assertEqual(rec["contract_version"], 1)
        self.assertEqual(rec["yaams_candidate_id"], "a1b2c3d4e5f60718")
        self.assertEqual(rec["yaams_entity"], "AFØR")
        self.assertEqual(
            rec["yaams_source_item_ids"],
            ["9f2e1c0a7b3d4e5f", "6a8d2f1e0c9b3a7d"],
        )
        self.assertEqual(rec["title"], "AFØR krever minimum tre år å oppnå")
        self.assertEqual(rec["filename"], "fact__afr.md")
        self.assertEqual(rec["reason"], "discarded")
        # rejected_at is ISO-8601 UTC Z form
        self.assertRegex(rec["rejected_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        # exact key set per contract
        self.assertEqual(
            set(rec.keys()),
            {
                "contract_version", "rejected_at", "yaams_candidate_id",
                "yaams_entity", "yaams_source_item_ids", "title",
                "filename", "reason",
            },
        )

    def test_keep_leaves_file(self):
        from ledger.inbox import reject_inbox_item

        path = self._copy_fixture()
        result = reject_inbox_item(path, reason="duplicate", remove=False)

        self.assertFalse(result["removed"])
        self.assertTrue(path.exists())
        self.assertEqual(self._read_lines()[0]["reason"], "duplicate")

    def test_bare_filename_resolves_to_inbox(self):
        from ledger.inbox import reject_inbox_item

        self._copy_fixture("fact__bare.md")
        result = reject_inbox_item("fact__bare.md")
        self.assertTrue(result["removed"])
        self.assertFalse((self.inbox / "fact__bare.md").exists())

    def test_pre_v1_file_degrades_open(self):
        from ledger.inbox import reject_inbox_item

        path = self.inbox / "legacy.md"
        path.write_text(
            "---\ncreated: 2026-04-30T00:00:00Z\ntags: [test]\nconfidence: 0.6\n---\n\n# Legacy capture\n\nBody.\n",
            encoding="utf-8",
        )
        result = reject_inbox_item(path)
        self.assertTrue(result["removed"])

        rec = self._read_lines()[0]
        self.assertEqual(rec["yaams_candidate_id"], "")
        self.assertEqual(rec["yaams_entity"], "")
        self.assertEqual(rec["yaams_source_item_ids"], [])
        self.assertEqual(rec["title"], "Legacy capture")
        self.assertEqual(rec["filename"], "legacy.md")

    def test_invalid_reason_rejected(self):
        from ledger.inbox import reject_inbox_item

        path = self._copy_fixture()
        with self.assertRaises(ValueError):
            reject_inbox_item(path, reason="bogus")
        # file untouched, nothing logged
        self.assertTrue(path.exists())
        self.assertFalse(self.rejected.exists())

    def test_missing_file_raises(self):
        from ledger.inbox import reject_inbox_item

        with self.assertRaises(FileNotFoundError):
            reject_inbox_item("nope__missing.md")

    def test_appends_multiple_lines(self):
        from ledger.inbox import reject_inbox_item

        reject_inbox_item(self._copy_fixture("a.md"))
        reject_inbox_item(self._copy_fixture("b.md"))
        self.assertEqual(len(self._read_lines()), 2)


class ListRejectionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))
        self.inbox = self.cfg.ledger_notes_dir / "00_inbox"
        self.rejected = self.cfg.ledger_notes_dir / "08_indices" / "rejected_candidates.jsonl"

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _write_record(self, candidate_id: str, rejected_at: str, reason: str = "discarded") -> None:
        rec = {
            "contract_version": 1,
            "rejected_at": rejected_at,
            "yaams_candidate_id": candidate_id,
            "yaams_entity": "TestEntity",
            "yaams_source_item_ids": [],
            "title": f"Title {candidate_id}",
            "filename": f"{candidate_id}.md",
            "reason": reason,
        }
        with self.rejected.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def test_missing_file_returns_empty(self):
        from ledger.inbox import list_rejections
        result = list_rejections()
        self.assertEqual(result, [])

    def test_returns_newest_first(self):
        from ledger.inbox import list_rejections
        self._write_record("aaa", "2026-01-01T00:00:00Z")
        self._write_record("bbb", "2026-03-01T00:00:00Z")
        self._write_record("ccc", "2026-02-01T00:00:00Z")
        result = list_rejections()
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["yaams_candidate_id"], "bbb")
        self.assertEqual(result[1]["yaams_candidate_id"], "ccc")
        self.assertEqual(result[2]["yaams_candidate_id"], "aaa")

    def test_since_days_filter(self):
        from ledger.inbox import list_rejections
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_ts = (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write_record("old", old_ts)
        self._write_record("recent", recent_ts)
        result = list_rejections(since_days=30)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["yaams_candidate_id"], "recent")

    def test_malformed_lines_skipped(self):
        from ledger.inbox import list_rejections
        self._write_record("good", "2026-06-01T00:00:00Z")
        with self.rejected.open("a", encoding="utf-8") as fh:
            fh.write("NOT VALID JSON\n")
        result = list_rejections()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["yaams_candidate_id"], "good")


class ClearRejectionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))
        self.inbox = self.cfg.ledger_notes_dir / "00_inbox"
        self.rejected = self.cfg.ledger_notes_dir / "08_indices" / "rejected_candidates.jsonl"

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _write_record(self, candidate_id: str, rejected_at: str) -> None:
        rec = {
            "contract_version": 1,
            "rejected_at": rejected_at,
            "yaams_candidate_id": candidate_id,
            "yaams_entity": "TestEntity",
            "yaams_source_item_ids": [],
            "title": f"Title {candidate_id}",
            "filename": f"{candidate_id}.md",
            "reason": "discarded",
        }
        with self.rejected.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def test_clear_all_truncates_and_returns_count(self):
        from ledger.inbox import clear_rejections
        self._write_record("a", "2026-01-01T00:00:00Z")
        self._write_record("b", "2026-02-01T00:00:00Z")
        count = clear_rejections()
        self.assertEqual(count, 2)
        self.assertEqual(self.rejected.read_text(encoding="utf-8"), "")

    def test_clear_before_removes_older_records(self):
        from ledger.inbox import clear_rejections
        from datetime import datetime, timezone
        self._write_record("old", "2025-12-01T00:00:00Z")
        self._write_record("keep", "2026-06-01T00:00:00Z")
        before = datetime(2026, 1, 1, tzinfo=timezone.utc)
        count = clear_rejections(before=before)
        self.assertEqual(count, 1)
        lines = [
            json.loads(l) for l in self.rejected.read_text(encoding="utf-8").splitlines() if l.strip()
        ]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["yaams_candidate_id"], "keep")

    def test_missing_file_returns_zero(self):
        from ledger.inbox import clear_rejections
        self.assertFalse(self.rejected.exists())
        count = clear_rejections()
        self.assertEqual(count, 0)

    def test_malformed_lines_preserved(self):
        from ledger.inbox import clear_rejections
        from datetime import datetime, timezone
        self._write_record("keep", "2026-06-01T00:00:00Z")
        with self.rejected.open("a", encoding="utf-8") as fh:
            fh.write("NOT VALID JSON\n")
        before = datetime(2020, 1, 1, tzinfo=timezone.utc)
        count = clear_rejections(before=before)
        self.assertEqual(count, 0)
        raw = self.rejected.read_text(encoding="utf-8")
        self.assertIn("NOT VALID JSON", raw)
        self.assertIn("keep", raw)


class IsRejectedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))
        self.inbox = self.cfg.ledger_notes_dir / "00_inbox"
        self.rejected = self.cfg.ledger_notes_dir / "08_indices" / "rejected_candidates.jsonl"

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _write_record(self, candidate_id: str) -> None:
        rec = {
            "contract_version": 1,
            "rejected_at": "2026-06-01T00:00:00Z",
            "yaams_candidate_id": candidate_id,
            "yaams_entity": "TestEntity",
            "yaams_source_item_ids": [],
            "title": "Some title",
            "filename": f"{candidate_id}.md",
            "reason": "discarded",
        }
        with self.rejected.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def test_exact_id_match(self):
        from ledger.inbox import is_rejected
        self._write_record("myid123")
        self.assertTrue(is_rejected("myid123"))
        self.assertFalse(is_rejected("myid12"))  # No partial match

    def test_unknown_or_empty_id_false(self):
        from ledger.inbox import is_rejected
        self._write_record("realid")
        self.assertFalse(is_rejected(""))
        self.assertFalse(is_rejected(None))
        self.assertFalse(is_rejected("nothere"))


class CleanupLogsRejectionsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))
        self.inbox = self.cfg.ledger_notes_dir / "00_inbox"
        self.rejected = self.cfg.ledger_notes_dir / "08_indices" / "rejected_candidates.jsonl"

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _make_stale_yaams_file(self, name: str, candidate_id: str = "cand123") -> Path:
        """Create a stale session__* file with YAAMS frontmatter."""
        import os, time
        path = self.inbox / name
        path.write_text(
            f"---\ncreated: 2020-01-01T00:00:00Z\npromoted_by: yaams\nyaams_candidate_id: {candidate_id}\nyaams_entity: TestEnt\nyaams_source_item_ids: []\n---\n\n# Test\n",
            encoding="utf-8",
        )
        # Set mtime to 30 days ago.
        old_time = time.time() - 31 * 86400
        os.utime(path, (old_time, old_time))
        return path

    def _make_stale_plain_file(self, name: str) -> Path:
        """Create a stale session__* file without YAAMS frontmatter."""
        import os, time
        path = self.inbox / name
        path.write_text(
            "---\ncreated: 2020-01-01T00:00:00Z\ntags: [test]\n---\n\n# Test\n",
            encoding="utf-8",
        )
        old_time = time.time() - 31 * 86400
        os.utime(path, (old_time, old_time))
        return path

    def test_stale_yaams_candidate_logged_on_apply(self):
        from ledger.inbox import cleanup_inbox
        self._make_stale_yaams_file("session__yaams_stale.md")
        result = cleanup_inbox(stale_days=14, apply=True)
        self.assertIn("session__yaams_stale.md", result["logged_rejections"])
        self.assertFalse((self.inbox / "session__yaams_stale.md").exists())
        # Check rejection was written.
        self.assertTrue(self.rejected.exists())
        lines = [json.loads(l) for l in self.rejected.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["yaams_candidate_id"], "cand123")

    def test_stale_plain_file_not_logged(self):
        from ledger.inbox import cleanup_inbox
        self._make_stale_plain_file("session__plain_stale.md")
        result = cleanup_inbox(stale_days=14, apply=True)
        self.assertNotIn("session__plain_stale.md", result["logged_rejections"])
        self.assertFalse((self.inbox / "session__plain_stale.md").exists())
        self.assertFalse(self.rejected.exists())

    def test_dry_run_does_not_log(self):
        from ledger.inbox import cleanup_inbox
        self._make_stale_yaams_file("session__yaams_dry.md")
        result = cleanup_inbox(stale_days=14, apply=False)
        # In dry-run mode, logged_rejections should be empty.
        self.assertEqual(result["logged_rejections"], [])
        self.assertTrue((self.inbox / "session__yaams_dry.md").exists())
        self.assertFalse(self.rejected.exists())


class InboxRejectedCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))
        self.inbox = self.cfg.ledger_notes_dir / "00_inbox"
        self.rejected = self.cfg.ledger_notes_dir / "08_indices" / "rejected_candidates.jsonl"

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _write_record(self, candidate_id: str, rejected_at: str) -> None:
        rec = {
            "contract_version": 1,
            "rejected_at": rejected_at,
            "yaams_candidate_id": candidate_id,
            "yaams_entity": "TestEntity",
            "yaams_source_item_ids": ["src1"],
            "title": f"Title {candidate_id}",
            "filename": f"{candidate_id}.md",
            "reason": "discarded",
        }
        with self.rejected.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _run_cli(self, argv: list[str]) -> tuple[str, int]:
        """Run main() with given argv; return (stdout, exit_code)."""
        import io
        from unittest.mock import patch
        from ledger.cli import main
        buf = io.StringIO()
        exit_code = 0
        with patch("sys.stdout", buf), patch("sys.argv", ["ledger"] + argv):
            try:
                main()
            except SystemExit as exc:
                exit_code = exc.code if isinstance(exc.code, int) else 1
        return buf.getvalue(), exit_code

    def test_list_empty(self):
        out, code = self._run_cli(["inbox", "rejected"])
        self.assertEqual(code, 0)
        self.assertIn("No rejection records", out)

    def test_list_shows_records(self):
        self._write_record("abc123", "2026-04-30T19:53:00Z")
        out, code = self._run_cli(["inbox", "rejected"])
        self.assertEqual(code, 0)
        self.assertIn("abc123", out)
        self.assertIn("discarded", out)

    def test_list_json_envelope(self):
        self._write_record("j1", "2026-05-01T00:00:00Z")
        out, code = self._run_cli(["inbox", "rejected", "--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["tool"], "ledger")
        self.assertEqual(data["command"], "inbox rejected")
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 1)
        self.assertEqual(len(data["records"]), 1)

    def test_clear_yes_truncates(self):
        self._write_record("todel", "2026-01-01T00:00:00Z")
        out, code = self._run_cli(["inbox", "rejected", "--clear", "--yes"])
        self.assertEqual(code, 0)
        self.assertIn("1", out)
        self.assertEqual(self.rejected.read_text(encoding="utf-8"), "")

    def test_clear_prompts_abort_on_no(self):
        import io
        from unittest.mock import patch
        from ledger.cli import main
        self._write_record("notdel", "2026-01-01T00:00:00Z")
        buf = io.StringIO()
        exit_code = 0
        with patch("sys.stdout", buf), patch("sys.argv", ["ledger", "inbox", "rejected", "--clear"]), \
             patch("builtins.input", return_value="n"):
            try:
                main()
            except SystemExit as exc:
                exit_code = exc.code if isinstance(exc.code, int) else 1
        out = buf.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("Aborted", out)
        # File should still have content.
        content = self.rejected.read_text(encoding="utf-8")
        self.assertIn("notdel", content)

    def test_clear_before_removes_older_keeps_newer(self):
        self._write_record("old", "2025-12-01T00:00:00Z")
        self._write_record("new", "2026-06-01T00:00:00Z")
        out, code = self._run_cli(["inbox", "rejected", "--clear", "--before", "2026-01-01", "--yes"])
        self.assertEqual(code, 0)
        lines = [
            json.loads(l) for l in self.rejected.read_text(encoding="utf-8").splitlines() if l.strip()
        ]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["yaams_candidate_id"], "new")


if __name__ == "__main__":
    unittest.main()


class TestReapUnheldLocks(unittest.TestCase):
    """`FileLock` never unlinks (avoiding an unlink-after-unlock race), so lock
    files accumulate. `cleanup_inbox` used to miss them entirely: it only looked
    in 00_inbox and only at locks whose .md sibling was gone. 57 were sitting in
    the live tree, mostly under 02_facts/.
    """

    def test_reaps_unheld_locks_across_typed_folders(self):
        from ledger.inbox import reap_unheld_locks

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "02_facts").mkdir()
            (root / "05_open_loops").mkdir()
            note = root / "02_facts" / "fact__x.md"
            note.write_text("# x\n", encoding="utf-8")
            lock_a = root / "02_facts" / "fact__x.md.lock"
            lock_b = root / "05_open_loops" / "loop__y.md.lock"
            lock_a.touch()
            lock_b.touch()

            found = reap_unheld_locks(notes_dir=root, apply=False)
            self.assertEqual(len(found), 2, found)
            self.assertTrue(lock_a.exists(), "dry run must not delete")

            reaped = reap_unheld_locks(notes_dir=root, apply=True)
            self.assertEqual(len(reaped), 2)
            self.assertFalse(lock_a.exists())
            self.assertFalse(lock_b.exists())
            self.assertTrue(note.exists(), "the note itself must survive")

    def test_reaps_nested_non_markdown_lock(self):
        from ledger.inbox import reap_unheld_locks

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "08_indices" / "importers" / "claude_memory"
            state_dir.mkdir(parents=True)
            lock = state_dir / "state.json.lock"
            lock.touch()

            found = reap_unheld_locks(notes_dir=root, apply=False)
            self.assertEqual(found, ["08_indices/importers/claude_memory/state.json.lock"])
            self.assertTrue(lock.exists(), "dry run must not delete")

            reaped = reap_unheld_locks(notes_dir=root, apply=True)
            self.assertEqual(reaped, found)
            self.assertFalse(lock.exists())

    def test_leaves_a_held_lock_alone(self):
        """A lock someone holds is live contention, not garbage."""
        import fcntl
        import os

        from ledger.inbox import reap_unheld_locks

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "02_facts").mkdir()
            lock = root / "02_facts" / "fact__held.md.lock"
            lock.touch()
            fd = os.open(str(lock), os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(reap_unheld_locks(notes_dir=root, apply=True), [])
                self.assertTrue(lock.exists())
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

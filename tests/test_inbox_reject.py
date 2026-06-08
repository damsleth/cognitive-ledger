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


if __name__ == "__main__":
    unittest.main()

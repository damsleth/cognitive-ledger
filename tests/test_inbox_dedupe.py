"""Inbox candidates that duplicate a ledger note are refused at promote time."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.config import LedgerConfig, reset_config, set_config  # noqa: E402
from ledger.inbox import DuplicateCandidateError, TriageAction, _load_one_candidate, apply_actions, promote  # noqa: E402

NOTE = """---
created: 2026-10-28T10:00:00Z
updated: 2026-10-28T10:00:00Z
tags: [test]
confidence: 0.7
source: assistant
scope: personal
lang: no
status: open
---

# {title}

{body}
"""


class InboxDedupeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.nd = root / "notes"
        for d in ("00_inbox", "02_facts", "05_open_loops", "08_indices", "09_archive"):
            (self.nd / d).mkdir(parents=True)
        (self.nd / "08_indices" / "timeline.md").write_text("# Timeline\n", encoding="utf-8")
        set_config(LedgerConfig(ledger_root=root, ledger_notes_dir=self.nd, source_notes_dir=root / "source"))
        (self.nd / "05_open_loops" / "loop__spotify.md").write_text(
            NOTE.format(title="Spotify familie: bytt kort og fjern eks", body="Betaling på utgått kort."),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        reset_config()
        self.tmp.cleanup()

    def _inbox(self, name: str, title: str, body: str = "Noe helt annet.") -> Path:
        p = self.nd / "00_inbox" / name
        p.write_text(NOTE.format(title=title, body=body), encoding="utf-8")
        return p

    def test_duplicate_title_is_refused_and_left_in_inbox(self) -> None:
        src = self._inbox("loop__spotify_kort.md", "Spotify familie: bytt kort, fjern eks")
        with self.assertRaises(DuplicateCandidateError) as ctx:
            promote(src, "loops", notes_dir=self.nd)
        self.assertIn("05_open_loops/loop__spotify.md", str(ctx.exception))
        self.assertTrue(src.is_file())

    def test_force_promotes_anyway(self) -> None:
        src = self._inbox("loop__spotify_kort.md", "Spotify familie: bytt kort, fjern eks")
        target = promote(src, "loops", notes_dir=self.nd, force=True)
        self.assertTrue(target.is_file())

    def test_unrelated_candidate_promotes(self) -> None:
        src = self._inbox("fact__korpsbil.md", "Korpsbil 52 ute av drift igjen")
        target = promote(src, "facts", notes_dir=self.nd)
        self.assertTrue(target.is_file())

    def test_apply_actions_reports_duplicate_as_failed(self) -> None:
        src = self._inbox("loop__spotify_kort.md", "Spotify familie: bytt kort, fjern eks")
        cand = _load_one_candidate(src)
        summary = apply_actions([cand], [TriageAction(row=1, action="accept", target_type="loops")], notes_dir=self.nd)
        self.assertEqual(summary["failed"], 1)
        self.assertIn("duplicates", summary["errors"][0])
        self.assertTrue(src.is_file())


if __name__ == "__main__":
    unittest.main()

"""Tests for `ledger inbox conflicts` CLI subcommand (plan 40, step E9/E10)."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from ledger.config import LedgerConfig, reset_config, set_config

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_VALID_NOTE = """---
created: 2026-06-01T10:00:00Z
updated: 2026-06-01T10:00:00Z
tags: [inbox, yaams]
confidence: 0.8
source: tool
scope: work
lang: en
promoted_by: yaams
yaams_candidate_id: {sig}
yaams_entity: {entity}
---

# {title}

## Statement

This is the statement about {title}.
"""


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


def _write_candidate(inbox: Path, name: str, **extra_fm) -> Path:
    text = _VALID_NOTE.format(
        sig=extra_fm.pop("sig", "deadbeef"),
        entity=extra_fm.pop("entity", "E"),
        title=extra_fm.pop("title", "Title"),
    )
    fm_extra = ""
    for k, v in extra_fm.items():
        fm_extra += f"{k}: {v}\n"
    if fm_extra:
        text = text.replace("---\n\n# ", f"{fm_extra}---\n\n# ", 1)
    path = inbox / name
    path.write_text(text, encoding="utf-8")
    return path


class InboxConflictsCLITests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))
        self.inbox = self.cfg.ledger_notes_dir / "00_inbox"

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _run_cli(self, argv: list[str]) -> tuple[int, str]:
        """Run ledger CLI main() with given argv, capture stdout."""
        from ledger.cli import main as cli_main
        buf = io.StringIO()
        old_argv = sys.argv
        old_stdout = sys.stdout
        rc = 0
        try:
            sys.argv = ["ledger"] + argv
            sys.stdout = buf
            try:
                cli_main()
            except SystemExit as exc:
                rc = int(exc.code) if exc.code is not None else 0
        finally:
            sys.argv = old_argv
            sys.stdout = old_stdout
        return rc, buf.getvalue()

    def test_inbox_conflicts_filters_to_contradictions(self):
        _write_candidate(
            self.inbox, "fact__contra.md",
            sig="aaaa1111",
            title="Contra",
            merge_with="notes/02_facts/fact__existing.md",
            conflict_classification="contradict",
            conflict_confidence=0.88,
            conflict_reason="contradicts existing claim",
        )
        _write_candidate(
            self.inbox, "fact__supplement.md",
            sig="bbbb2222",
            title="Supplement",
            conflict_classification="supplement",
        )
        _write_candidate(self.inbox, "fact__plain.md", sig="cccc3333", title="Plain")

        rc, out = self._run_cli(["inbox", "conflicts"])
        self.assertEqual(rc, 0)
        self.assertIn("fact__contra.md", out)
        self.assertNotIn("fact__supplement.md", out)
        self.assertNotIn("fact__plain.md", out)

    def test_inbox_conflicts_json_envelope_shape(self):
        _write_candidate(
            self.inbox, "fact__contra.md",
            sig="aaaa1111",
            title="Contra Title",
            merge_with="notes/02_facts/fact__existing.md",
            conflict_classification="contradict",
            conflict_confidence=0.91,
            conflict_reason="contradicts existing date",
        )

        rc, out = self._run_cli(["inbox", "conflicts", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.strip())
        self.assertEqual(data["tool"], "ledger")
        self.assertEqual(data["command"], "inbox conflicts")
        self.assertTrue(data["ok"])
        self.assertIsInstance(data["conflicts"], list)
        self.assertEqual(len(data["conflicts"]), 1)
        record = data["conflicts"][0]
        self.assertEqual(record["filename"], "fact__contra.md")
        self.assertEqual(record["conflict_classification"], "contradict")
        self.assertIn("statement", record)
        self.assertIn("merge_with", record)
        self.assertIn("conflict_reason", record)

    def test_inbox_conflicts_empty_inbox(self):
        # No contradict candidates at all
        _write_candidate(self.inbox, "fact__plain.md", sig="00001111", title="Plain")

        rc, out = self._run_cli(["inbox", "conflicts"])
        self.assertEqual(rc, 0)
        self.assertIn("No conflict candidates", out)

    def test_inbox_conflicts_empty_inbox_json(self):
        rc, out = self._run_cli(["inbox", "conflicts", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.strip())
        self.assertEqual(data["conflicts"], [])
        self.assertTrue(data["ok"])

    def test_inbox_conflicts_multiple_contradictions(self):
        for i in range(3):
            _write_candidate(
                self.inbox, f"fact__contra{i}.md",
                sig=f"abcd{i:04d}",
                title=f"Contra {i}",
                merge_with=f"notes/02_facts/fact__existing{i}.md",
                conflict_classification="contradict",
                conflict_confidence=0.75 + i * 0.05,
            )

        rc, out = self._run_cli(["inbox", "conflicts", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.strip())
        self.assertEqual(len(data["conflicts"]), 3)


if __name__ == "__main__":
    unittest.main()

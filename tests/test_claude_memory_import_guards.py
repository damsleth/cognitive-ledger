"""Guards on the claude-memory importer: classification + re-import safety.

Both regressions were found 2026-08-21 while auditing ~/brain/ledger:
  * a naming-convention *fact* was filed as a concept (title-marker too loose)
  * memory files already promoted by triage were re-imported, which forked
    fact/loop twins and reopened work that had been closed
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ledger.claude_memory import (
    SKIP_ALREADY_PROMOTED,
    classify,
    existing_external_ids,
)


class TestClassify(unittest.TestCase):
    def test_naming_convention_is_a_fact_not_a_concept(self):
        """'convention' in a title must not outrank the note being a plain fact."""
        cls = classify(
            "project",
            "nocos-jan-naming-convention",
            'Jan = Jan Guttormsen (Currents, ekstern). "Jan Karl" = Jan Karl Andersen.',
        )
        self.assertEqual(cls.note_type, "facts")

    def test_real_concept_markers_still_win(self):
        for name in (
            "third-mind-integration-architecture",
            "note-autonomy-principle",
            "retrieval-invariant",
        ):
            with self.subTest(name=name):
                self.assertEqual(classify("project", name, "body").note_type, "concepts")

    def test_multiword_markers_do_not_match_kebab_titles(self):
        """Documents a known limitation rather than leaving it silently broken.

        Titles arrive as kebab-case slugs, so the multi-word markers
        ("design pattern", "mental model") can never fire on a title — only in
        a prose body. Deliberately not widened: bug A was *over*-classification,
        so loosening the match is the wrong direction without evidence.
        """
        self.assertEqual(
            classify("project", "hugr-design-pattern", "body").note_type, "facts"
        )
        self.assertEqual(
            classify("project", "x", "this is a design pattern").note_type, "concepts"
        )

    def test_open_work_still_becomes_a_loop(self):
        cls = classify("project", "migrate-outlook-addin", "TODO: next step is to migrate the RG")
        self.assertEqual(cls.note_type, "loops")


class TestExistingExternalIds(unittest.TestCase):
    def test_indexes_typed_notes_and_ignores_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "02_facts").mkdir()
            (root / "00_inbox").mkdir()
            (root / "02_facts" / "fact__promoted.md").write_text(
                "---\nexternal_id: yaams/already-here\n---\n\n# Promoted\n",
                encoding="utf-8",
            )
            # inbox is staging, not a promotion — must not count
            (root / "00_inbox" / "note__staged.md").write_text(
                "---\nexternal_id: yaams/still-staged\n---\n\n# Staged\n",
                encoding="utf-8",
            )

            class _Cfg:
                ledger_notes_dir = root

            found = existing_external_ids(_Cfg())

        self.assertIn("yaams/already-here", found)
        self.assertNotIn("yaams/still-staged", found)

    def test_missing_notes_dir_is_not_fatal(self):
        class _Cfg:
            ledger_notes_dir = Path("/nonexistent/ledger/notes")

        self.assertEqual(existing_external_ids(_Cfg()), {})


class TestSkipMarker(unittest.TestCase):
    def test_marker_is_a_distinct_bucket_from_unchanged(self):
        """The reason string must be greppable so reports can split the buckets."""
        reason = f"{SKIP_ALREADY_PROMOTED}: /notes/02_facts/fact__x.md"
        self.assertTrue(reason.startswith(SKIP_ALREADY_PROMOTED))
        self.assertNotIn("unchanged", reason)


if __name__ == "__main__":
    unittest.main()

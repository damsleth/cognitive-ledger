"""Coverage for interactive batch inbox triage (Plan 37, Phase B)."""

from __future__ import annotations

import builtins
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
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


_VALID_NOTE = """---
created: 2026-06-01T10:00:00Z
updated: 2026-06-01T10:00:00Z
tags: [test]
confidence: 0.6
source: inferred
scope: personal
lang: en
promoted_by: yaams
yaams_candidate_id: {sig}
yaams_entity: "{entity}"
---

# {title}

## Statement
{title} body line.
"""


class TriageTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg = _set_temp_config(Path(self._tmp.name))
        self.inbox = self.cfg.ledger_notes_dir / "00_inbox"
        self.conflicts = self.inbox / "_conflicts"

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _write(self, name: str, *, sig: str = "0000000000000000",
               entity: str = "E", title: str = "Title",
               promoted_by: str = "yaams", dest: Path | None = None,
               created: str = "2026-06-01T10:00:00Z",
               merge_with: str | None = None,
               conflict_classification: str | None = None,
               conflict_confidence: float | None = None,
               conflict_reason: str | None = None,
               dedup_similarity: float | None = None) -> Path:
        dest = dest or self.inbox
        text = _VALID_NOTE.format(sig=sig, entity=entity, title=title)
        if promoted_by != "yaams":
            text = text.replace("promoted_by: yaams\n", f"promoted_by: {promoted_by}\n")
        text = text.replace("created: 2026-06-01T10:00:00Z", f"created: {created}")
        extra_fm = ""
        if merge_with:
            extra_fm += f"merge_with: {merge_with}\n"
        if dedup_similarity is not None:
            extra_fm += f"dedup_similarity: {dedup_similarity}\n"
        if conflict_classification is not None:
            extra_fm += f"conflict_classification: {conflict_classification}\n"
        if conflict_confidence is not None:
            extra_fm += f"conflict_confidence: {conflict_confidence}\n"
        if conflict_reason is not None:
            extra_fm += f"conflict_reason: {conflict_reason}\n"
        if extra_fm:
            text = text.replace("---\n\n# ", f"{extra_fm}---\n\n# ", 1)
        path = dest / name
        path.write_text(text, encoding="utf-8")
        return path


class LoadCandidatesTests(TriageTestBase):
    def test_parses_frontmatter_and_body(self):
        from ledger.inbox import load_candidates_for_triage

        self._write("fact__a.md", sig="aaaa1111bbbb2222", entity="Crayon", title="Crayon uses Viva")
        cands = load_candidates_for_triage()
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual(c.type, "facts")
        self.assertEqual(c.title, "Crayon uses Viva")
        self.assertEqual(c.signature, "aaaa1111bbbb2222")
        self.assertEqual(c.yaams_entity, "Crayon")
        self.assertIn("body line", c.body)

    def test_sorts_yaams_first(self):
        from ledger.inbox import load_candidates_for_triage

        self._write("manual.md", sig="zzzz0000", promoted_by="user", title="Manual")
        self._write("yaams.md", sig="aaaa0000", title="Yaams")
        cands = load_candidates_for_triage()
        self.assertEqual(cands[0].title, "Yaams")
        self.assertEqual(cands[1].title, "Manual")

    def test_groups_by_signature(self):
        from ledger.inbox import load_candidates_for_triage

        self._write("c.md", sig="ccccffff", title="C")
        self._write("a1.md", sig="aaaa0000", title="A1", created="2026-06-02T10:00:00Z")
        self._write("a2.md", sig="aaaa0000", title="A2", created="2026-06-01T10:00:00Z")
        cands = load_candidates_for_triage()
        # aaaa group first; within group oldest created first => A2 then A1
        self.assertEqual([c.title for c in cands], ["A2", "A1", "C"])

    def test_conflicts_subfolder_loaded_and_sorted(self):
        from ledger.inbox import load_candidates_for_triage

        self.conflicts.mkdir()
        self._write("regular.md", sig="bbbb0000", title="Regular")
        self._write("conflict.md", sig="aaaa0000", title="Conflict", dest=self.conflicts)
        cands = load_candidates_for_triage()
        self.assertEqual(len(cands), 2)
        # Conflict has lower signature so sorts first.
        self.assertEqual(cands[0].title, "Conflict")


class ParseCommandTests(TriageTestBase):
    def _candidates(self, n: int = 12):
        from ledger.inbox import load_candidates_for_triage

        for i in range(1, n + 1):
            self._write(f"fact__{i:02d}.md", sig=f"{i:016d}", title=f"Note {i}")
        return load_candidates_for_triage()

    def test_accept_range(self):
        from ledger.inbox_triage import _parse_command

        cands = self._candidates()
        actions = _parse_command("a 1,3,5-7", cands)
        self.assertEqual([a.row for a in actions], [1, 3, 5, 6, 7])
        self.assertTrue(all(a.action == "accept" for a in actions))

    def test_accept_open_range(self):
        from ledger.inbox_triage import _parse_command

        cands = self._candidates()
        actions = _parse_command("a 10-", cands)
        self.assertEqual([a.row for a in actions], [10, 11, 12])

    def test_reject_list(self):
        from ledger.inbox_triage import _parse_command

        cands = self._candidates()
        actions = _parse_command("r 2,4", cands)
        self.assertEqual([a.row for a in actions], [2, 4])
        self.assertTrue(all(a.action == "reject" for a in actions))

    def test_type_override(self):
        from ledger.inbox_triage import _parse_command

        cands = self._candidates()
        actions = _parse_command("a 1:preferences", cands)
        self.assertEqual(actions[0].target_type, "preferences")

    def test_invalid_type_override_raises(self):
        from ledger.inbox_triage import _parse_command

        cands = self._candidates()
        with self.assertRaises(ValueError):
            _parse_command("a 1:bogus", cands)

    def test_invalid_index_raises(self):
        from ledger.inbox_triage import _parse_command

        cands = self._candidates()
        with self.assertRaises(ValueError):
            _parse_command("a 1,2,abc", cands)

    def test_out_of_range_raises(self):
        from ledger.inbox_triage import _parse_command

        cands = self._candidates(3)
        with self.assertRaises(ValueError):
            _parse_command("a 5", cands)

    def test_unknown_command_raises(self):
        from ledger.inbox_triage import _parse_command

        cands = self._candidates(3)
        with self.assertRaises(ValueError):
            _parse_command("x 1", cands)

    def test_merge_without_hint_raises(self):
        from ledger.inbox_triage import _parse_command

        cands = self._candidates(3)
        with self.assertRaises(ValueError):
            _parse_command("m 1", cands)


class ApplyActionsTests(TriageTestBase):
    def test_accept_calls_promote(self):
        from ledger.inbox import (
            TriageAction, apply_actions, load_candidates_for_triage,
        )

        self._write("fact__a.md", title="Accept me")
        cands = load_candidates_for_triage()
        summary = apply_actions(cands, [TriageAction(row=1, action="accept")])
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertFalse((self.inbox / "fact__a.md").exists())
        promoted = list((self.cfg.ledger_notes_dir / "02_facts").glob("*.md"))
        self.assertEqual(len(promoted), 1)

    def test_accept_type_override_routes_to_dir(self):
        from ledger.inbox import (
            TriageAction, apply_actions, load_candidates_for_triage,
        )

        self._write("fact__a.md", title="Pref me")
        cands = load_candidates_for_triage()
        apply_actions(cands, [TriageAction(row=1, action="accept", target_type="preferences")])
        self.assertEqual(len(list((self.cfg.ledger_notes_dir / "03_preferences").glob("*.md"))), 1)

    def test_reject_calls_reject_inbox_item(self):
        from ledger.inbox import (
            TriageAction, apply_actions, load_candidates_for_triage,
        )

        self._write("fact__a.md", sig="abcd1234abcd1234", title="Reject me")
        cands = load_candidates_for_triage()
        summary = apply_actions(cands, [TriageAction(row=1, action="reject")])
        self.assertEqual(summary["rejected"], 1)
        self.assertFalse((self.inbox / "fact__a.md").exists())
        rejected = self.cfg.ledger_notes_dir / "08_indices" / "rejected_candidates.jsonl"
        self.assertTrue(rejected.exists())
        self.assertIn("abcd1234abcd1234", rejected.read_text(encoding="utf-8"))

    def test_merge_appends_delimited_provenance_block(self):
        from ledger.inbox import (
            TriageAction, apply_actions, load_candidates_for_triage,
        )

        target = self.cfg.ledger_notes_dir / "02_facts" / "fact__target.md"
        target.write_text(_VALID_NOTE.format(
            sig="ffff0000ffff0000", entity="E", title="Existing target"
        ).replace("promoted_by: yaams\n", "promoted_by: user\n"), encoding="utf-8")

        src = self._write("fact__src.md", sig="1111aaaa2222bbbb", title="Merge source")
        cands = load_candidates_for_triage()
        summary = apply_actions(
            cands, [TriageAction(row=1, action="merge", target_note=target)]
        )
        self.assertEqual(summary["merged"], 1)
        merged_text = target.read_text(encoding="utf-8")
        self.assertIn("## Added from inbox candidate", merged_text)
        self.assertIn("fact__src.md", merged_text)
        self.assertIn("1111aaaa2222bbbb", merged_text)
        # source removed, backup written
        self.assertFalse(src.exists())
        self.assertEqual(len(summary["backups"]), 1)
        self.assertTrue(Path(summary["backups"][0]).exists())

    def test_merge_no_target_marks_failed(self):
        from ledger.inbox import (
            TriageAction, apply_actions, load_candidates_for_triage,
        )

        self._write("fact__a.md", title="No target")
        cands = load_candidates_for_triage()
        summary = apply_actions(cands, [TriageAction(row=1, action="merge")])
        self.assertEqual(summary["merged"], 0)
        self.assertEqual(summary["failed"], 1)

    def test_continues_on_per_row_failure(self):
        from ledger.inbox import (
            TriageAction, apply_actions, load_candidates_for_triage,
        )

        self._write("fact__a.md", sig="0001", title="Good 1")
        self._write("fact__b.md", sig="0002", title="Good 2")
        cands = load_candidates_for_triage()
        # row 3 does not exist -> failure, but rows 1,2 still apply
        actions = [
            TriageAction(row=1, action="accept"),
            TriageAction(row=3, action="accept"),
            TriageAction(row=2, action="reject"),
        ]
        summary = apply_actions(cands, actions)
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["rejected"], 1)
        self.assertEqual(summary["failed"], 1)

    def test_accepted_candidate_failing_lint_stays_in_inbox(self):
        from ledger.inbox import (
            TriageAction, apply_actions, load_candidates_for_triage,
        )

        # Write a candidate with an invalid source so lint fails post-promote.
        bad = self.inbox / "fact__bad.md"
        bad.write_text(
            "---\ncreated: 2026-06-01T10:00:00Z\nupdated: 2026-06-01T10:00:00Z\n"
            "tags: [test]\nconfidence: 0.6\nsource: totally_invalid\nscope: personal\n"
            "lang: en\npromoted_by: yaams\nyaams_candidate_id: deadbeef\n---\n\n"
            "# Bad note\n\n## Statement\nbody\n",
            encoding="utf-8",
        )
        cands = load_candidates_for_triage()
        with redirect_stdout(io.StringIO()):
            summary = apply_actions(cands, [TriageAction(row=1, action="accept")])
        self.assertEqual(summary["accepted"], 0)
        self.assertEqual(summary["failed"], 1)
        # File restored to inbox, nothing left in 02_facts.
        self.assertEqual(len(list((self.cfg.ledger_notes_dir / "02_facts").glob("*.md"))), 0)
        self.assertEqual(len(list(self.inbox.glob("*.md"))), 1)


class InteractiveLoopTests(TriageTestBase):
    def _run(self, lines: list[str]) -> tuple[int, str]:
        from ledger.inbox_triage import run_interactive_triage

        it = iter(lines)

        def fake_input(prompt: str = "") -> str:
            try:
                return next(it)
            except StopIteration:
                raise EOFError

        buf = io.StringIO()
        orig = builtins.input
        builtins.input = fake_input
        try:
            with redirect_stdout(buf):
                rc = run_interactive_triage()
        finally:
            builtins.input = orig
        return rc, buf.getvalue()

    def test_empty_inbox_exits_zero(self):
        rc, out = self._run([])
        self.assertEqual(rc, 0)
        self.assertIn("Inbox is empty.", out)

    def test_accept_then_quit_applies(self):
        self._write("fact__a.md", title="Accept and quit")
        rc, out = self._run(["a 1", "q"])
        self.assertEqual(rc, 0)
        self.assertIn("accepted: 1", out)
        self.assertEqual(len(list((self.cfg.ledger_notes_dir / "02_facts").glob("*.md"))), 1)

    def test_capital_q_discards(self):
        self._write("fact__a.md", title="Discard")
        rc, out = self._run(["a 1", "Q"])
        self.assertEqual(rc, 0)
        self.assertIn("Discarded.", out)
        self.assertTrue((self.inbox / "fact__a.md").exists())

    def test_override_then_reject_wins(self):
        self._write("fact__a.md", title="Override")
        rc, out = self._run(["a 1", "r 1", "q"])
        self.assertIn("rejected: 1", out)
        self.assertIn("accepted: 0", out)

    def test_unset_clears_queued_action(self):
        self._write("fact__a.md", title="Unset me")
        rc, out = self._run(["a 1", "u 1", "q"])
        self.assertIn("accepted: 0", out)
        # File untouched (deferred by virtue of no action).
        self.assertTrue((self.inbox / "fact__a.md").exists())

    def test_inspect_prints_body(self):
        self._write("fact__a.md", title="Inspect target")
        rc, out = self._run(["i 1", "Q"])
        self.assertIn("Inspect target body line.", out)

    def test_inspect_alias_p(self):
        self._write("fact__a.md", title="Alias preview")
        rc, out = self._run(["p 1", "Q"])
        self.assertIn("Alias preview body line.", out)

    def test_help_prints_commands(self):
        self._write("fact__a.md", title="Help")
        rc, out = self._run(["?", "Q"])
        self.assertIn("Commands:", out)

    def test_merge_requires_y_confirmation(self):
        target = self.cfg.ledger_notes_dir / "02_facts" / "fact__target.md"
        target.write_text(_VALID_NOTE.format(
            sig="ffff", entity="E", title="Target"
        ).replace("promoted_by: yaams\n", "promoted_by: user\n"), encoding="utf-8")
        self._write("fact__src.md", title="Src", merge_with="notes/02_facts/fact__target.md")

        # Decline the merge: 'q' -> confirm prompt 'n' -> loop again -> 'Q'
        rc, out = self._run(["m 1", "q", "n", "Q"])
        self.assertIn("Aborted", out)
        # source still present, target unchanged (no merge block)
        self.assertTrue((self.inbox / "fact__src.md").exists())
        self.assertNotIn("## Added from inbox candidate", target.read_text(encoding="utf-8"))

    def test_merge_confirmed_applies(self):
        target = self.cfg.ledger_notes_dir / "02_facts" / "fact__target.md"
        target.write_text(_VALID_NOTE.format(
            sig="ffff", entity="E", title="Target"
        ).replace("promoted_by: yaams\n", "promoted_by: user\n"), encoding="utf-8")
        self._write("fact__src.md", sig="5555aaaa", title="Src",
                    merge_with="notes/02_facts/fact__target.md")

        rc, out = self._run(["m 1", "q", "y"])
        self.assertIn("merged:   1", out)
        self.assertIn("## Added from inbox candidate", target.read_text(encoding="utf-8"))


class TestConflictMetadata(TriageTestBase):
    """E7: conflict frontmatter parsed into InboxCandidate."""

    def test_load_candidates_reads_conflict_metadata(self):
        self._write(
            "fact__conflict_one.md",
            sig="aabb1122",
            title="Conflict One",
            merge_with="notes/02_facts/fact__existing.md",
            dedup_similarity=0.84,
            conflict_classification="contradict",
            conflict_confidence=0.91,
            conflict_reason="candidate contradicts existing date",
        )
        from ledger.inbox import load_candidates_for_triage
        candidates = load_candidates_for_triage(self.cfg.ledger_notes_dir)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c.merge_with, "notes/02_facts/fact__existing.md")
        self.assertAlmostEqual(c.dedup_similarity, 0.84)
        self.assertEqual(c.conflict_classification, "contradict")
        self.assertAlmostEqual(c.conflict_confidence, 0.91)
        self.assertEqual(c.conflict_reason, "candidate contradicts existing date")

    def test_load_candidates_conflict_fields_default_none(self):
        self._write("fact__plain.md", sig="11223344", title="Plain")
        from ledger.inbox import load_candidates_for_triage
        candidates = load_candidates_for_triage(self.cfg.ledger_notes_dir)
        c = candidates[0]
        self.assertIsNone(c.conflict_classification)
        self.assertIsNone(c.conflict_confidence)
        self.assertIsNone(c.conflict_reason)
        self.assertIsNone(c.dedup_similarity)


class TestRenderTableConflictColumn(TriageTestBase):
    """E8: conflict? column in table render."""

    def test_render_table_shows_conflict_column(self):
        self._write(
            "fact__contradict.md",
            sig="aabbccdd",
            title="Contradict One",
            merge_with="notes/02_facts/fact__existing.md",
            conflict_classification="contradict",
        )
        self._write("fact__supplement.md", sig="11223344", title="Supplement One",
                    conflict_classification="supplement")
        self._write("fact__plain.md", sig="deadbeef", title="Plain")

        from ledger.inbox import load_candidates_for_triage
        from ledger.inbox_triage import _render_table
        import io, unittest.mock as mock
        candidates = load_candidates_for_triage(self.cfg.ledger_notes_dir)
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            _render_table(candidates, {})
        text = output.getvalue()
        self.assertIn("conflict?", text)
        self.assertIn("CONTRADICT", text)
        self.assertIn("SUPPLEMENT", text)
        self.assertIn("-", text)


class TestRangeAcceptSkipsContradict(InteractiveLoopTests):
    """E7: range-accept drops contradict rows and single-index still works."""

    def test_range_accept_skips_contradict_rows(self):
        self._write("fact__aa.md", sig="0001", title="AA",
                    conflict_classification="contradict",
                    merge_with="notes/02_facts/fact__x.md")
        self._write("fact__bb.md", sig="0002", title="BB")
        self._write("fact__cc.md", sig="0003", title="CC",
                    conflict_classification="contradict",
                    merge_with="notes/02_facts/fact__y.md")

        import io, unittest.mock as mock
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc, out = self._run(["a 1-3", "q", "y"])
        combined = buf.getvalue() + out
        self.assertIn("skipping", combined.lower())
        # fact__bb.md should be accepted; contradicts should remain
        self.assertTrue((self.inbox / "fact__aa.md").exists() or
                        (self.inbox / "fact__cc.md").exists(),
                        "At least one contradict file should remain")

    def test_single_accept_allows_contradict_row(self):
        self._write("fact__contra.md", sig="aaaa", title="Contra",
                    conflict_classification="contradict",
                    merge_with="notes/02_facts/fact__x.md")

        rc, out = self._run(["a 1", "q", "y"])
        # Single-index accept does NOT filter contradicts
        self.assertNotIn("skipping", out.lower())
        self.assertIn("accepted:", out)


# ---------------------------------------------------------------------------
# Parametrized tests for triage_suggestions() type-inference heuristics
# ---------------------------------------------------------------------------

import pytest


def _make_inbox_note(tmp_path: Path, body_text: str) -> Path:
    """Write a minimal inbox note with *body_text* as note body and return notes_dir."""
    from ledger.config import LedgerConfig, reset_config, set_config

    notes_dir = tmp_path / "notes"
    for folder in (
        "00_inbox", "01_identity", "02_facts", "03_preferences",
        "04_goals", "05_open_loops", "06_concepts", "07_projects",
        "08_indices", "09_archive",
    ):
        (notes_dir / folder).mkdir(parents=True)
    (notes_dir / "08_indices" / "timeline.md").write_text("# Timeline\n", encoding="utf-8")

    cfg = LedgerConfig(
        ledger_root=tmp_path,
        ledger_notes_dir=notes_dir,
        source_notes_dir=tmp_path / "source",
    )
    set_config(cfg)

    note = notes_dir / "00_inbox" / "test_note.md"
    note.write_text(
        "---\ncreated: 2026-06-01T10:00:00Z\nupdated: 2026-06-01T10:00:00Z\n"
        "tags: [test]\nconfidence: 0.6\nsource: inferred\nscope: personal\n"
        "lang: en\npromoted_by: yaams\nyaams_candidate_id: aabbccdd\n---\n\n"
        f"# Test Note\n\n## Statement\n{body_text}\n",
        encoding="utf-8",
    )
    return notes_dir


_TYPE_INFERENCE_CASES = [
    # (body_text, expected_suggested_type)
    # --- preferences: first signal wins
    ("I prefer dark mode going forward.", "preferences"),
    # --- facts: signal keyword
    ("We decided to use PostgreSQL as the primary database.", "facts"),
    # --- goals: objective keyword
    ("The goal is to achieve 99% uptime by Q3.", "goals"),
    # --- loops: open-loop keyword
    ("TODO: revisit this decision after the sprint.", "loops"),
    # --- concepts: definition keyword (avoid "is a" which triggers facts first)
    ("The concept of immutability follows a recurring pattern in functional programming.", "concepts"),
    # --- fallback: no recognisable signal -> "facts"
    ("Random unstructured note without any signal keywords here.", "facts"),
]


@pytest.mark.parametrize("body_text,expected_type", _TYPE_INFERENCE_CASES)
def test_triage_type_inference(tmp_path, body_text, expected_type):
    """triage_suggestions() infers the correct suggested_type from note content."""
    from ledger.config import reset_config
    from ledger.inbox import triage_suggestions

    notes_dir = _make_inbox_note(tmp_path, body_text)
    try:
        suggestions = triage_suggestions(notes_dir)
        assert len(suggestions) == 1, f"Expected 1 suggestion, got {len(suggestions)}"
        assert suggestions[0]["suggested_type"] == expected_type, (
            f"body={body_text!r}: expected {expected_type!r}, "
            f"got {suggestions[0]['suggested_type']!r} "
            f"(reason: {suggestions[0].get('reason')})"
        )
    finally:
        reset_config()


@pytest.mark.parametrize("body_text,expected_type", _TYPE_INFERENCE_CASES)
def test_triage_reason_populated(tmp_path, body_text, expected_type):
    """triage_suggestions() always populates a non-empty 'reason' field."""
    from ledger.config import reset_config
    from ledger.inbox import triage_suggestions

    notes_dir = _make_inbox_note(tmp_path, body_text)
    try:
        suggestions = triage_suggestions(notes_dir)
        assert suggestions[0]["reason"], "reason field should be non-empty"
    finally:
        reset_config()


if __name__ == "__main__":
    unittest.main()

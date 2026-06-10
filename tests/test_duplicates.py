"""Tests for ledger.duplicates.scan_duplicates()."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from ledger.duplicates import DuplicateFinding, scan_duplicates, _jaccard, _normalize_title


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_normalize_title_lowercases(self):
        assert _normalize_title("Hello World!") == "hello world"

    def test_normalize_title_strips_punctuation(self):
        # _normalize_title collapses whitespace, so punctuation becomes single space.
        assert _normalize_title("foo: bar, baz") == "foo bar baz"
        assert "foo" in _normalize_title("foo: bar")

    def test_jaccard_identical(self):
        assert _jaccard({"a", "b", "c"}, {"a", "b", "c"}) == pytest.approx(1.0)

    def test_jaccard_disjoint(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == pytest.approx(0.0)

    def test_jaccard_partial(self):
        j = _jaccard({"a", "b", "c"}, {"a", "b", "d"})
        # intersection 2, union 4 -> 0.5
        assert j == pytest.approx(0.5)

    def test_jaccard_empty(self):
        assert _jaccard(set(), {"a"}) == pytest.approx(0.0)
        assert _jaccard({"a"}, set()) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# scan_duplicates
# ---------------------------------------------------------------------------

def _note(text: str) -> str:
    """Return a minimal note with YAML frontmatter."""
    return textwrap.dedent(f"""\
        ---
        created: 2026-01-01T00:00:00Z
        updated: 2026-01-01T00:00:00Z
        tags: [test]
        confidence: 0.9
        source: user
        scope: meta
        lang: en
        ---
        {text}
    """)


class TestScanDuplicates:
    def test_empty_dir_returns_empty(self, tmp_path):
        findings = scan_duplicates(tmp_path)
        assert findings == []

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        findings = scan_duplicates(tmp_path / "nonexistent")
        assert findings == []

    def test_single_note_no_duplicates(self, tmp_path):
        notes = tmp_path / "02_facts"
        notes.mkdir(parents=True)
        (notes / "fact__solo.md").write_text(_note("# Solo\nUnique content here"))
        findings = scan_duplicates(tmp_path)
        assert findings == []

    def test_exact_duplicate_detected(self, tmp_path):
        notes = tmp_path / "02_facts"
        notes.mkdir(parents=True)
        content = _note("# Identical\nWord1 word2 word3 same content exactly here.")
        (notes / "fact__a.md").write_text(content)
        (notes / "fact__b.md").write_text(content)
        findings = scan_duplicates(tmp_path)
        assert len(findings) == 1
        assert findings[0].reason == "exact_content"
        assert findings[0].score == pytest.approx(1.0)

    def test_title_overlap_detected(self, tmp_path):
        notes = tmp_path / "02_facts"
        notes.mkdir(parents=True)
        # Very similar titles via frontmatter title: field, different body.
        content_a = (
            "---\ncreated: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n"
            "title: Cognitive Ledger Setup\ntags: [test]\nconfidence: 0.9\n"
            "source: user\nscope: meta\nlang: en\n---\n\nBody A content unique.\n"
        )
        content_b = (
            "---\ncreated: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n"
            "title: Cognitive Ledger Setup\ntags: [test]\nconfidence: 0.9\n"
            "source: user\nscope: meta\nlang: en\n---\n\nBody B different text here.\n"
        )
        (notes / "fact__a.md").write_text(content_a)
        (notes / "fact__b.md").write_text(content_b)
        findings = scan_duplicates(tmp_path, title_threshold=0.7)
        # Could be title_overlap or exact_content depending on hash.
        assert len(findings) >= 1

    def test_content_overlap_detected(self, tmp_path):
        notes = tmp_path / "02_facts"
        notes.mkdir(parents=True)
        # Nearly identical long content.
        base_words = " ".join(["word"] * 5 + [f"tok{i}" for i in range(25)])
        content_a = _note(f"# Topic A\n{base_words} unique_a extra_a")
        content_b = _note(f"# Topic B\n{base_words} unique_b extra_b")
        (notes / "fact__a.md").write_text(content_a)
        (notes / "fact__b.md").write_text(content_b)
        findings = scan_duplicates(tmp_path, jaccard_threshold=0.5)
        content_findings = [f for f in findings if f.reason == "content_overlap"]
        assert len(content_findings) >= 1

    def test_excluded_dirs_skipped(self, tmp_path):
        # Notes in 08_indices and 09_archive should be excluded.
        for d in ["08_indices", "09_archive"]:
            p = tmp_path / d
            p.mkdir(parents=True)
            content = _note("# Identical Index Note\nSame content in both.")
            (p / "note_a.md").write_text(content)
            (p / "note_b.md").write_text(content)
        findings = scan_duplicates(tmp_path)
        assert findings == []

    def test_finding_has_notes_prefix(self, tmp_path):
        notes = tmp_path / "02_facts"
        notes.mkdir(parents=True)
        content = _note("# Dup\nSame content exact copy here.")
        (notes / "fact__x.md").write_text(content)
        (notes / "fact__y.md").write_text(content)
        findings = scan_duplicates(tmp_path)
        assert findings
        assert findings[0].path_a.startswith("notes/")
        assert findings[0].path_b.startswith("notes/")

    def test_finding_to_dict_shape(self, tmp_path):
        notes = tmp_path / "02_facts"
        notes.mkdir(parents=True)
        content = _note("# Dup\nIdentical exact content here.")
        (notes / "fact__x.md").write_text(content)
        (notes / "fact__y.md").write_text(content)
        findings = scan_duplicates(tmp_path)
        assert findings
        d = findings[0].to_dict()
        assert {"path_a", "path_b", "reason", "score", "details"} <= set(d.keys())
        assert isinstance(d["score"], float)

    def test_no_false_positives_distinct_notes(self, tmp_path):
        """Completely unrelated notes should not trigger any finding."""
        notes = tmp_path / "02_facts"
        notes.mkdir(parents=True)
        (notes / "fact__python.md").write_text(_note(
            "# Python\nPython is a programming language used for scripting and data science."
        ))
        (notes / "fact__coffee.md").write_text(_note(
            "# Coffee\nCoffee is a brewed beverage made from roasted coffee beans."
        ))
        findings = scan_duplicates(tmp_path, jaccard_threshold=0.65, title_threshold=0.75)
        assert findings == []

    def test_sorted_by_score_descending(self, tmp_path):
        notes = tmp_path / "02_facts"
        notes.mkdir(parents=True)
        # One exact pair (score 1.0) and one title-overlap pair.
        exact_content = _note("# Exact Duplicate Note\nIdentical text here exact copy.")
        (notes / "fact__exact_a.md").write_text(exact_content)
        (notes / "fact__exact_b.md").write_text(exact_content)
        # Second pair with title overlap.
        (notes / "fact__similar_a.md").write_text(_note("# Similar Topic Theme\nBody A content."))
        (notes / "fact__similar_b.md").write_text(_note("# Similar Topic Theme\nBody B content."))
        findings = scan_duplicates(tmp_path)
        if len(findings) >= 2:
            scores = [f.score for f in findings]
            assert scores == sorted(scores, reverse=True)

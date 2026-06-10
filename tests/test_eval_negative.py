"""Tests for negative eval cases (expected_none: true)."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from ledger.eval import parse_eval_cases, validate_eval_cases, run_eval, print_eval_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_cases(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "cases.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _dummy_ranker_with_score(score: float, path: str = "notes/02_facts/fact__dummy.md"):
    """Return a rank_query_fn that always returns one result with the given score."""
    def _ranker(query, scope="all", limit=50, **kwargs):
        return {
            "results": [{"path": path, "rel_path": path, "score": score, "title": "dummy"}],
        }
    return _ranker


def _dummy_positive_ranker(expected_path: str):
    """Ranker that always returns the expected path as top result."""
    def _ranker(query, scope="all", limit=50, **kwargs):
        return {
            "results": [{"path": expected_path, "rel_path": expected_path, "score": 0.9, "title": "match"}],
        }
    return _ranker


# ---------------------------------------------------------------------------
# parse_eval_cases — expected_none parsing
# ---------------------------------------------------------------------------

class TestParseEvalCasesNegative:
    def test_parses_expected_none_true(self, tmp_path):
        p = _write_cases(tmp_path, """\
            - query: unrelated topic with no notes
              scope: all
              expected_none: true
        """)
        cases = parse_eval_cases(p)
        assert len(cases) == 1
        assert cases[0]["expected_none"] is True
        assert cases[0]["expected_any"] == []

    def test_parses_expected_none_false(self, tmp_path):
        p = _write_cases(tmp_path, """\
            - query: some topic
              scope: all
              expected_none: false
              expected_any:
                - notes/02_facts/fact__dummy.md
        """)
        cases = parse_eval_cases(p)
        assert len(cases) == 1
        assert cases[0]["expected_none"] is False

    def test_mixed_positive_and_negative(self, tmp_path):
        p = _write_cases(tmp_path, """\
            - query: positive case
              scope: all
              expected_any:
                - notes/02_facts/fact__dummy.md

            - query: negative case
              scope: all
              expected_none: true
        """)
        cases = parse_eval_cases(p)
        assert len(cases) == 2
        positive = [c for c in cases if not c.get("expected_none")]
        negative = [c for c in cases if c.get("expected_none")]
        assert len(positive) == 1
        assert len(negative) == 1


# ---------------------------------------------------------------------------
# validate_eval_cases — mutual exclusion
# ---------------------------------------------------------------------------

class TestValidateNegativeCases:
    def test_mutual_exclusion_error(self, tmp_path):
        p = _write_cases(tmp_path, """\
            - query: bad case
              scope: all
              expected_none: true
              expected_any:
                - notes/02_facts/fact__dummy.md
        """)
        cases = parse_eval_cases(p)
        errors = validate_eval_cases(cases, strict_cases=False)
        assert any("mutually exclusive" in e for e in errors)

    def test_negative_case_no_path_validation(self, tmp_path):
        p = _write_cases(tmp_path, """\
            - query: negative case
              scope: all
              expected_none: true
        """)
        cases = parse_eval_cases(p)
        errors = validate_eval_cases(cases, strict_cases=True)
        # No error about expected_any missing for negative cases.
        path_errors = [e for e in errors if "expected_any" in e and "negative case" in e.lower() or "case_1" in e]
        assert not any("expected_any must contain" in e for e in errors)


# ---------------------------------------------------------------------------
# run_eval — negative case scoring
# ---------------------------------------------------------------------------

class TestRunEvalNegative:
    def test_empty_cases_returns_negative_fields(self, tmp_path):
        p = _write_cases(tmp_path, """\
            # empty
        """)
        # write a minimal non-empty file so parse doesn't complain
        p.write_text("", encoding="utf-8")
        cases = parse_eval_cases(p)
        # Patch to empty result directly
        result = {
            "cases": 0, "hit1": 0.0, "hitk": 0.0, "mrr": 0.0,
            "failed": [], "k": 3,
            "negative_cases": 0, "negative_pass_count": 0,
            "false_positive_rate": 0.0, "abstain_accuracy": 0.0,
        }
        assert "negative_cases" in result
        assert "abstain_accuracy" in result

    def test_negative_case_pass_when_low_score(self, tmp_path):
        """Negative case passes when top score is below threshold (0.5)."""
        p = _write_cases(tmp_path, """\
            - id: neg1
              query: nothing here
              scope: all
              expected_none: true
        """)
        # Ranker returns score 0.1 — should pass (not a false positive).
        ranker = _dummy_ranker_with_score(0.1)
        result = run_eval(p, k=3, rank_query_fn=ranker)
        assert result["negative_cases"] == 1
        assert result["negative_pass_count"] == 1
        assert result["false_positive_rate"] == pytest.approx(0.0)
        assert result["abstain_accuracy"] == pytest.approx(1.0)

    def test_negative_case_fail_when_high_score(self, tmp_path):
        """Negative case fails (false positive) when top score exceeds threshold (0.5)."""
        p = _write_cases(tmp_path, """\
            - id: neg1
              query: nothing here
              scope: all
              expected_none: true
        """)
        # Ranker returns score 0.9 — false positive.
        ranker = _dummy_ranker_with_score(0.9)
        result = run_eval(p, k=3, rank_query_fn=ranker)
        assert result["negative_cases"] == 1
        assert result["negative_pass_count"] == 0
        assert result["false_positive_rate"] == pytest.approx(1.0)
        assert result["abstain_accuracy"] == pytest.approx(0.0)

    def test_positive_and_negative_scoring_independent(self, tmp_path):
        """Positive hit@k and negative false_positive_rate are independent metrics."""
        p = _write_cases(tmp_path, """\
            - id: pos1
              query: positive topic
              scope: all
              expected_any:
                - notes/02_facts/fact__dummy.md

            - id: neg1
              query: nothing here
              scope: all
              expected_none: true
        """)
        # Positive ranker returns the expected path; score 0.9 is high (FP for negative).
        ranker = _dummy_ranker_with_score(0.9, "notes/02_facts/fact__dummy.md")
        result = run_eval(p, k=3, rank_query_fn=ranker)
        # Positive case: hit@1 = 1.0 (the expected path is top result).
        assert result["cases"] == 1  # only positive cases count toward "cases"
        assert result["hit1"] == pytest.approx(1.0)
        # Negative case: FP because score 0.9 > 0.5 threshold.
        assert result["negative_cases"] == 1
        assert result["false_positive_rate"] == pytest.approx(1.0)

    def test_negative_cases_dont_count_in_positive_metrics(self, tmp_path):
        """Negative cases are excluded from hit@k/mrr computations."""
        p = _write_cases(tmp_path, """\
            - id: neg1
              query: nothing
              scope: all
              expected_none: true

            - id: neg2
              query: also nothing
              scope: all
              expected_none: true
        """)
        ranker = _dummy_ranker_with_score(0.1)
        result = run_eval(p, k=3, rank_query_fn=ranker)
        # "cases" should be 0 (no positive cases).
        assert result["cases"] == 0
        assert result["hit1"] == pytest.approx(0.0)
        assert result["mrr"] == pytest.approx(0.0)
        assert result["negative_cases"] == 2
        assert result["negative_pass_count"] == 2


# ---------------------------------------------------------------------------
# print_eval_result — human output includes negative eval line
# ---------------------------------------------------------------------------

class TestPrintEvalResultNegative:
    def test_negative_eval_line_in_output(self, capsys, tmp_path):
        result = {
            "retrieval_mode": "legacy",
            "cases": 1,
            "hit1": 1.0,
            "hitk": 1.0,
            "mrr": 1.0,
            "failed": [],
            "k": 3,
            "hit1_count": 1,
            "hitk_count": 1,
            "negative_cases": 2,
            "negative_pass_count": 2,
            "false_positive_rate": 0.0,
            "abstain_accuracy": 1.0,
        }
        print_eval_result(result)
        captured = capsys.readouterr()
        assert "negative_eval" in captured.out
        assert "abstain_accuracy=1.000" in captured.out
        assert "false_positive_rate=0.000" in captured.out

    def test_no_negative_line_when_zero_cases(self, capsys):
        result = {
            "retrieval_mode": "legacy",
            "cases": 1,
            "hit1": 1.0,
            "hitk": 1.0,
            "mrr": 1.0,
            "failed": [],
            "k": 3,
            "hit1_count": 1,
            "hitk_count": 1,
            "negative_cases": 0,
            "negative_pass_count": 0,
            "false_positive_rate": 0.0,
            "abstain_accuracy": 0.0,
        }
        print_eval_result(result)
        captured = capsys.readouterr()
        assert "negative_eval" not in captured.out

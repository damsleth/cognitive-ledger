"""The retrieval gate reads the in-corpus copy of the fixture cases."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def test_fixture_case_copies_are_identical():
    outer = (ROOT / "retrieval_eval_cases.yaml").read_text(encoding="utf-8")
    inner = (ROOT / "corpus" / "notes" / "08_indices" / "retrieval_eval_cases.yaml").read_text(encoding="utf-8")
    assert outer == inner, "edit both copies - scripts/ab_gate.sh measures the in-corpus one"

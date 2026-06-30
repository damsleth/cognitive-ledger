"""Shape test for contradiction fixture files T2 and T3.

Run with:  .venv/bin/python -m pytest tests/ -k contradiction_fixtures
"""
from __future__ import annotations

from pathlib import Path

import yaml

FIXTURES = Path(__file__).parent / "fixtures"
T2_PATH = FIXTURES / "contradiction_t2.yaml"
T3_PATH = FIXTURES / "contradiction_t3.yaml"

REQUIRED_KEYS = {"note_a", "note_b", "relation", "attribute", "lang"}


def _load(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_contradiction_fixtures_t2_shape():
    entries = _load(T2_PATH)
    assert entries, "T2 fixture is empty"

    for e in entries:
        missing = REQUIRED_KEYS - e.keys()
        assert not missing, f"{e.get('id', '?')!r} missing keys: {missing}"

    positives = [e for e in entries if e["relation"] == "contradiction"]
    negatives = [e for e in entries if e["relation"] == "compatible"]

    assert len(positives) >= 30, f"T2 needs ≥30 positive pairs, got {len(positives)}"
    assert len(negatives) >= 50, f"T2 needs ≥50 negative pairs, got {len(negatives)}"

    # lang:no subset must be marked expected_nli_fail: true
    no_positives = [e for e in positives if e["lang"] == "no"]
    assert no_positives, "T2 must include at least one lang:no positive pair"
    for e in no_positives:
        assert e.get("expected_nli_fail") is True, (
            f"{e.get('id', '?')!r}: lang:no positive pairs must have expected_nli_fail: true"
        )


def test_contradiction_fixtures_t3_shape():
    entries = _load(T3_PATH)
    assert entries, "T3 fixture is empty"

    for e in entries:
        missing = REQUIRED_KEYS - e.keys()
        assert not missing, f"{e.get('id', '?')!r} missing keys: {missing}"
        assert "type" in e, f"{e.get('id', '?')!r} missing 'type' key"

    type1 = [e for e in entries if e["type"] == "co_referential"]
    type2 = [e for e in entries if e["type"] == "propagated"]

    assert len(type1) >= 10, f"T3 needs ≥10 Type-I (co_referential), got {len(type1)}"
    assert len(type2) >= 10, f"T3 needs ≥10 Type-II (propagated), got {len(type2)}"

    for e in type2:
        assert "dependency" in e, f"{e.get('id', '?')!r}: propagated entries must have 'dependency'"

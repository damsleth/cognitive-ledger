"""eval_t2 splits precision/recall by language (plan 08 gate)."""

from __future__ import annotations

from ledger.contradiction import eval_t2


def test_precision_recall_per_language(tmp_path):
    fixture = tmp_path / "t2.yaml"
    fixture.write_text(
        """
- {id: a, note_a: x1, note_b: y1, relation: contradiction, attribute: r, lang: en}
- {id: b, note_a: x2, note_b: y2, relation: contradiction, attribute: r, lang: en}
- {id: c, note_a: x3, note_b: y3, relation: compatible, attribute: r, lang: en}
- {id: d, note_a: x4, note_b: y4, relation: contradiction, attribute: r, lang: "no"}
""",
        encoding="utf-8",
    )
    scores = {"x1": 0.9, "x2": 0.5, "x3": 0.9, "x4": 0.1}

    report = eval_t2(fixture, thresholds=(0.6,), score_fn=lambda a, b: scores[a])

    en = report["en"]["thresholds"]["0.60"]
    assert (en["tp"], en["fp"], en["fn"]) == (1, 1, 1)
    assert en["precision"] == 0.5 and en["recall"] == 0.5
    no = report["no"]["thresholds"]["0.60"]
    assert no["recall"] == 0.0 and no["precision"] is None

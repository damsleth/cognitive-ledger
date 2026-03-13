from __future__ import annotations

import json

from ledger import eval as eval_lib


def _sample_result() -> dict[str, object]:
    return {
        "cases": 3,
        "k": 3,
        "retrieval_mode": "legacy",
        "hit1": 0.5,
        "hitk": 0.75,
        "mrr": 0.625,
        "hit1_count": 1,
        "hitk_count": 2,
        "failed": [{"id": "case_3"}],
    }


def test_build_and_write_baseline_snapshot(tmp_path):
    result = _sample_result()
    generated_at = "2026-03-11T12:00:00Z"
    cases_path = tmp_path / "cases.yaml"
    cases_path.write_text("- query: test\n", encoding="utf-8")

    snapshot = eval_lib.build_baseline_snapshot(
        result,
        cases_path=cases_path,
        generated_at=generated_at,
    )
    assert snapshot["generated_at"] == generated_at
    assert snapshot["cases_path"] == str(cases_path.resolve())

    output_path = tmp_path / "baseline.json"
    written = eval_lib.write_baseline_snapshot(
        result,
        cases_path=cases_path,
        output_path=output_path,
        generated_at=generated_at,
    )
    assert written == snapshot
    assert json.loads(output_path.read_text(encoding="utf-8"))["cases"] == 3


def test_eval_result_to_json_and_baseline_formatting(tmp_path):
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps({"hit1": 0.4, "hitk": 0.7, "mrr": 0.6, "k": 3, "cases": 3}),
        encoding="utf-8",
    )
    result = _sample_result()

    payload = eval_lib.eval_result_to_json(
        result,
        default_k=3,
        default_retrieval_mode="legacy",
        embed_backend="local",
        embed_model="fake-model",
        baseline_path=baseline_path,
        baseline_written="notes/08_indices/baseline.json",
    )

    assert payload["embed_backend"] == "local"
    assert payload["embed_model"] == "fake-model"
    assert payload["baseline_comparison"]["available"] is True
    assert payload["baseline_written"] == "notes/08_indices/baseline.json"

    formatted = eval_lib.format_baseline_comparison(payload["baseline_comparison"], k=3)
    assert formatted.startswith("baseline_comparison:")

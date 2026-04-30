"""Coverage for ledger/rerank.py and ledger/ab_charts.py.

These modules sit on heavy optional deps (sentence-transformers,
matplotlib) so we test the parts that don't require them and the
graceful-failure paths for the ones that do.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class RerankPureFunctionTests(unittest.TestCase):
    def test_candidate_text_combines_title_and_body(self):
        from ledger.rerank import candidate_text
        out = candidate_text("Title here", "body content")
        self.assertEqual(out, "Title here\nbody content")

    def test_candidate_text_handles_missing_title(self):
        from ledger.rerank import candidate_text
        self.assertEqual(candidate_text("", "just body"), "just body")
        self.assertEqual(candidate_text(None, "just body"), "just body")  # type: ignore[arg-type]

    def test_candidate_text_truncates_to_max_chars(self):
        from ledger.rerank import candidate_text
        body = "x" * 4000
        out = candidate_text("T", body, max_chars=100)
        self.assertEqual(len(out), 100)

    def test_candidate_text_no_truncation_when_max_chars_zero(self):
        from ledger.rerank import candidate_text
        body = "y" * 5000
        out = candidate_text("", body, max_chars=0)
        self.assertEqual(len(out), 5000)

    def test_rerank_pairs_returns_empty_list_for_empty_input(self):
        from ledger.rerank import rerank_pairs
        self.assertEqual(rerank_pairs("q", [], model_name="any"), [])

    def test_rerank_timer_ms_returns_positive_float(self):
        import time
        from ledger.rerank import rerank_timer_ms
        start = time.perf_counter()
        out = rerank_timer_ms(start)
        self.assertIsInstance(out, float)
        self.assertGreaterEqual(out, 0.0)

    def test_reset_reranker_cache_clears_state(self):
        from ledger import rerank
        rerank._RERANKER_CACHE["fake"] = object()
        rerank.reset_reranker_cache()
        self.assertEqual(rerank._RERANKER_CACHE, {})

    def test_get_reranker_returns_cached_when_present(self):
        from ledger import rerank
        sentinel = object()
        rerank._RERANKER_CACHE["my-model::512"] = sentinel
        try:
            self.assertIs(rerank.get_reranker("my-model"), sentinel)
        finally:
            rerank.reset_reranker_cache()

    def test_get_reranker_raises_when_sentence_transformers_missing(self):
        from ledger import rerank
        rerank.reset_reranker_cache()

        # Force an ImportError on `from sentence_transformers import CrossEncoder`
        original_st = sys.modules.pop("sentence_transformers", None)
        sys.modules["sentence_transformers"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises(RuntimeError) as ctx:
                rerank.get_reranker("does-not-matter")
            self.assertIn("sentence-transformers", str(ctx.exception))
        finally:
            if original_st is not None:
                sys.modules["sentence_transformers"] = original_st
            else:
                sys.modules.pop("sentence_transformers", None)

    def test_rerank_pairs_invokes_cached_model(self):
        from ledger import rerank
        rerank.reset_reranker_cache()

        class FakeModel:
            def __init__(self):
                self.calls = []
            def predict(self, pairs, **kwargs):
                self.calls.append((pairs, kwargs))
                return [0.5 for _ in pairs]

        fake = FakeModel()
        rerank._RERANKER_CACHE["fake-model::512"] = fake
        try:
            scores = rerank.rerank_pairs("query", [("q", "doc1"), ("q", "doc2")], model_name="fake-model")
        finally:
            rerank.reset_reranker_cache()

        self.assertEqual(scores, [0.5, 0.5])
        self.assertEqual(len(fake.calls), 1)


class AbChartsTests(unittest.TestCase):
    def test_main_raises_when_series_file_missing(self):
        from ledger.ab_charts import main
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                main(root=Path(tmp))

    def test_main_renders_when_series_present(self):
        from ledger import ab_charts
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ab_dir = root / "docs" / "ab"
            ab_dir.mkdir(parents=True)
            series = [
                {
                    "label": "run-1",
                    "generated_at": "2026-04-01T00:00:00Z",
                    "baseline_mrr": 0.5, "candidate_mrr": 0.6,
                    "baseline_hitk": 0.7, "candidate_hitk": 0.75,
                    "baseline_query_p95_ms": 100.0, "candidate_query_p95_ms": 90.0,
                },
            ]
            (ab_dir / "performance_series.json").write_text(json.dumps(series), encoding="utf-8")

            # Use real matplotlib (it's available). Set MPLBACKEND to avoid GUI.
            import os
            os.environ["MPLBACKEND"] = "Agg"
            from ledger import ab_charts
            ab_charts.main(root=root)

            charts_dir = ab_dir / "charts"
            self.assertTrue((charts_dir / "mrr_over_time.png").exists())
            self.assertTrue((charts_dir / "hitk_over_time.png").exists())
            self.assertTrue((charts_dir / "p95_query_over_time.png").exists())

    def test_render_raises_systemexit_when_matplotlib_missing(self):
        from ledger import ab_charts

        # Force an ImportError on matplotlib.pyplot
        original_mpl = sys.modules.pop("matplotlib.pyplot", None)
        sys.modules["matplotlib.pyplot"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises(SystemExit):
                ab_charts.render(
                    series=[{"label": "x", "baseline_mrr": 0.5, "candidate_mrr": 0.6}],
                    metric=ab_charts.METRICS[0],
                    charts_dir=Path("/tmp"),
                    root=Path("/tmp"),
                )
        finally:
            if original_mpl is not None:
                sys.modules["matplotlib.pyplot"] = original_mpl
            else:
                sys.modules.pop("matplotlib.pyplot", None)


if __name__ == "__main__":
    unittest.main()

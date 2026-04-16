import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ledger import ab as ab_lib

ROOT = Path(__file__).resolve().parents[1]
LEDGER_AB_PATH = ROOT / "scripts" / "ledger_ab"

# Ensure ledger package is importable
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_ledger_ab_module():
    """Load the extensionless scripts/ledger_ab as a module."""
    loader = importlib.machinery.SourceFileLoader("ledger_ab_module", str(LEDGER_AB_PATH))
    spec = importlib.util.spec_from_file_location("ledger_ab_module", str(LEDGER_AB_PATH), loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LedgerABDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger_ab = load_ledger_ab_module()

    def test_regression_if_any_quality_metric_drops(self):
        result = self.ledger_ab.decide_outcome(
            baseline_quality={"hit1": 0.9, "hitk": 0.95, "mrr": 0.92},
            candidate_quality={"hit1": 0.89, "hitk": 0.95, "mrr": 0.92},
            baseline_eval_p95_ms=100.0,
            candidate_eval_p95_ms=50.0,
            baseline_query_p95_ms=100.0,
            candidate_query_p95_ms=50.0,
            latency_tol_pct=0.05,
            latency_tol_ms=2.0,
        )
        self.assertEqual(result["decision"], "regression")
        self.assertEqual(result["exit_code"], self.ledger_ab.EXIT_REGRESSION)

    def test_beneficial_if_quality_improves(self):
        result = self.ledger_ab.decide_outcome(
            baseline_quality={"hit1": 0.8, "hitk": 0.9, "mrr": 0.85},
            candidate_quality={"hit1": 0.81, "hitk": 0.9, "mrr": 0.85},
            baseline_eval_p95_ms=100.0,
            candidate_eval_p95_ms=120.0,
            baseline_query_p95_ms=100.0,
            candidate_query_p95_ms=120.0,
            latency_tol_pct=0.05,
            latency_tol_ms=2.0,
        )
        self.assertEqual(result["decision"], "beneficial")
        self.assertEqual(result["exit_code"], self.ledger_ab.EXIT_BENEFICIAL)

    def test_tie_beneficial_when_latency_within_threshold(self):
        result = self.ledger_ab.decide_outcome(
            baseline_quality={"hit1": 0.8, "hitk": 0.9, "mrr": 0.85},
            candidate_quality={"hit1": 0.8, "hitk": 0.9, "mrr": 0.85},
            baseline_eval_p95_ms=100.0,
            candidate_eval_p95_ms=106.9,
            baseline_query_p95_ms=80.0,
            candidate_query_p95_ms=85.9,
            latency_tol_pct=0.05,
            latency_tol_ms=2.0,
        )
        self.assertEqual(result["decision"], "beneficial")
        self.assertEqual(result["exit_code"], self.ledger_ab.EXIT_BENEFICIAL)

    def test_tie_neutral_when_latency_exceeds_threshold(self):
        result = self.ledger_ab.decide_outcome(
            baseline_quality={"hit1": 0.8, "hitk": 0.9, "mrr": 0.85},
            candidate_quality={"hit1": 0.8, "hitk": 0.9, "mrr": 0.85},
            baseline_eval_p95_ms=100.0,
            candidate_eval_p95_ms=108.1,
            baseline_query_p95_ms=80.0,
            candidate_query_p95_ms=84.0,
            latency_tol_pct=0.05,
            latency_tol_ms=2.0,
        )
        self.assertEqual(result["decision"], "neutral")
        self.assertEqual(result["exit_code"], self.ledger_ab.EXIT_NEUTRAL)

    def test_retrieval_modes_include_semantic_hybrid(self):
        self.assertIn("semantic_hybrid", self.ledger_ab.RETRIEVAL_MODES)

    def test_script_delegates_decision_logic_to_library(self):
        self.assertIs(self.ledger_ab.decide_outcome, ab_lib.decide_outcome)


class LedgerABFingerprintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger_ab = load_ledger_ab_module()

    def _build_fixture_repo(self, root: Path, aliases_content: str, case_query: str) -> None:
        directories = [
            "notes/02_facts",
            "notes/03_preferences",
            "notes/04_goals",
            "notes/05_open_loops",
            "notes/06_concepts",
            "notes/08_indices",
        ]
        for directory in directories:
            (root / directory).mkdir(parents=True, exist_ok=True)

        (root / "notes/02_facts/fact__one.md").write_text("# fact one\n", encoding="utf-8")
        (root / "notes/03_preferences/pref__one.md").write_text("# pref one\n", encoding="utf-8")
        (root / "notes/04_goals/goal__one.md").write_text("# goal one\n", encoding="utf-8")
        (root / "notes/05_open_loops/loop__one.md").write_text("# loop one\n", encoding="utf-8")
        (root / "notes/06_concepts/concept__one.md").write_text("# concept one\n", encoding="utf-8")
        (root / "notes/08_indices/aliases.json").write_text(aliases_content, encoding="utf-8")

        cases = (
            f'- query: "{case_query}"\n'
            '  scope: "all"\n'
            "  expected_any:\n"
            '    - "notes/02_facts/fact__one.md"\n'
        )
        (root / "notes/08_indices/retrieval_eval_cases.yaml").write_text(cases, encoding="utf-8")

    def _build_fixture_corpus(self, root: Path, aliases_content: str, case_query: str) -> None:
        directories = [
            "02_facts",
            "03_preferences",
            "04_goals",
            "05_open_loops",
            "06_concepts",
            "08_indices",
        ]
        for directory in directories:
            (root / directory).mkdir(parents=True, exist_ok=True)

        (root / "02_facts/fact__one.md").write_text("# fact one\n", encoding="utf-8")
        (root / "03_preferences/pref__one.md").write_text("# pref one\n", encoding="utf-8")
        (root / "04_goals/goal__one.md").write_text("# goal one\n", encoding="utf-8")
        (root / "05_open_loops/loop__one.md").write_text("# loop one\n", encoding="utf-8")
        (root / "06_concepts/concept__one.md").write_text("# concept one\n", encoding="utf-8")
        (root / "08_indices/aliases.json").write_text(aliases_content, encoding="utf-8")

        cases = (
            f'- query: "{case_query}"\n'
            '  scope: "all"\n'
            "  expected_any:\n"
            '    - "notes/02_facts/fact__one.md"\n'
        )
        (root / "08_indices/retrieval_eval_cases.yaml").write_text(cases, encoding="utf-8")

    def test_same_input_produces_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_fixture_repo(root, '{"alex": ["example_user"]}\n', "hello")

            first = self.ledger_ab.compute_corpus_fingerprint(
                root, Path("notes/08_indices/retrieval_eval_cases.yaml")
            )
            second = self.ledger_ab.compute_corpus_fingerprint(
                root, Path("notes/08_indices/retrieval_eval_cases.yaml")
            )

            self.assertEqual(first["fingerprint"], second["fingerprint"])

    def test_changed_input_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._build_fixture_repo(root, '{"alex": ["example_user"]}\n', "hello")
            first = self.ledger_ab.compute_corpus_fingerprint(
                root, Path("notes/08_indices/retrieval_eval_cases.yaml")
            )

            (root / "notes/08_indices/aliases.json").write_text(
                '{"alex": ["example_user", "demo_user"]}\n', encoding="utf-8"
            )
            second = self.ledger_ab.compute_corpus_fingerprint(
                root, Path("notes/08_indices/retrieval_eval_cases.yaml")
            )

            self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_script_delegates_fingerprint_logic_to_library(self):
        self.assertIs(self.ledger_ab.compute_corpus_fingerprint, ab_lib.compute_corpus_fingerprint)

    def test_external_corpus_root_without_notes_wrapper_is_supported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            corpus_root = Path(temp_dir) / "ledger-notes"
            self._build_fixture_corpus(corpus_root, '{"alex": ["example_user"]}\n', "hello")

            first = self.ledger_ab.compute_corpus_fingerprint(
                corpus_root, Path("notes/08_indices/retrieval_eval_cases.yaml")
            )
            second = self.ledger_ab.compute_corpus_fingerprint(
                corpus_root, Path("notes/08_indices/retrieval_eval_cases.yaml")
            )

            self.assertTrue(self.ledger_ab.is_corpus_root(corpus_root))
            self.assertEqual(first["fingerprint"], second["fingerprint"])


class LedgerABRetrievalModePassThroughTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger_ab = load_ledger_ab_module()

    def test_script_delegates_probe_helpers_to_library(self):
        self.assertIs(self.ledger_ab.run_probe_for_side, ab_lib.run_probe_for_side)
        self.assertIs(
            self.ledger_ab.maybe_build_semantic_index,
            ab_lib.maybe_build_semantic_index,
        )

    def test_run_quality_eval_passes_retrieval_mode(self):
        class FakeModule:
            def __init__(self):
                self.seen_modes = []

            def run_eval(self, _cases_path, k=3, retrieval_mode="legacy"):
                self.seen_modes.append(retrieval_mode)
                return {"hit1": 0.1, "hitk": 0.2, "mrr": 0.3, "cases": 1, "k": k}

        module = FakeModule()
        _ = self.ledger_ab.run_quality_eval(module, Path("/tmp/cases.yaml"), 3, "two_stage")
        self.assertEqual(module.seen_modes, ["two_stage"])

    def test_probe_query_latency_passes_retrieval_mode(self):
        class FakeModule:
            def __init__(self):
                self.seen_modes = []

            def parse_eval_cases(self, _cases_path):
                return [{"query": "q", "scope": "all"}]

            def rank_query(self, _query, scope="all", limit=50, retrieval_mode="legacy"):
                self.seen_modes.append(retrieval_mode)
                return {"results": [], "scope": scope, "limit": limit}

        module = FakeModule()
        _ = self.ledger_ab.probe_query_latency(module, Path("/tmp/cases.yaml"), 2, "two_stage")
        self.assertEqual(module.seen_modes, ["two_stage", "two_stage"])

    def test_invoke_run_eval_passes_embed_backend_and_model(self):
        class FakeModule:
            def __init__(self):
                self.seen = []

            def run_eval(
                self,
                _cases_path,
                k=3,
                retrieval_mode="legacy",
                embed_backend="local",
                embed_model=None,
            ):
                self.seen.append((retrieval_mode, embed_backend, embed_model, k))
                return {"hit1": 0.1, "hitk": 0.2, "mrr": 0.3, "cases": 1, "k": k}

        module = FakeModule()
        _ = self.ledger_ab.invoke_run_eval(
            module,
            "/tmp/cases.yaml",
            3,
            "semantic_hybrid",
            embed_backend="openai",
            embed_model="text-embedding-3-small",
        )
        self.assertEqual(
            module.seen,
            [("semantic_hybrid", "openai", "text-embedding-3-small", 3)],
        )

    def test_invoke_rank_query_passes_embed_backend_and_model(self):
        class FakeModule:
            def __init__(self):
                self.seen = []

            def rank_query(
                self,
                _query,
                scope="all",
                limit=50,
                retrieval_mode="legacy",
                embed_backend="local",
                embed_model=None,
            ):
                self.seen.append((retrieval_mode, embed_backend, embed_model, scope, limit))
                return {"results": []}

        module = FakeModule()
        _ = self.ledger_ab.invoke_rank_query(
            module,
            "q",
            "all",
            50,
            "semantic_hybrid",
            embed_backend="openai",
            embed_model="text-embedding-3-small",
        )
        self.assertEqual(
            module.seen,
            [("semantic_hybrid", "openai", "text-embedding-3-small", "all", 50)],
        )


class LedgerABSemanticIndexBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger_ab = load_ledger_ab_module()

    def test_maybe_build_semantic_index_noop_for_non_semantic_mode(self):
        class FakeModule:
            def load_embeddings_module(self):
                raise AssertionError("should not be called")

        payload = self.ledger_ab.maybe_build_semantic_index(
            FakeModule(),
            retrieval_mode="legacy",
            embed_backend="local",
            embed_model=None,
            side_label="baseline",
        )
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["reason"], "retrieval_mode_not_semantic_hybrid")

    def test_maybe_build_semantic_index_calls_build_indices_for_semantic_mode(self):
        class FakeEmbeddings:
            @staticmethod
            def default_model_for_backend(_backend):
                return "TaylorAI/bge-micro-v2"

            @staticmethod
            def build_indices(
                target,
                backend,
                model,
                write_manifest=True,
                append_timeline=True,
            ):
                self.assertEqual(target, "ledger")
                self.assertEqual(backend, "local")
                self.assertEqual(model, "TaylorAI/bge-micro-v2")
                self.assertFalse(write_manifest)
                self.assertFalse(append_timeline)
                return {
                    "results": [
                        {
                            "model": model,
                            "item_count": 10,
                            "dims": 384,
                            "embedded_count": 4,
                            "reused_count": 6,
                            "removed_count": 0,
                            "built_at": "2026-02-13T00:00:00Z",
                        }
                    ]
                }

        class FakeModule:
            @staticmethod
            def load_embeddings_module():
                return FakeEmbeddings()

        payload = self.ledger_ab.maybe_build_semantic_index(
            FakeModule(),
            retrieval_mode="semantic_hybrid",
            embed_backend="local",
            embed_model=None,
            side_label="candidate",
        )
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["backend"], "local")
        self.assertEqual(payload["model"], "TaylorAI/bge-micro-v2")
        self.assertEqual(payload["item_count"], 10)
        self.assertEqual(payload["dims"], 384)


class LedgerABCacheResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger_ab = load_ledger_ab_module()

    def test_reset_module_cache_uses_clear_function_when_available(self):
        class FakeModule:
            def __init__(self):
                self._CANDIDATE_CACHE = "warm"
                self.called = 0

            def clear_candidate_cache(self):
                self.called += 1
                self._CANDIDATE_CACHE = None

        module = FakeModule()
        self.ledger_ab.reset_module_cache(module)
        self.assertEqual(module.called, 1)
        self.assertIsNone(module._CANDIDATE_CACHE)

    def test_probe_eval_latency_resets_cache_each_run(self):
        class FakeModule:
            def __init__(self):
                self._CANDIDATE_CACHE = "warm"
                self.seen_cache_values = []

            def run_eval(self, _cases_path, k=3, retrieval_mode="legacy"):
                self.seen_cache_values.append(self._CANDIDATE_CACHE)
                self._CANDIDATE_CACHE = "warm"
                return {"hit1": 0.0, "hitk": 0.0, "mrr": 0.0, "cases": 1, "k": k}

        module = FakeModule()
        _ = self.ledger_ab.probe_eval_latency(module, Path("/tmp/cases.yaml"), 3, 2, "two_stage")
        self.assertEqual(module.seen_cache_values, [None, None])

    def test_probe_query_latency_resets_cache_each_run(self):
        class FakeModule:
            def __init__(self):
                self._CANDIDATE_CACHE = "warm"
                self.seen_cache_values = []

            def parse_eval_cases(self, _cases_path):
                return [{"query": "q", "scope": "all"}]

            def rank_query(self, _query, scope="all", limit=50, retrieval_mode="legacy"):
                self.seen_cache_values.append(self._CANDIDATE_CACHE)
                self._CANDIDATE_CACHE = "warm"
                return {"results": [], "scope": scope, "limit": limit}

        module = FakeModule()
        _ = self.ledger_ab.probe_query_latency(module, Path("/tmp/cases.yaml"), 2, "two_stage")
        self.assertEqual(module.seen_cache_values, [None, None])

    def test_probe_query_latency_resets_cache_each_case_when_cold_query(self):
        class FakeModule:
            def __init__(self):
                self._CANDIDATE_CACHE = "warm"
                self.seen_cache_values = []

            def parse_eval_cases(self, _cases_path):
                return [{"query": "q1", "scope": "all"}, {"query": "q2", "scope": "all"}]

            def rank_query(self, _query, scope="all", limit=50, retrieval_mode="legacy"):
                self.seen_cache_values.append(self._CANDIDATE_CACHE)
                self._CANDIDATE_CACHE = "warm"
                return {"results": [], "scope": scope, "limit": limit}

        module = FakeModule()
        payload = self.ledger_ab.probe_query_latency(
            module, Path("/tmp/cases.yaml"), 2, "two_stage", cold_query=True
        )
        self.assertEqual(module.seen_cache_values, [None, None, None, None])
        self.assertTrue(payload["cold_query"])


class LedgerABSmokeIntegrationTests(unittest.TestCase):
    def test_head_vs_head_smoke(self):
        with tempfile.TemporaryDirectory() as out_dir:
            process = subprocess.run(
                [
                    sys.executable,
                    str(LEDGER_AB_PATH),
                    "--baseline-ref",
                    "HEAD",
                    "--candidate-ref",
                    "HEAD",
                    "--baseline-mode",
                    "legacy",
                    "--candidate-mode",
                    "legacy",
                    "--eval-runs",
                    "1",
                    "--query-runs",
                    "1",
                    "--out-dir",
                    out_dir,
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )

            self.assertIn(process.returncode, {0, 3})

            json_report = Path(out_dir) / "ab_eval.json"
            md_report = Path(out_dir) / "ab_eval.md"

            self.assertTrue(json_report.exists())
            self.assertTrue(md_report.exists())

            payload = json.loads(json_report.read_text(encoding="utf-8"))
            self.assertIn("baseline", payload)
            self.assertIn("candidate", payload)
            self.assertIn("decision", payload)
            self.assertIn("settings", payload)
            self.assertIn("query_metrics", payload["baseline"])
            self.assertIn("context_metrics", payload["baseline"])
            self.assertIn("maintenance_metrics", payload["baseline"])
            self.assertIn("boot_context_tokens", payload["baseline"]["context_metrics"])
            self.assertIn("notes_total_tokens", payload["baseline"]["context_metrics"])
            self.assertIn("index_rebuild_ms", payload["baseline"]["query_metrics"])
            self.assertNotIn("composite_quality_score", payload)

    def test_head_vs_head_smoke_with_cold_query(self):
        with tempfile.TemporaryDirectory() as out_dir:
            process = subprocess.run(
                [
                    sys.executable,
                    str(LEDGER_AB_PATH),
                    "--baseline-ref",
                    "HEAD",
                    "--candidate-ref",
                    "HEAD",
                    "--baseline-mode",
                    "legacy",
                    "--candidate-mode",
                    "legacy",
                    "--eval-runs",
                    "1",
                    "--query-runs",
                    "1",
                    "--cold-query",
                    "--out-dir",
                    out_dir,
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )

            self.assertIn(process.returncode, {0, 3})
            payload = json.loads((Path(out_dir) / "ab_eval.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["settings"]["cold_query"])
            self.assertTrue(payload["baseline"]["latency"]["query"]["cold_query"])
            self.assertTrue(payload["candidate"]["latency"]["query"]["cold_query"])

    def test_head_vs_head_smoke_with_runs_shortcut(self):
        with tempfile.TemporaryDirectory() as out_dir:
            process = subprocess.run(
                [
                    sys.executable,
                    str(LEDGER_AB_PATH),
                    "--baseline-ref",
                    "HEAD",
                    "--candidate-ref",
                    "HEAD",
                    "--baseline-mode",
                    "legacy",
                    "--candidate-mode",
                    "legacy",
                    "--runs",
                    "1",
                    "--out-dir",
                    out_dir,
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )

            self.assertIn(process.returncode, {0, 3})
            payload = json.loads((Path(out_dir) / "ab_eval.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["settings"]["runs"], 1)
            self.assertEqual(payload["settings"]["eval_runs"], 1)
            self.assertEqual(payload["settings"]["query_runs"], 1)


if __name__ == "__main__":
    unittest.main()

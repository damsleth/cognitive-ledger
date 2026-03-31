import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "scripts" / "ledger"
CASES_PATH = ROOT / "notes" / "08_indices" / "retrieval_eval_cases.yaml"

# Ensure ledger package is importable
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_ledger_module():
    """Load the extensionless scripts/ledger as a module."""
    loader = importlib.machinery.SourceFileLoader("ledger_module", str(LEDGER_PATH))
    spec = importlib.util.spec_from_file_location("ledger_module", str(LEDGER_PATH), loader=loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload_get(payload, key, default=None):
    return getattr(payload, key, default) if hasattr(payload, key) else payload.get(key, default)


def payload_results(payload):
    return payload.results if hasattr(payload, "results") else payload["results"]


def result_get(result, key, default=None):
    return getattr(result, key, default) if hasattr(result, key) else result.get(key, default)


class LedgerUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = load_ledger_module()

    def test_parse_frontmatter_block_list_quotes_comments(self):
        from ledger.parsing import parse_frontmatter_lines

        lines = [
            "created: 2026-02-09T00:00:00Z",
            "updated: 2026-02-09T00:00:00Z",
            "tags:",
            "  - parser",
            "  - smoke_test",
            'source: "inferred" # comment',
            "confidence: '0.65'",
        ]
        data = parse_frontmatter_lines(lines)
        self.assertEqual(data["tags"], ["parser", "smoke_test"])
        self.assertEqual(data["source"], "inferred")
        # Canonical parser returns float for numeric scalars
        self.assertAlmostEqual(float(data["confidence"]), 0.65)

    def test_normalize_section_aliases(self):
        self.assertEqual(self.ledger.normalize_section_name("Next Actions"), "next action")
        self.assertEqual(self.ledger.normalize_section_name("Why matters"), "why it matters")

    def test_retrieval_modes_include_semantic_hybrid(self):
        self.assertIn("semantic_hybrid", self.ledger.RETRIEVAL_MODES)

    def test_recency_normalization(self):
        now = dt.datetime(2026, 2, 9, tzinfo=dt.timezone.utc)
        fresh = dt.datetime(2026, 2, 9, tzinfo=dt.timezone.utc)
        old = dt.datetime(2025, 11, 11, tzinfo=dt.timezone.utc)  # ~90 days
        self.assertAlmostEqual(self.ledger.compute_recency_component(fresh, now), 1.0, places=4)
        self.assertLessEqual(self.ledger.compute_recency_component(old, now), 0.05)

    def test_closed_loop_penalty_history_override(self):
        now = dt.datetime(2026, 2, 9, tzinfo=dt.timezone.utc)
        candidate = {
            "type": "loop",
            "status": "closed",
            "note_tokens": {"deploy", "production"},
            "tag_tokens": {"deploy"},
            "scope": "dev",
            "updated_ts": now,
            "confidence": 0.9,
        }
        query_tokens = {"deploy"}

        score_normal, _, _ = self.ledger.score_candidate(
            candidate, query_tokens, "all", False, False, False, now, expansion_events=[]
        )
        score_history, _, _ = self.ledger.score_candidate(
            candidate, query_tokens, "all", True, False, False, now, expansion_events=[]
        )
        self.assertGreater(score_history, score_normal)

    def test_scope_boost_vs_demote(self):
        now = dt.datetime(2026, 2, 9, tzinfo=dt.timezone.utc)
        candidate = {
            "type": "fact",
            "status": "",
            "note_tokens": {"planning", "work"},
            "tag_tokens": {"planning"},
            "scope": "work",
            "updated_ts": now,
            "confidence": 0.9,
        }
        query_tokens = {"planning"}

        score_match, _, _ = self.ledger.score_candidate(
            candidate, query_tokens, "work", False, False, False, now, expansion_events=[]
        )
        score_miss, _, _ = self.ledger.score_candidate(
            candidate, query_tokens, "personal", False, False, False, now, expansion_events=[]
        )
        self.assertGreater(score_match, score_miss)

    def test_bm25_component_boosts_candidate_score(self):
        now = dt.datetime(2026, 2, 9, tzinfo=dt.timezone.utc)
        candidate = {
            "type": "fact",
            "status": "",
            "note_tokens": {"python", "ledger"},
            "tag_tokens": {"python"},
            "scope": "dev",
            "updated_ts": now,
            "confidence": 0.9,
        }
        query_tokens = {"python"}

        score_no_bm25, _, _ = self.ledger.score_candidate(
            candidate,
            query_tokens,
            "all",
            False,
            False,
            False,
            now,
            expansion_events=[],
            bm25_score=0.0,
        )
        score_with_bm25, _, _ = self.ledger.score_candidate(
            candidate,
            query_tokens,
            "all",
            False,
            False,
            False,
            now,
            expansion_events=[],
            bm25_score=1.0,
        )
        self.assertGreater(score_with_bm25, score_no_bm25)

    def test_semantic_hybrid_falls_back_to_lexical_when_index_missing(self):
        from ledger.config import LedgerConfig, set_config, reset_config
        from ledger.retrieval import clear_candidate_cache

        ledger = self.ledger
        original_loader = ledger.load_embeddings_module

        class FakeEmbeddings:
            @staticmethod
            def default_model_for_backend(_backend):
                return "fake-model"

            @staticmethod
            def semantic_score_map(**_kwargs):
                return {
                    "available": False,
                    "reason": "missing_index",
                    "results": [],
                    "score_by_rel_path": {},
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            note_dir = tmp / "notes" / "05_open_loops"
            note_dir.mkdir(parents=True)
            (note_dir / "loop__release.md").write_text(
                "---\ncreated: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n"
                "tags: [release]\nconfidence: 0.9\nsource: user\nscope: dev\nlang: en\n"
                "status: open\n---\n\n# Release\n\n## Question or Task\n\n"
                "What should I do next for release?\n",
                encoding="utf-8",
            )
            config = LedgerConfig(root_dir=tmp)
            set_config(config)
            clear_candidate_cache()
            ledger.load_embeddings_module = lambda: FakeEmbeddings
            try:
                payload = ledger.rank_query(
                    "What should I do next for release?",
                    scope="dev",
                    limit=8,
                    retrieval_mode="semantic_hybrid",
                )
            finally:
                ledger.load_embeddings_module = original_loader
                clear_candidate_cache()
                reset_config()

        self.assertEqual(payload_get(payload, "retrieval_mode"), "semantic_hybrid")
        self.assertEqual(payload_get(payload, "effective_retrieval_mode"), "legacy")
        self.assertEqual(payload_get(payload, "semantic")["reason"], "missing_index")
        self.assertTrue(payload_results(payload))

    def test_semantic_hybrid_openai_fails_without_api_key(self):
        ledger = self.ledger
        original_loader = ledger.load_embeddings_module
        original_key = os.environ.get("OPENAI_API_KEY")

        class FakeEmbeddings:
            @staticmethod
            def default_model_for_backend(_backend):
                return "text-embedding-3-small"

            @staticmethod
            def ensure_openai_api_key():
                raise RuntimeError("OPENAI_API_KEY is required for OpenAI embedding backend")

        ledger.load_embeddings_module = lambda: FakeEmbeddings
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
        try:
            with self.assertRaises(RuntimeError):
                ledger.rank_query(
                    "test openai",
                    retrieval_mode="semantic_hybrid",
                    embed_backend="openai",
                )
        finally:
            ledger.load_embeddings_module = original_loader
            if original_key is not None:
                os.environ["OPENAI_API_KEY"] = original_key

    def test_semantic_hybrid_uses_ledger_target_only(self):
        ledger = self.ledger
        original_loader = ledger.load_embeddings_module
        seen_targets = []

        class FakeEmbeddings:
            @staticmethod
            def default_model_for_backend(_backend):
                return "fake-model"

            @staticmethod
            def semantic_score_map(**kwargs):
                seen_targets.append(kwargs.get("target"))
                return {
                    "available": True,
                    "index_item_count": 1,
                    "results": [],
                    "score_by_rel_path": {
                        "notes/05_open_loops/loop__sample_release_checklist.md": 0.95
                    },
                }

        ledger.load_embeddings_module = lambda: FakeEmbeddings
        try:
            payload = ledger.rank_query(
                "release checklist",
                scope="dev",
                limit=3,
                retrieval_mode="semantic_hybrid",
            )
        finally:
            ledger.load_embeddings_module = original_loader

        self.assertEqual(seen_targets, ["ledger"])
        self.assertEqual(payload_get(payload, "retrieval_mode"), "semantic_hybrid")

    def test_alias_expansion(self):
        expanded, events = self.ledger.expand_query_tokens(
            {"alex"}, {"alex": ["example_user", "demo_user"]}
        )
        self.assertIn("example", expanded)
        self.assertIn("demo", expanded)
        self.assertTrue(events)

    def test_query_telemetry_writes_jsonl_when_enabled(self):
        from ledger.config import LedgerConfig, set_config, reset_config
        from ledger.retrieval import _maybe_log_query, clear_candidate_cache
        from ledger.retrieval_types import RetrievalResult, TimingInfo

        original = os.environ.get("LEDGER_QUERY_LOG")
        os.environ["LEDGER_QUERY_LOG"] = "1"
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                tmp = Path(temp_dir)
                (tmp / "notes" / "08_indices").mkdir(parents=True)
                config = LedgerConfig(root_dir=tmp)
                set_config(config)
                result = RetrievalResult(
                    query="calendar constraints",
                    scope="work",
                    retrieval_mode="legacy",
                    progressive_top_n=0,
                    expanded_tokens=[],
                    expansion_events=[],
                    candidate_pool_size=0,
                    indexed_pool_size=None,
                    prefilter_size=0,
                    shortlist_size=0,
                    results=[],
                    timing=TimingInfo(
                        expand_ms=0, candidates_ms=0, prefilter_ms=0,
                        shortlist_ms=0, score_ms=0, total_ms=42.6,
                    ),
                )
                _maybe_log_query(result)
                log_path = tmp / "notes" / "08_indices" / "query_log.jsonl"
                content = log_path.read_text(encoding="utf-8")
                self.assertIn('"query": "calendar constraints"', content)
                self.assertIn('"scope": "work"', content)
                self.assertIn('"latency_ms": 42.6', content)
        finally:
            reset_config()
            if original is None:
                del os.environ["LEDGER_QUERY_LOG"]
            else:
                os.environ["LEDGER_QUERY_LOG"] = original

    def test_extract_link_tokens_captures_markdown_and_wiki_refs(self):
        body = (
            "See [Progressive Disclosure](notes/06_concepts/concept__progressive_disclosure.md) "
            "and [[concept__compressed_attention_retrieval]]."
        )
        tokens = self.ledger.extract_link_tokens(body)
        self.assertIn("progressive", tokens)
        self.assertIn("disclosure", tokens)
        self.assertIn("compressed", tokens)
        self.assertIn("attention", tokens)

    def test_build_candidates_uses_process_cache(self):
        first_no_cache = self.ledger.build_candidates()
        second_no_cache = self.ledger.build_candidates()
        self.assertIsNot(first_no_cache, second_no_cache)

        first_cached = self.ledger.build_candidates(use_cache=True)
        second_cached = self.ledger.build_candidates(use_cache=True)
        self.assertIs(first_cached, second_cached)

    def test_build_candidate_index_uses_process_cache(self):
        first = self.ledger.build_candidate_index(use_cache=True)
        second = self.ledger.build_candidate_index(use_cache=True)
        self.assertIs(first, second)

    def test_retrieve_candidates_from_index_prefers_matching_tokens(self):
        candidates = [
            {
                "path": "/tmp/one.md",
                "updated": "2026-02-10T00:00:00Z",
                "scope": "dev",
                "note_tokens": {"alpha"},
                "tag_tokens": {"alpha"},
                "attention_tokens": {"alpha"},
            },
            {
                "path": "/tmp/two.md",
                "updated": "2026-02-10T00:00:00Z",
                "scope": "dev",
                "note_tokens": {"beta"},
                "tag_tokens": {"beta"},
                "attention_tokens": {"beta"},
            },
        ]
        index = {
            "candidates": candidates,
            "token_index": {"alpha": [0], "beta": [1]},
        }
        selected = self.ledger.retrieve_candidates_from_index(
            index, query_tokens={"alpha"}, query_scope="all", minimum_pool=1
        )
        self.assertEqual(result_get(selected[0], "path"), "/tmp/one.md")

    def test_shortlist_candidates_reduces_candidate_pool(self):
        candidates = []
        for idx in range(80):
            candidates.append(
                {
                    "path": f"/tmp/path_{idx}.md",
                    "updated": f"2026-02-10T00:{idx:02d}:00Z",
                    "note_tokens": {"target", f"token{idx}"},
                    "tag_tokens": set(),
                    "scope": "dev",
                    "type": "fact",
                    "status": "",
                }
            )

        shortlist = self.ledger.shortlist_candidates(
            candidates=candidates,
            query_tokens={"target"},
            query_scope="all",
            history_mode=False,
            loop_mode=False,
            preference_mode=False,
            limit=8,
        )
        expected_len = min(
            len(candidates),
            max(
                self.ledger.SHORTLIST_MIN_CANDIDATES,
                min(
                    self.ledger.SHORTLIST_MAX_CANDIDATES,
                    max(1, 8) * self.ledger.SHORTLIST_LIMIT_MULTIPLIER,
                ),
            ),
        )
        self.assertEqual(len(shortlist), expected_len)

    def test_shortlist_candidates_filters_non_matching_all_scope(self):
        matching = {
            "path": "/tmp/match.md",
            "updated": "2026-02-10T00:00:00Z",
            "note_tokens": {"target"},
            "tag_tokens": set(),
            "scope": "dev",
            "type": "fact",
            "status": "",
        }
        non_matching = {
            "path": "/tmp/miss.md",
            "updated": "2026-02-10T00:00:00Z",
            "note_tokens": {"other"},
            "tag_tokens": set(),
            "scope": "dev",
            "type": "fact",
            "status": "",
        }

        shortlist = self.ledger.shortlist_candidates(
            candidates=[matching, non_matching],
            query_tokens={"target"},
            query_scope="all",
            history_mode=False,
            loop_mode=False,
            preference_mode=False,
            limit=8,
        )
        shortlisted_paths = {result_get(item, "path") for item in shortlist}
        self.assertIn("/tmp/match.md", shortlisted_paths)
        self.assertNotIn("/tmp/miss.md", shortlisted_paths)

    def test_shortlist_attention_candidates_reduces_candidate_pool(self):
        candidates = []
        for idx in range(120):
            candidates.append(
                {
                    "path": f"/tmp/path_{idx}.md",
                    "updated": f"2026-02-10T00:{idx % 60:02d}:00Z",
                    "note_tokens": {"target", f"token{idx}"},
                    "attention_tokens": {"target", f"capsule{idx}"},
                    "tag_tokens": set(),
                    "scope": "dev",
                    "type": "fact",
                    "status": "",
                }
            )

        shortlist = self.ledger.shortlist_attention_candidates(
            candidates=candidates,
            query_tokens={"target"},
            query_scope="all",
            history_mode=False,
            loop_mode=False,
            preference_mode=False,
            limit=50,
        )
        expected_len = min(
            len(candidates),
            max(
                self.ledger.ATTENTION_SHORTLIST_MIN_CANDIDATES,
                min(
                    self.ledger.ATTENTION_SHORTLIST_MAX_CANDIDATES,
                    max(1, 50) * self.ledger.ATTENTION_SHORTLIST_LIMIT_MULTIPLIER,
                ),
            ),
        )
        self.assertEqual(len(shortlist), expected_len)

    def test_scope_type_prefilter_filters_non_overlapping_intent_noise(self):
        loop_candidate = {
            "path": "/tmp/loop.md",
            "updated": "2026-02-10T00:00:00Z",
            "note_tokens": {"pending", "task"},
            "tag_tokens": {"loop"},
            "scope": "dev",
            "type": "loop",
            "status": "open",
        }
        unrelated_fact = {
            "path": "/tmp/fact.md",
            "updated": "2026-02-10T00:00:00Z",
            "note_tokens": {"calendar"},
            "tag_tokens": {"meeting"},
            "scope": "dev",
            "type": "fact",
            "status": "",
        }

        filtered = self.ledger.prefilter_candidates_by_scope_and_type(
            candidates=[loop_candidate, unrelated_fact],
            query_tokens={"loop", "task", "pending"},
            query_scope="all",
            history_mode=False,
            loop_mode=True,
            preference_mode=False,
            limit=8,
        )
        filtered_paths = {result_get(item, "path") for item in filtered}
        self.assertIn("/tmp/loop.md", filtered_paths)
        self.assertNotIn("/tmp/fact.md", filtered_paths)

    def test_expected_path_canonicalizes_external_absolute_notes_path(self):
        candidates = self.ledger.path_candidates_from_expected(
            "/other/root/notes/03_preferences/pref__agent_work_style.md"
        )
        self.assertIn("notes/03_preferences/pref__agent_work_style.md", candidates)

    def test_eval_matches_when_case_uses_external_absolute_path(self):
        from ledger.config import LedgerConfig, set_config, reset_config
        from ledger.retrieval import clear_candidate_cache
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            note_dir = tmp / "notes" / "03_preferences"
            note_dir.mkdir(parents=True)
            note = note_dir / "pref__test_style.md"
            note.write_text(
                "---\ncreated: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n"
                "tags: [test]\nconfidence: 0.9\nsource: user\nscope: dev\nlang: en\n---\n\n"
                "# Test Pref\n\n## Statement\n\nStable work-style preferences for testing.\n",
                encoding="utf-8",
            )
            config = LedgerConfig(root_dir=tmp)
            set_config(config)
            clear_candidate_cache()
            try:
                content = (
                    '- query: "What are my stable work-style preferences?"\n'
                    "  id: stable_work_style\n"
                    '  scope: "all"\n'
                    "  expected_any:\n"
                    f'    - "/other/root/notes/03_preferences/pref__test_style.md"\n'
                )
                case_path = tmp / "cases.yaml"
                case_path.write_text(content, encoding="utf-8")
                result = self.ledger.run_eval(case_path, k=3)
            finally:
                clear_candidate_cache()
                reset_config()
        self.assertGreater(result["hitk"], 0.0)

    def test_parse_eval_cases_reads_id(self):
        content = (
            '- query: "test query"\n'
            "  id: test_case_one\n"
            '  scope: "all"\n'
            "  expected_any:\n"
            '    - "notes/03_preferences/pref__agent_work_style.md"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            case_path = Path(temp_dir) / "cases.yaml"
            case_path.write_text(content, encoding="utf-8")
            cases = self.ledger.parse_eval_cases(case_path)
        self.assertEqual(cases[0]["id"], "test_case_one")

    def test_eval_strict_cases_rejects_absolute_path(self):
        content = (
            '- query: "test query"\n'
            "  id: strict_abs_path\n"
            '  scope: "all"\n'
            "  expected_any:\n"
            '    - "/tmp/notes/03_preferences/pref__agent_work_style.md"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            case_path = Path(temp_dir) / "cases.yaml"
            case_path.write_text(content, encoding="utf-8")
            with self.assertRaises(self.ledger.EvalCaseValidationError):
                self.ledger.run_eval(case_path, k=3, strict_cases=True)

    def test_eval_strict_cases_rejects_missing_or_duplicate_id(self):
        missing_id = (
            '- query: "first query"\n'
            '  scope: "all"\n'
            "  expected_any:\n"
            '    - "notes/03_preferences/pref__agent_work_style.md"\n'
        )
        duplicate_id = (
            '- query: "second query"\n'
            "  id: duplicate_case\n"
            '  scope: "all"\n'
            "  expected_any:\n"
            '    - "notes/03_preferences/pref__agent_work_style.md"\n'
            "\n"
            '- query: "third query"\n'
            "  id: duplicate_case\n"
            '  scope: "all"\n'
            "  expected_any:\n"
            '    - "notes/03_preferences/pref__agent_work_style.md"\n'
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            missing_case_path = Path(temp_dir) / "missing_id.yaml"
            missing_case_path.write_text(missing_id, encoding="utf-8")
            with self.assertRaises(self.ledger.EvalCaseValidationError):
                self.ledger.run_eval(missing_case_path, k=3, strict_cases=True)

            duplicate_case_path = Path(temp_dir) / "duplicate_id.yaml"
            duplicate_case_path.write_text(duplicate_id, encoding="utf-8")
            with self.assertRaises(self.ledger.EvalCaseValidationError):
                self.ledger.run_eval(duplicate_case_path, k=3, strict_cases=True)

    def test_eval_strict_cases_rejects_missing_note_path(self):
        content = (
            '- query: "test query"\n'
            "  id: missing_note\n"
            '  scope: "all"\n'
            "  expected_any:\n"
            '    - "notes/03_preferences/pref__does_not_exist.md"\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            case_path = Path(temp_dir) / "cases.yaml"
            case_path.write_text(content, encoding="utf-8")
            with self.assertRaises(self.ledger.EvalCaseValidationError):
                self.ledger.run_eval(case_path, k=3, strict_cases=True)


def _has_corpus_notes() -> bool:
    """Check if the repo has actual notes (not just .gitkeep)."""
    notes_root = Path(__file__).resolve().parents[1] / "notes"
    for folder in ("02_facts", "03_preferences", "04_goals", "05_open_loops", "06_concepts"):
        d = notes_root / folder
        if d.is_dir() and any(f.suffix == ".md" for f in d.iterdir()):
            return True
    return False


@unittest.skipUnless(_has_corpus_notes(), "no corpus notes present - skipping integration tests")
class LedgerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = load_ledger_module()

    def test_query_release_returns_open_loop(self):
        payload = self.ledger.rank_query("What should I do next for release?", scope="dev", limit=8)
        top_paths = [result_get(item, "path") for item in payload_results(payload)[:5]]
        self.assertTrue(
            any("loop__sample_release_checklist.md" in path for path in top_paths),
            msg=f"Top paths: {top_paths}",
        )

    def test_query_reports_two_stage_metadata(self):
        payload = self.ledger.rank_query(
            "What should I do next for release?",
            scope="dev",
            limit=8,
            retrieval_mode="two_stage",
        )
        self.assertEqual(payload_get(payload, "retrieval_mode"), "two_stage")
        self.assertIsNotNone(payload_get(payload, "candidate_pool_size"))
        self.assertIsNotNone(payload_get(payload, "shortlist_size"))
        self.assertLessEqual(payload_get(payload, "shortlist_size"), payload_get(payload, "candidate_pool_size"))

    def test_query_reports_compressed_attention_metadata(self):
        payload = self.ledger.rank_query(
            "What should I do next for release?",
            scope="dev",
            limit=8,
            retrieval_mode="compressed_attention",
        )
        self.assertEqual(payload_get(payload, "retrieval_mode"), "compressed_attention")
        self.assertIsNotNone(payload_get(payload, "candidate_pool_size"))
        self.assertIsNotNone(payload_get(payload, "shortlist_size"))
        self.assertLessEqual(payload_get(payload, "shortlist_size"), payload_get(payload, "candidate_pool_size"))

    def test_query_compressed_attention_shortlists_eval_limit(self):
        payload = self.ledger.rank_query(
            "What should I do next for release?",
            scope="dev",
            limit=50,
            retrieval_mode="compressed_attention",
        )
        self.assertLessEqual(payload_get(payload, "shortlist_size"), payload_get(payload, "candidate_pool_size"))
        if payload_get(payload, "candidate_pool_size") > self.ledger.ATTENTION_SHORTLIST_MAX_CANDIDATES:
            self.assertLess(payload_get(payload, "shortlist_size"), payload_get(payload, "candidate_pool_size"))

    def test_query_scope_type_prefilter_reports_prefilter_size(self):
        payload = self.ledger.rank_query(
            "What loop tasks are pending for release?",
            scope="dev",
            limit=8,
            retrieval_mode="scope_type_prefilter",
        )
        self.assertEqual(payload_get(payload, "retrieval_mode"), "scope_type_prefilter")
        self.assertIsNotNone(payload_get(payload, "prefilter_size"))
        self.assertLessEqual(payload_get(payload, "prefilter_size"), payload_get(payload, "candidate_pool_size"))

    def test_query_precomputed_index_reports_indexed_pool_size(self):
        payload = self.ledger.rank_query(
            "What should I do next for release?",
            scope="dev",
            limit=8,
            retrieval_mode="precomputed_index",
        )
        self.assertEqual(payload_get(payload, "retrieval_mode"), "precomputed_index")
        self.assertIsNotNone(payload_get(payload, "indexed_pool_size"))
        self.assertLessEqual(payload_get(payload, "indexed_pool_size"), payload_get(payload, "candidate_pool_size"))

    def test_query_progressive_disclosure_assigns_levels(self):
        payload = self.ledger.rank_query(
            "What should I do next for release?",
            scope="dev",
            limit=8,
            retrieval_mode="progressive_disclosure",
        )
        self.assertEqual(payload_get(payload, "retrieval_mode"), "progressive_disclosure")
        self.assertEqual(payload_get(payload, "progressive_top_n"), self.ledger.PROGRESSIVE_RATIONALE_TOP)
        self.assertTrue(payload_results(payload))

        top_n = min(self.ledger.PROGRESSIVE_RATIONALE_TOP, len(payload_results(payload)))
        for item in payload_results(payload)[:top_n]:
            self.assertEqual(result_get(item, "disclosure_level"), "rationale")
            self.assertTrue(result_get(item, "reasons"))
        for item in payload_results(payload)[top_n:]:
            self.assertEqual(result_get(item, "disclosure_level"), "compact")
            self.assertEqual(result_get(item, "reasons"), [])

    def test_query_large_limit_suppresses_rationale_strings(self):
        payload = self.ledger.rank_query(
            "What should I do next for release?",
            scope="dev",
            limit=50,
            retrieval_mode="two_stage",
        )
        self.assertTrue(payload_results(payload))
        self.assertTrue(all(result_get(item, "reasons") == [] for item in payload_results(payload)))
        self.assertEqual(payload_get(payload, "shortlist_size"), payload_get(payload, "candidate_pool_size"))

    def test_query_default_legacy_mode_shortlists(self):
        payload = self.ledger.rank_query("What should I do next for release?", scope="dev", limit=8)
        self.assertEqual(payload_get(payload, "retrieval_mode"), "legacy")
        self.assertLessEqual(payload_get(payload, "shortlist_size"), payload_get(payload, "candidate_pool_size"))
        if payload_get(payload, "candidate_pool_size") > self.ledger.ATTENTION_SHORTLIST_MAX_CANDIDATES:
            self.assertLess(payload_get(payload, "shortlist_size"), payload_get(payload, "candidate_pool_size"))

    def test_query_legacy_keeps_reasons_for_large_limit(self):
        payload = self.ledger.rank_query("What should I do next for release?", scope="dev", limit=50)
        self.assertTrue(payload_results(payload))
        self.assertTrue(any(result_get(item, "reasons") for item in payload_results(payload)))

    def test_eval_cases_parse(self):
        cases = self.ledger.parse_eval_cases(CASES_PATH)
        self.assertGreaterEqual(len(cases), 16)
        self.assertTrue(all("query" in case for case in cases))

    def test_eval_runs_and_returns_metrics(self):
        result = self.ledger.run_eval(CASES_PATH, k=3)
        self.assertIn("hit1", result)
        self.assertIn("hitk", result)
        self.assertIn("mrr", result)
        self.assertGreater(result["cases"], 0)


if __name__ == "__main__":
    unittest.main()

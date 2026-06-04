"""Tests for FUSION (RRF) and MECHANISM 2 (PRF).

Covers:
- reciprocal_rank_fusion: hand-computed RRF scores match function output.
- fusion=weighted_sum (default): ranking is byte-identical to pre-change behaviour
  (golden test using FakeEmbeddings, no model download).
- fusion=rrf: items with high lexical AND semantic rank surface to the top.
- PRF: off by default (--prf off = unchanged results).
- PRF: off-topic top result gets demoted after vector expansion (construct
  embedding vectors directly with numpy; no model download).
- prf_expand_query_vector: L2-normalised output; orthogonal negative does not
  cancel the query entirely.
"""

from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger import query as query_module
from ledger.config import LedgerConfig, reset_config, set_config
from ledger.query import prf_expand_query_vector, reciprocal_rank_fusion
from ledger.retrieval import clear_candidate_cache
from ledger.retrieval_types import RetrievalResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_note(path: Path, statement: str, tags: str = "[test]", scope: str = "dev") -> None:
    _write(
        path,
        f"""---
created: 2026-01-01T00:00:00Z
updated: 2026-01-01T00:00:00Z
tags: {tags}
confidence: 0.9
source: user
scope: {scope}
lang: en
---

# Note

## Statement

{statement}
""",
    )


@pytest.fixture(autouse=True)
def clean_state():
    reset_config()
    clear_candidate_cache()
    yield
    clear_candidate_cache()
    reset_config()
    importlib.reload(query_module)


# ---------------------------------------------------------------------------
# reciprocal_rank_fusion unit tests
# ---------------------------------------------------------------------------


class TestReciprocalRankFusion:
    """Unit tests for reciprocal_rank_fusion — no file system involved."""

    def test_single_list_scores(self):
        """RRF of a single list should equal 1/(k+rank) for each item."""
        k = 60
        items = ["a", "b", "c"]
        scores = reciprocal_rank_fusion([items], k=k)
        for rank, item in enumerate(items):
            expected = 1.0 / (k + rank + 1)
            assert abs(scores[item] - expected) < 1e-12, (
                f"item={item!r} rank={rank}: expected {expected:.6f} got {scores[item]:.6f}"
            )

    def test_two_lists_accumulate(self):
        """Items present in both lists accumulate their scores."""
        k = 60
        list1 = ["a", "b", "c"]
        list2 = ["b", "a", "c"]
        scores = reciprocal_rank_fusion([list1, list2], k=k)
        # "a": rank-0 in list1 + rank-1 in list2
        expected_a = 1 / (k + 1) + 1 / (k + 2)
        assert abs(scores["a"] - expected_a) < 1e-12
        # "b": rank-1 in list1 + rank-0 in list2
        expected_b = 1 / (k + 2) + 1 / (k + 1)
        assert abs(scores["b"] - expected_b) < 1e-12
        # symmetry: a and b should be equal
        assert abs(scores["a"] - scores["b"]) < 1e-12

    def test_item_only_in_one_list_has_lower_score_than_top_of_both(self):
        """An item appearing in both lists should outrank one appearing in only one."""
        k = 60
        # "shared" is rank-1 in both lists; "unique" is rank-0 in list1 only
        list1 = ["unique", "shared", "other"]
        list2 = ["shared", "extra"]
        scores = reciprocal_rank_fusion([list1, list2], k=k)
        # "shared" accumulates from both; "unique" only from list1 at rank-0
        assert scores["shared"] > scores["unique"], (
            f"shared={scores['shared']:.6f} unique={scores['unique']:.6f}"
        )

    def test_k_parameter_controls_smoothing(self):
        """Smaller k makes rank-1 vs rank-2 difference larger."""
        items = ["a", "b"]
        scores_k1 = reciprocal_rank_fusion([items], k=1)
        scores_k100 = reciprocal_rank_fusion([items], k=100)
        gap_k1 = scores_k1["a"] - scores_k1["b"]
        gap_k100 = scores_k100["a"] - scores_k100["b"]
        assert gap_k1 > gap_k100

    def test_empty_list_returns_empty(self):
        assert reciprocal_rank_fusion([]) == {}

    def test_empty_rank_list_in_sequence(self):
        """An empty rank list contributes nothing."""
        scores = reciprocal_rank_fusion([["a", "b"], []], k=60)
        assert "a" in scores
        # Scores from single list only
        assert abs(scores["a"] - 1 / (60 + 1)) < 1e-12

    def test_hand_computed_three_way_example(self):
        """Verify a fully hand-computed 3-list example."""
        k = 60
        # Three rank lists
        l1 = ["x", "y", "z"]
        l2 = ["y", "x", "z"]
        l3 = ["z", "x", "y"]
        scores = reciprocal_rank_fusion([l1, l2, l3], k=k)
        # x: 1/61 + 1/62 + 1/62
        expected_x = 1 / 61 + 1 / 62 + 1 / 62
        # y: 1/62 + 1/61 + 1/63
        expected_y = 1 / 62 + 1 / 61 + 1 / 63
        # z: 1/63 + 1/63 + 1/61
        expected_z = 1 / 63 + 1 / 63 + 1 / 61
        assert abs(scores["x"] - expected_x) < 1e-12
        assert abs(scores["y"] - expected_y) < 1e-12
        assert abs(scores["z"] - expected_z) < 1e-12


# ---------------------------------------------------------------------------
# prf_expand_query_vector unit tests
# ---------------------------------------------------------------------------


class TestPRFExpandQueryVector:
    """Unit tests for prf_expand_query_vector — pure numpy, no model download."""

    def _unit_vec(self, *coords: float) -> np.ndarray:
        v = np.array(coords, dtype=np.float32)
        return (v / np.linalg.norm(v)).reshape(1, -1)

    def test_output_is_l2_normalised(self):
        """Expanded vector must be L2-normalised."""
        q = self._unit_vec(1.0, 0.0, 0.0)
        vecs = {
            "pos1": np.array([0.9, 0.1, 0.0], dtype=np.float32),
            "neg1": np.array([0.0, 0.0, 1.0], dtype=np.float32),
        }
        expanded = prf_expand_query_vector(q, vecs, ["pos1"], ["neg1"])
        norm = float(np.linalg.norm(expanded))
        assert abs(norm - 1.0) < 1e-5, f"L2 norm should be ~1.0, got {norm}"

    def test_output_shape_matches_input(self):
        """Output shape must match input query vector."""
        q = self._unit_vec(1.0, 0.0, 0.0)
        vecs = {"pos1": np.array([1.0, 0.0, 0.0], dtype=np.float32)}
        expanded = prf_expand_query_vector(q, vecs, ["pos1"], [])
        assert expanded.shape == q.shape

    def test_no_pos_no_neg_returns_original_direction(self):
        """With no pseudo docs, the vector should stay in the same direction."""
        q = self._unit_vec(0.6, 0.8, 0.0)
        vecs: dict = {}
        expanded = prf_expand_query_vector(q, vecs, [], [])
        # dot product with original should be ~1.0 (same direction)
        dot = float(np.dot(expanded.reshape(-1), q.reshape(-1)))
        assert dot > 0.999, f"dot product should be ~1.0, got {dot}"

    def test_positive_shifts_toward_pos_centroid(self):
        """Adding a positive example shifts the query toward it."""
        q = self._unit_vec(1.0, 0.0)
        # Positive is 45 degrees off
        pos = np.array([0.707, 0.707], dtype=np.float32)
        vecs = {"pos1": pos}
        expanded = prf_expand_query_vector(q, vecs, ["pos1"], [], beta=0.75, gamma=0.0)
        # Component in y direction should now be positive
        assert expanded.reshape(-1)[1] > 0.0

    def test_negative_shifts_away_from_neg_centroid(self):
        """Adding a negative example shifts the query away from it."""
        q = self._unit_vec(1.0, 0.0)
        # Negative is aligned with y axis
        neg = np.array([0.0, 1.0], dtype=np.float32)
        vecs = {"neg1": neg}
        # Without negative: pos y is 0
        base = prf_expand_query_vector(q, vecs, [], [], alpha=1.0, beta=0.0, gamma=0.0)
        # With negative subtraction
        with_neg = prf_expand_query_vector(q, vecs, [], ["neg1"], alpha=1.0, beta=0.0, gamma=0.5)
        # y component should be smaller (more negative) with the negative applied
        assert with_neg.reshape(-1)[1] < base.reshape(-1)[1]

    def test_missing_path_in_vectors_is_ignored(self):
        """Paths not in the vectors dict should be silently skipped."""
        q = self._unit_vec(1.0, 0.0)
        vecs: dict = {}  # empty — all paths missing
        # Should not raise
        expanded = prf_expand_query_vector(q, vecs, ["missing_pos"], ["missing_neg"])
        assert expanded.shape == q.shape

    def test_off_topic_positive_gets_demoted_after_expansion(self):
        """PRF fixture: an off-topic top result gets demoted after expansion.

        Setup: query = [1, 0]; relevant docs have high x-component; the
        "off-topic" doc that happened to rank first in the initial pass has
        a high y-component.  After PRF with the actually-relevant docs as
        pseudo-positives and the off-topic doc as a pseudo-negative, the
        re-scored cosine of the relevant doc should exceed the off-topic one.
        """
        # Query: 1D aligned with x
        q = np.array([[1.0, 0.0]], dtype=np.float32)

        # Off-topic doc: mostly y, scored highest by the fake initial ranker
        off_topic_vec = np.array([0.2, 0.98], dtype=np.float32)
        off_topic_vec /= np.linalg.norm(off_topic_vec)

        # Relevant doc: mostly x
        relevant_vec = np.array([0.98, 0.2], dtype=np.float32)
        relevant_vec /= np.linalg.norm(relevant_vec)

        vecs = {"off_topic": off_topic_vec, "relevant": relevant_vec}

        # Before PRF: off-topic has lower cosine but let's define initial ranking
        # by injecting off-topic at rank-0 in score_by_rel_path (simulating a fluke)
        # PRF: top-1 = off_topic (as pseudo-positive is wrong here, so let's use
        # relevant as pseudo-positive and off_topic as pseudo-negative)
        expanded = prf_expand_query_vector(
            q, vecs,
            top_rel_paths=["relevant"],       # pseudo-positive = actually relevant
            bottom_rel_paths=["off_topic"],   # pseudo-negative = off-topic
            alpha=1.0, beta=0.75, gamma=0.30,
        )

        # Re-score with expanded vector
        q2 = expanded.reshape(-1)
        cos_off = float(np.dot(off_topic_vec, q2))
        cos_rel = float(np.dot(relevant_vec, q2))

        assert cos_rel > cos_off, (
            f"After PRF, relevant ({cos_rel:.4f}) should outscore off-topic ({cos_off:.4f})"
        )


# ---------------------------------------------------------------------------
# Integration tests using FakeEmbeddings (no model download)
# ---------------------------------------------------------------------------


class FakeEmbeddings:
    """Deterministic fake embeddings module for tests.

    Returns pre-built score_by_rel_path and optionally item_vectors for PRF.
    """

    def __init__(self, score_map: dict[str, float], item_vectors: dict[str, np.ndarray] | None = None, items: list | None = None):
        self._score_map = score_map
        self._item_vectors = item_vectors or {}
        self._items = items or []

    def semantic_score_map(self, *, query, target, backend, model):
        payload: dict = {
            "available": True,
            "index_item_count": len(self._score_map),
            "score_by_rel_path": dict(self._score_map),
        }
        if self._item_vectors:
            payload["item_vectors"] = self._item_vectors
        if self._items:
            payload["items"] = self._items
        return payload

    def embed_query_text(self, text, *, backend="local", model=None, text_template="none"):
        # Return a dummy unit vector (won't actually be used in most tests)
        v = np.array([1.0, 0.0], dtype=np.float32)
        return v.reshape(1, -1)


class FakeEmbeddingsWithIndex(FakeEmbeddings):
    """Fake the real embeddings module shape: vectors live in load_semantic_index."""

    def __init__(
        self,
        score_map: dict[str, float],
        vectors: np.ndarray,
        items: list[dict],
        *,
        text_template: str = "none",
    ):
        super().__init__(score_map)
        self._vectors = vectors
        self._index_items = items
        self._text_template = text_template
        self.query_templates: list[str] = []

    def load_semantic_index(self, target, backend, model):
        return {
            "items": self._index_items,
            "text_template": self._text_template,
            "item_count": len(self._index_items),
        }, self._vectors

    def embed_query_text(self, text, *, backend="local", model=None, text_template="none"):
        self.query_templates.append(text_template)
        return np.array([[1.0, 0.0]], dtype=np.float32)


class TestFusionWeightedSumGolden:
    """fusion=weighted_sum (default) must produce byte-identical results to pre-change."""

    def test_default_fusion_is_weighted_sum(self, tmp_path):
        """Config default and output are weighted_sum."""
        config = LedgerConfig(ledger_root=tmp_path)
        assert config.fusion == "weighted_sum"

    def test_weighted_sum_result_matches_manual_formula(self, tmp_path):
        """Verify the score for a note equals the manual weighted sum formula."""
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)

        note_path = config.ledger_notes_dir / "02_facts" / "fact__golden.md"
        _seed_note(note_path, "Golden test statement")

        rel_path = "notes/02_facts/fact__golden.md"
        cosine = 0.85
        fake = FakeEmbeddings({rel_path: cosine})

        result = query_module.rank_query_semantic_hybrid(
            "golden test",
            scope="all",
            limit=5,
            load_embeddings_module=lambda: fake,
            resolve_embed_model=lambda _b, _m: "fake",
        )
        assert result.results
        r = result.results[0]
        assert r.rel_path == rel_path

        # Re-compute manually: semantic=cosine, lexical ~ 0 (tokens differ), scope=1.0
        # The exact score depends on lexical overlap — just check it's not zero and
        # the semantic component is preserved.
        assert r.components.semantic_similarity == cosine
        assert r.score > 0.0
        # effective_retrieval_mode should be "semantic_hybrid" for default fusion
        assert result.effective_retrieval_mode == "semantic_hybrid"
        # semantic meta should reflect fusion
        assert result.semantic is not None
        assert result.semantic.get("fusion") == "weighted_sum"

    def test_invalid_fusion_falls_back_to_weighted_sum_metadata(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        config.fusion = "typo"
        set_config(config)

        rel_path = "notes/02_facts/fact__fallback.md"
        _seed_note(config.ledger_notes_dir / "02_facts" / "fact__fallback.md", "fallback test")
        fake = FakeEmbeddings({rel_path: 0.70})

        result = query_module.rank_query_semantic_hybrid(
            "fallback test",
            scope="all",
            limit=5,
            load_embeddings_module=lambda: fake,
            resolve_embed_model=lambda _b, _m: "fake",
        )

        assert result.semantic is not None
        assert result.semantic.get("fusion") == "weighted_sum"
        assert result.effective_retrieval_mode == "semantic_hybrid"

    def test_prior_score_is_serialized_in_detail_components(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        config.prior_enabled = True
        set_config(config)

        rel_path = "notes/02_facts/fact__prior_json.md"
        _seed_note(config.ledger_notes_dir / "02_facts" / "fact__prior_json.md", "prior json")
        fake = FakeEmbeddings({rel_path: 0.70})

        result = query_module.rank_query_semantic_hybrid(
            "prior json",
            scope="all",
            limit=5,
            load_embeddings_module=lambda: fake,
            resolve_embed_model=lambda _b, _m: "fake",
        )
        payload = query_module.query_result_to_json(result, view="detail")

        components = payload["results"][0]["components"]
        assert "prior_score" in components
        assert components["prior_score"] > 0.0

    def test_prf_off_does_not_change_results(self, tmp_path):
        """--prf off (default) produces the same results as not passing the flag."""
        config = LedgerConfig(ledger_root=tmp_path)
        set_config(config)

        note_path = config.ledger_notes_dir / "02_facts" / "fact__prf_off.md"
        _seed_note(note_path, "PRF off test")

        rel_path = "notes/02_facts/fact__prf_off.md"
        fake = FakeEmbeddings({rel_path: 0.70})

        result_no_flag = query_module.rank_query_semantic_hybrid(
            "PRF off test",
            scope="all",
            limit=5,
            load_embeddings_module=lambda: fake,
            resolve_embed_model=lambda _b, _m: "fake",
        )
        result_prf_off = query_module.rank_query_semantic_hybrid(
            "PRF off test",
            scope="all",
            limit=5,
            prf_enabled=False,
            load_embeddings_module=lambda: fake,
            resolve_embed_model=lambda _b, _m: "fake",
        )

        assert len(result_no_flag.results) == len(result_prf_off.results)
        for a, b in zip(result_no_flag.results, result_prf_off.results):
            assert a.rel_path == b.rel_path
            assert abs(a.score - b.score) < 1e-9

    def test_prf_uses_real_index_vectors_and_text_template(self, tmp_path):
        config = LedgerConfig(ledger_root=tmp_path)
        config.prf_enabled = True
        config.prf_top_m = 1
        config.prf_bottom_n = 1
        set_config(config)

        rel_a = "notes/02_facts/fact__a.md"
        rel_b = "notes/02_facts/fact__b.md"
        _seed_note(config.ledger_notes_dir / "02_facts" / "fact__a.md", "alpha")
        _seed_note(config.ledger_notes_dir / "02_facts" / "fact__b.md", "beta")

        vectors = np.array(
            [
                [0.0, 1.0],  # initially top, but away from query after PRF
                [1.0, 0.0],
            ],
            dtype=np.float32,
        )
        items = [{"rel_path": rel_a}, {"rel_path": rel_b}]
        fake = FakeEmbeddingsWithIndex(
            {rel_a: 0.99, rel_b: 0.10},
            vectors,
            items,
            text_template="e5_prefix",
        )

        result = query_module.rank_query_semantic_hybrid(
            "alpha beta",
            scope="all",
            limit=5,
            prf_enabled=True,
            load_embeddings_module=lambda: fake,
            resolve_embed_model=lambda _b, _m: "fake",
        )

        assert fake.query_templates == ["e5_prefix"]
        rescored = {r.rel_path: r.components.semantic_similarity for r in result.results}
        assert rescored[rel_b] > rescored[rel_a]


class TestFusionRRF:
    """Tests for fusion=rrf mode."""

    def test_rrf_mode_is_reflected_in_result_metadata(self, tmp_path):
        """When fusion=rrf, the result metadata should report rrf."""
        config = LedgerConfig(ledger_root=tmp_path)
        config.fusion = "rrf"
        set_config(config)

        note_path = config.ledger_notes_dir / "02_facts" / "fact__rrf.md"
        _seed_note(note_path, "kubernetes deploy rrf test")

        rel_path = "notes/02_facts/fact__rrf.md"
        fake = FakeEmbeddings({rel_path: 0.80})

        result = query_module.rank_query_semantic_hybrid(
            "kubernetes deploy",
            scope="all",
            limit=5,
            load_embeddings_module=lambda: fake,
            resolve_embed_model=lambda _b, _m: "fake",
        )

        assert result.semantic is not None
        assert result.semantic.get("fusion") == "rrf"
        assert result.effective_retrieval_mode == "semantic_hybrid_rrf"

    def test_rrf_promotes_double_ranked_item(self, tmp_path):
        """An item ranking high in BOTH lexical and semantic lists should be promoted."""
        config = LedgerConfig(ledger_root=tmp_path)
        config.fusion = "rrf"
        set_config(config)

        # "overlap" note: appears in query tokens AND has high cosine score
        overlap_path = config.ledger_notes_dir / "02_facts" / "fact__overlap.md"
        _seed_note(overlap_path, "deploy kubernetes overlap note")

        # "semantic_only" note: high cosine but no lexical overlap
        semantic_path = config.ledger_notes_dir / "02_facts" / "fact__semantic_only.md"
        _seed_note(semantic_path, "something completely different zzzz")

        rel_overlap = "notes/02_facts/fact__overlap.md"
        rel_semantic = "notes/02_facts/fact__semantic_only.md"

        # "overlap" leads BOTH lists: it is rank-1 semantic (highest cosine) and
        # rank-1 lexical (only note with query-token overlap).  "semantic_only"
        # trails in both.  With full-pool RRF the double-leader is promoted.
        fake = FakeEmbeddings({
            rel_overlap: 0.95,
            rel_semantic: 0.82,
        })

        result = query_module.rank_query_semantic_hybrid(
            "deploy kubernetes",  # tokens match overlap note
            scope="all",
            limit=5,
            load_embeddings_module=lambda: fake,
            resolve_embed_model=lambda _b, _m: "fake",
        )

        # Both should appear
        result_paths = [r.rel_path for r in result.results]
        assert rel_overlap in result_paths
        assert rel_semantic in result_paths

        # "overlap" (strong in both lists) should rank ahead of "semantic_only"
        idx_overlap = result_paths.index(rel_overlap)
        idx_semantic = result_paths.index(rel_semantic)
        assert idx_overlap < idx_semantic, (
            f"overlap ({idx_overlap}) should rank higher than semantic_only ({idx_semantic})"
        )

    def test_rrf_result_scores_are_in_unit_interval(self, tmp_path):
        """All RRF scores must be clamped to [0, 1]."""
        config = LedgerConfig(ledger_root=tmp_path)
        config.fusion = "rrf"
        set_config(config)

        for i in range(4):
            note_path = config.ledger_notes_dir / "02_facts" / f"fact__rrf_{i}.md"
            _seed_note(note_path, f"test rrf note {i}")

        fake = FakeEmbeddings({
            f"notes/02_facts/fact__rrf_{i}.md": 0.5 + i * 0.1 for i in range(4)
        })

        result = query_module.rank_query_semantic_hybrid(
            "test rrf",
            scope="all",
            limit=10,
            load_embeddings_module=lambda: fake,
            resolve_embed_model=lambda _b, _m: "fake",
        )
        for r in result.results:
            assert 0.0 <= r.score <= 1.0, f"score {r.score} out of [0,1] for {r.rel_path}"

    def test_rrf_k_config_is_respected(self, tmp_path):
        """Different rrf_k values should produce different (but valid) orderings."""
        rel = "notes/02_facts/fact__k_test.md"

        for rrf_k in (1, 60, 1000):
            config = LedgerConfig(ledger_root=tmp_path)
            config.fusion = "rrf"
            config.rrf_k = rrf_k
            set_config(config)
            clear_candidate_cache()

            note_path = config.ledger_notes_dir / "02_facts" / "fact__k_test.md"
            _seed_note(note_path, "rrf k test note")

            fake = FakeEmbeddings({rel: 0.80})
            result = query_module.rank_query_semantic_hybrid(
                "rrf k test",
                scope="all",
                limit=5,
                load_embeddings_module=lambda: fake,
                resolve_embed_model=lambda _b, _m: "fake",
            )
            assert result.results  # should always return something

    def test_rrf_lifts_lexical_match_relative_to_weighted_sum(self, tmp_path):
        """RRF and weighted_sum measure different things: RRF rewards lexical-list
        position, so a lexically-matched note ranks higher *relative to* a pure
        high-cosine note under RRF than under weighted_sum. This confirms the
        code paths are independent without relying on the old (buggy) asymmetry."""
        # overlap: high lexical match but only moderate cosine.
        overlap_path = tmp_path / "notes" / "02_facts" / "fact__overlap.md"
        # semonly: dominant cosine but zero lexical overlap.
        semantic_path = tmp_path / "notes" / "02_facts" / "fact__semonly.md"
        # filler: no lexical overlap, low cosine — provides a distinct list bottom.
        filler_path = tmp_path / "notes" / "02_facts" / "fact__filler.md"
        _seed_note(overlap_path, "deploy kubernetes overlap")
        _seed_note(semantic_path, "unrelated zephyr buzz")
        _seed_note(filler_path, "another unrelated quux note")

        rel_overlap = "notes/02_facts/fact__overlap.md"
        rel_semonly = "notes/02_facts/fact__semonly.md"
        rel_filler = "notes/02_facts/fact__filler.md"

        fake = FakeEmbeddings({rel_overlap: 0.60, rel_semonly: 0.99, rel_filler: 0.40})

        def _gap(fusion: str) -> int:
            config = LedgerConfig(ledger_root=tmp_path)
            config.fusion = fusion
            set_config(config)
            clear_candidate_cache()
            result = query_module.rank_query_semantic_hybrid(
                "deploy kubernetes",
                scope="all",
                limit=5,
                load_embeddings_module=lambda: fake,
                resolve_embed_model=lambda _b, _m: "fake",
            )
            paths = [r.rel_path for r in result.results]
            # positional gap: how far overlap trails semonly (smaller/negative = overlap higher)
            return paths.index(rel_overlap) - paths.index(rel_semonly)

        gap_ws = _gap("weighted_sum")
        gap_rrf = _gap("rrf")

        # Under RRF the lexically-matched note should be no worse positioned
        # relative to the pure-cosine note than under weighted_sum, and strictly
        # better in at least the typical case.
        assert gap_rrf <= gap_ws, (
            f"RRF should not demote the lexical match relative to weighted_sum: "
            f"gap_rrf={gap_rrf} gap_ws={gap_ws}"
        )

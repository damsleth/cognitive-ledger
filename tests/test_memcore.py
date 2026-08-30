"""Tests for the memcore package (shared retrieval contract for ledger + yaams)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import memcore
from memcore.rrf import fuse_ranked_lists, reciprocal_rank_fusion
from memcore.trust import TrustVerdict, attach_trust_verdicts

MEMCORE_DIR = Path(memcore.__file__).resolve().parent


# --- rrf ---------------------------------------------------------------------- #

def test_fuse_ranked_lists_orders_by_rrf_score():
    # "b" ranks 1st and 2nd; "a" ranks 2nd and 1st; "c" appears once.
    fused = fuse_ranked_lists([["a", "b", "c"], ["b", "a"]])
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a"]])
    assert fused == sorted(scores, key=lambda k: -scores[k])
    assert set(fused) == {"a", "b", "c"}
    assert fused[-1] == "c"


def test_fuse_ranked_lists_tie_breaks_by_first_appearance():
    # Symmetric lists: a and b tie exactly; a appears first in the inputs.
    assert fuse_ranked_lists([["a", "b"], ["b", "a"]]) == ["a", "b"]
    assert fuse_ranked_lists([["b", "a"], ["a", "b"]]) == ["b", "a"]


def test_fuse_ranked_lists_empty():
    assert fuse_ranked_lists([]) == []
    assert fuse_ranked_lists([[], []]) == []


# --- trust -------------------------------------------------------------------- #

def _result(**kw):
    base = dict(
        rel_path="notes/02_facts/fact__x.md",
        superseded_by="",
        components=SimpleNamespace(recency=0.9),
        confidence=0.9,
        trust=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_attach_trust_verdicts_annotates_in_place_without_reordering():
    results = [_result(rel_path="a"), _result(rel_path="b", confidence=0.1)]
    out = attach_trust_verdicts(results, confidence_of=lambda r: r.confidence)
    assert [r.rel_path for r in out] == ["a", "b"]
    assert results[0].trust.level == "high"
    assert results[1].trust.level == "low"
    assert isinstance(results[0].trust, TrustVerdict)


def test_attach_trust_verdicts_uses_injected_signal_callables():
    r = _result(confidence=0.9)
    attach_trust_verdicts(
        [r],
        confidence_of=lambda x: x.confidence,
        validation_count_of=lambda p: 3.0,
        contradiction_count_of=lambda p: 1.0,
    )
    # Contradiction outranks the high confidence + affirmations.
    assert r.trust.level == "low"
    assert "contradicted" in r.trust.reason


def test_attach_trust_verdicts_supersession_from_result_field():
    r = _result(superseded_by="notes/02_facts/fact__y.md")
    attach_trust_verdicts([r], confidence_of=lambda x: 0.95)
    assert r.trust.level == "low"
    assert "superseded" in r.trust.reason


# --- ledger shims ------------------------------------------------------------- #

def test_ledger_reexports_are_the_same_objects():
    from ledger import rerank as ledger_rerank
    from ledger import retrieval_types, scoring
    from ledger.query import reciprocal_rank_fusion as ledger_rrf
    from memcore import rerank as memcore_rerank
    from memcore import schema, trust

    assert retrieval_types.ScoredResult is schema.ScoredResult
    assert retrieval_types.RetrievalCandidate is schema.RetrievalCandidate
    assert retrieval_types.ScoreComponents is schema.ScoreComponents
    assert scoring.TrustVerdict is trust.TrustVerdict
    assert scoring.trust_verdict is trust.trust_verdict
    assert ledger_rrf is reciprocal_rank_fusion
    # Same dict object: mutations through the ledger path hit the real cache.
    assert ledger_rerank._RERANKER_CACHE is memcore_rerank._RERANKER_CACHE


# --- the stdlib-only rule ------------------------------------------------------ #

def test_memcore_modules_have_no_top_level_third_party_imports():
    """memcore must be installable/importable with the stdlib alone.

    Module-level imports may only touch the stdlib or memcore itself.
    Runtime-optional deps (sentence-transformers in rerank.py) must stay
    lazy — inside a function — which this scan does not descend into.
    """
    allowed = set(sys.stdlib_module_names) | {"memcore"}
    for path in sorted(MEMCORE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # top level only, on purpose
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                root = name.split(".")[0]
                assert root in allowed, (
                    f"{path.name} imports third-party module {name!r} at top "
                    "level — memcore is stdlib-only (see memcore/README.md)"
                )

"""Tests for ledger.retrieval_types module."""

import unittest
from datetime import datetime

from ledger.retrieval_types import (
  ScoreComponents,
  RetrievalCandidate,
  ScoredResult,
  TimingInfo,
  RetrievalResult,
)


class TestScoreComponents(unittest.TestCase):
  """Tests for ScoreComponents dataclass."""

  def test_defaults(self):
    sc = ScoreComponents()
    self.assertEqual(sc.bm25_score, 0.0)
    self.assertEqual(sc.lexical_match, 0.0)
    self.assertEqual(sc.tag_overlap, 0.0)
    self.assertEqual(sc.scope_match, 0.0)
    self.assertEqual(sc.recency, 0.0)
    self.assertEqual(sc.confidence, 0.0)
    self.assertEqual(sc.semantic_similarity, 0.0)
    self.assertEqual(sc.lexical_score, 0.0)
    self.assertEqual(sc.scope_component, 0.0)
    self.assertEqual(sc.recency_component, 0.0)

  def test_custom_values(self):
    sc = ScoreComponents(bm25_score=1.5, confidence=0.9, recency=0.7)
    self.assertEqual(sc.bm25_score, 1.5)
    self.assertEqual(sc.confidence, 0.9)
    self.assertEqual(sc.recency, 0.7)
    # rest still defaults
    self.assertEqual(sc.tag_overlap, 0.0)


def _make_candidate(**overrides):
  """Helper to build a RetrievalCandidate with sensible defaults."""
  defaults = dict(
    path="/notes/02_facts/fact__test.md",
    rel_path="02_facts/fact__test.md",
    type="facts",
    title="Test Fact",
    statement="A test statement",
    body="Body text here",
    updated="2026-01-01T00:00:00Z",
    updated_ts=datetime(2026, 1, 1),
    confidence=0.9,
    source="user",
    scope="dev",
    status="",
    tags=["test"],
    note_tokens={"test", "fact"},
    tag_tokens={"test"},
    attention_tokens=set(),
    snippet="A test statement",
    has_next_action_checkbox=False,
  )
  defaults.update(overrides)
  return RetrievalCandidate(**defaults)


class TestRetrievalCandidate(unittest.TestCase):
  """Tests for RetrievalCandidate dataclass."""

  def test_construction(self):
    c = _make_candidate()
    self.assertEqual(c.path, "/notes/02_facts/fact__test.md")
    self.assertEqual(c.type, "facts")
    self.assertEqual(c.confidence, 0.9)
    self.assertEqual(c.tags, ["test"])
    self.assertIsInstance(c.note_tokens, set)
    self.assertFalse(c.has_next_action_checkbox)

  def test_updated_ts_can_be_none(self):
    c = _make_candidate(updated_ts=None)
    self.assertIsNone(c.updated_ts)

  def test_empty_tags(self):
    c = _make_candidate(tags=[])
    self.assertEqual(c.tags, [])


class TestScoredResult(unittest.TestCase):
  """Tests for ScoredResult dataclass."""

  def test_inherits_from_candidate(self):
    self.assertTrue(issubclass(ScoredResult, RetrievalCandidate))

  def test_default_score_fields(self):
    sr = ScoredResult(**{
      **_make_candidate().__dict__,
    })
    self.assertEqual(sr.score, 0.0)
    self.assertEqual(sr.reasons, [])
    self.assertIsInstance(sr.components, ScoreComponents)
    self.assertEqual(sr.disclosure_level, "")

  def test_custom_score(self):
    sr = ScoredResult(
      **_make_candidate().__dict__,
      score=8.5,
      reasons=["high bm25", "tag match"],
      disclosure_level="full",
    )
    self.assertEqual(sr.score, 8.5)
    self.assertEqual(len(sr.reasons), 2)
    self.assertEqual(sr.disclosure_level, "full")

  def test_custom_components(self):
    comps = ScoreComponents(bm25_score=2.0, confidence=0.95)
    sr = ScoredResult(
      **_make_candidate().__dict__,
      score=5.0,
      components=comps,
    )
    self.assertEqual(sr.components.bm25_score, 2.0)
    self.assertEqual(sr.components.confidence, 0.95)

  def test_preserves_candidate_fields(self):
    sr = ScoredResult(
      **_make_candidate(title="My Title", scope="work").__dict__,
      score=1.0,
    )
    self.assertEqual(sr.title, "My Title")
    self.assertEqual(sr.scope, "work")


class TestTimingInfo(unittest.TestCase):
  """Tests for TimingInfo dataclass."""

  def test_defaults(self):
    t = TimingInfo()
    self.assertEqual(t.expand_ms, 0.0)
    self.assertEqual(t.candidates_ms, 0.0)
    self.assertEqual(t.prefilter_ms, 0.0)
    self.assertEqual(t.shortlist_ms, 0.0)
    self.assertEqual(t.score_ms, 0.0)
    self.assertEqual(t.total_ms, 0.0)

  def test_custom_values(self):
    t = TimingInfo(expand_ms=1.2, total_ms=15.5)
    self.assertEqual(t.expand_ms, 1.2)
    self.assertEqual(t.total_ms, 15.5)


class TestRetrievalResult(unittest.TestCase):
  """Tests for RetrievalResult dataclass."""

  def test_construction_with_defaults(self):
    rr = RetrievalResult(
      query="test query",
      scope="dev",
      retrieval_mode="two_stage",
      progressive_top_n=5,
      expanded_tokens=["test", "query"],
      expansion_events=[],
      candidate_pool_size=100,
      indexed_pool_size=None,
      prefilter_size=50,
      shortlist_size=10,
      results=[],
    )
    self.assertEqual(rr.query, "test query")
    self.assertEqual(rr.scope, "dev")
    self.assertEqual(rr.retrieval_mode, "two_stage")
    self.assertEqual(rr.progressive_top_n, 5)
    self.assertEqual(rr.expanded_tokens, ["test", "query"])
    self.assertEqual(rr.candidate_pool_size, 100)
    self.assertIsNone(rr.indexed_pool_size)
    self.assertEqual(rr.results, [])
    # check defaults
    self.assertEqual(rr.effective_retrieval_mode, "")
    self.assertIsNone(rr.semantic)
    self.assertIsInstance(rr.timing, TimingInfo)

  def test_with_results(self):
    sr = ScoredResult(**_make_candidate().__dict__, score=5.0)
    rr = RetrievalResult(
      query="q",
      scope="all",
      retrieval_mode="legacy",
      progressive_top_n=3,
      expanded_tokens=[],
      expansion_events=[],
      candidate_pool_size=10,
      indexed_pool_size=10,
      prefilter_size=5,
      shortlist_size=3,
      results=[sr],
    )
    self.assertEqual(len(rr.results), 1)
    self.assertEqual(rr.results[0].score, 5.0)

  def test_indexed_pool_size_can_be_int(self):
    rr = RetrievalResult(
      query="q",
      scope="dev",
      retrieval_mode="semantic_hybrid",
      progressive_top_n=5,
      expanded_tokens=[],
      expansion_events=[],
      candidate_pool_size=200,
      indexed_pool_size=150,
      prefilter_size=80,
      shortlist_size=20,
      results=[],
    )
    self.assertEqual(rr.indexed_pool_size, 150)

  def test_custom_timing(self):
    timing = TimingInfo(total_ms=42.0, score_ms=10.0)
    rr = RetrievalResult(
      query="q",
      scope="dev",
      retrieval_mode="legacy",
      progressive_top_n=5,
      expanded_tokens=[],
      expansion_events=[],
      candidate_pool_size=0,
      indexed_pool_size=None,
      prefilter_size=0,
      shortlist_size=0,
      results=[],
      timing=timing,
    )
    self.assertEqual(rr.timing.total_ms, 42.0)
    self.assertEqual(rr.timing.score_ms, 10.0)

  def test_semantic_dict(self):
    rr = RetrievalResult(
      query="q",
      scope="dev",
      retrieval_mode="semantic_hybrid",
      progressive_top_n=5,
      expanded_tokens=[],
      expansion_events=[],
      candidate_pool_size=0,
      indexed_pool_size=None,
      prefilter_size=0,
      shortlist_size=0,
      results=[],
      semantic={"backend": "local", "model": "bge-micro"},
    )
    self.assertEqual(rr.semantic["backend"], "local")


if __name__ == "__main__":
  unittest.main()

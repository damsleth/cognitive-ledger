"""Tests for word_count cost hints on retrieval candidates."""

import unittest

from ledger.retrieval_types import RetrievalCandidate, ScoredResult, ScoreComponents


class TestWordCount(unittest.TestCase):

    def test_default_zero(self):
        c = RetrievalCandidate(
            path="", rel_path="", type="fact", title="test",
            statement="", body="", updated="", updated_ts=None,
            confidence=0.9, source="user", scope="dev", status="",
            tags=[], note_tokens=set(), tag_tokens=set(),
            attention_tokens=set(), snippet="",
            has_next_action_checkbox=False,
        )
        assert c.word_count == 0

    def test_explicit_count(self):
        c = RetrievalCandidate(
            path="", rel_path="", type="fact", title="test",
            statement="", body="one two three four five", updated="",
            updated_ts=None, confidence=0.9, source="user", scope="dev",
            status="", tags=[], note_tokens=set(), tag_tokens=set(),
            attention_tokens=set(), snippet="",
            has_next_action_checkbox=False, word_count=5,
        )
        assert c.word_count == 5

    def test_scored_result_inherits(self):
        sr = ScoredResult(
            path="", rel_path="", type="fact", title="test",
            statement="", body="a b c", updated="", updated_ts=None,
            confidence=0.9, source="user", scope="dev", status="",
            tags=[], note_tokens=set(), tag_tokens=set(),
            attention_tokens=set(), snippet="",
            has_next_action_checkbox=False, word_count=3,
            score=0.8, reasons=[], components=ScoreComponents(),
        )
        assert sr.word_count == 3


if __name__ == "__main__":
    unittest.main()

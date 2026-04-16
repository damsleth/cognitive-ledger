"""Tests for three-layer retrieval view system."""

import unittest

from ledger.query import (
    _result_index_fields,
    _result_context_fields,
    _result_detail_fields,
    query_result_to_json,
    format_query_results_human,
)
from ledger.retrieval_types import (
    RetrievalCandidate,
    RetrievalResult,
    ScoreComponents,
    ScoredResult,
    TimingInfo,
)


def _make_scored_result(**overrides) -> ScoredResult:
    defaults = dict(
        path="/tmp/notes/02_facts/fact__test.md",
        rel_path="notes/02_facts/fact__test.md",
        type="fact",
        title="Test fact",
        statement="This is a test statement.",
        body="This is the full body of the note with several words.",
        updated="2026-04-16T00:00:00Z",
        updated_ts=None,
        confidence=0.9,
        source="user",
        scope="dev",
        status="",
        tags=["test", "fact"],
        note_tokens={"test", "fact", "body"},
        tag_tokens={"test", "fact"},
        attention_tokens={"test"},
        snippet="This is a test statement.",
        has_next_action_checkbox=False,
        word_count=11,
        score=0.85,
        reasons=["lexical_overlap=3"],
        components=ScoreComponents(bm25_score=0.5, lexical_match=0.8),
    )
    defaults.update(overrides)
    return ScoredResult(**defaults)


def _make_payload(results=None) -> RetrievalResult:
    return RetrievalResult(
        query="test query",
        scope="all",
        retrieval_mode="legacy",
        progressive_top_n=0,
        expanded_tokens=["test", "query"],
        expansion_events=[],
        candidate_pool_size=50,
        indexed_pool_size=None,
        prefilter_size=50,
        shortlist_size=10,
        results=results or [_make_scored_result()],
        timing=TimingInfo(total_ms=5.0),
    )


class TestIndexView(unittest.TestCase):

    def test_has_compact_fields(self):
        item = _make_scored_result()
        data = _result_index_fields(item)
        assert "path" in data
        assert "title" in data
        assert "type" in data
        assert "score" in data
        assert "word_count" in data
        assert "updated" in data
        assert "confidence" in data
        assert "scope" in data

    def test_excludes_body(self):
        data = _result_index_fields(_make_scored_result())
        assert "body" not in data
        assert "statement" not in data
        assert "snippet" not in data
        assert "tags" not in data
        assert "note_tokens" not in data
        assert "reasons" not in data

    def test_includes_status_for_loops(self):
        item = _make_scored_result(type="loop", status="open")
        data = _result_index_fields(item)
        assert data["status"] == "open"

    def test_omits_status_for_non_loops(self):
        data = _result_index_fields(_make_scored_result(type="fact"))
        assert "status" not in data


class TestContextView(unittest.TestCase):

    def test_has_index_plus_context_fields(self):
        data = _result_context_fields(_make_scored_result())
        # Index fields
        assert "path" in data
        assert "score" in data
        assert "word_count" in data
        # Context fields
        assert "statement" in data
        assert "snippet" in data
        assert "tags" in data
        assert "source" in data
        assert "reasons" in data

    def test_excludes_body_and_tokens(self):
        data = _result_context_fields(_make_scored_result())
        assert "body" not in data
        assert "note_tokens" not in data
        assert "components" not in data


class TestDetailView(unittest.TestCase):

    def test_has_everything(self):
        data = _result_detail_fields(_make_scored_result())
        assert "path" in data
        assert "body" in data
        assert "statement" in data
        assert "note_tokens" in data
        assert "components" in data
        assert "tags" in data
        assert "word_count" in data


class TestQueryResultToJson(unittest.TestCase):

    def test_default_view_is_context(self):
        out = query_result_to_json(_make_payload())
        assert out["view"] == "context"
        result = out["results"][0]
        assert "statement" in result
        assert "body" not in result

    def test_index_view(self):
        out = query_result_to_json(_make_payload(), view="index")
        assert out["view"] == "index"
        result = out["results"][0]
        assert "word_count" in result
        assert "body" not in result
        assert "statement" not in result

    def test_detail_view(self):
        out = query_result_to_json(_make_payload(), view="detail")
        assert out["view"] == "detail"
        result = out["results"][0]
        assert "body" in result
        assert "components" in result


class TestFormatHuman(unittest.TestCase):

    def test_index_view_compact(self):
        output = format_query_results_human(_make_payload(), view="index")
        assert "view: index" in output
        # Index line should not have reasons
        lines = [l for l in output.splitlines() if l.startswith("- ")]
        assert len(lines) == 1
        assert "lexical_overlap" not in lines[0]

    def test_context_view_default(self):
        output = format_query_results_human(_make_payload())
        # Default view doesn't print "view:" line
        assert "view:" not in output

    def test_detail_view_shows_body(self):
        output = format_query_results_human(_make_payload(), view="detail")
        assert "view: detail" in output
        assert "body:" in output
        assert "statement:" in output


if __name__ == "__main__":
    unittest.main()

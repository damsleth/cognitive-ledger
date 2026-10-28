from dataclasses import fields

import pytest

from ledger.retrieval import _copy_candidate, _scored_result
from ledger.query import _scored_result_from_candidate
from ledger.retrieval_types import RetrievalCandidate, ScoreComponents


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("convert", [_copy_candidate, _scored_result, _scored_result_from_candidate])
def test_candidate_fields_survive_scoring(convert, legacy):
    candidate = _copy_candidate({"path": "note.md", "tags": ["trust"],
                                 "note_tokens": {"trust"}, "provenance": "validated",
                                 "via": "yaams", "validation_count": 3.0})
    values = {f.name: getattr(candidate, f.name) for f in fields(RetrievalCandidate)}
    source = values if legacy else candidate
    kwargs = {} if convert is _copy_candidate else dict(
        score=0.8, reasons=["match"], components=ScoreComponents())
    result = convert(source, **kwargs)
    assert {f.name: getattr(result, f.name) for f in fields(RetrievalCandidate)} == values
    assert result.tags is not candidate.tags
    assert result.note_tokens is not candidate.note_tokens

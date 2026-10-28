"""Adapters operations for retrieval."""

from __future__ import annotations

from typing import Any
from ledger.retrieval_types import RetrievalCandidate
from ledger.retrieval_types import ScoreComponents
from ledger.retrieval_types import ScoredResult
from typing import Union
from dataclasses import fields


CandidateLike = Union[RetrievalCandidate, ScoredResult, dict[str, Any]]



def _candidate_value(candidate: CandidateLike, field: str, default: Any = None) -> Any:
    if isinstance(candidate, dict):
        return candidate.get(field, default)
    return getattr(candidate, field, default)



def _candidate_sort_key(candidate: CandidateLike) -> tuple[str, str]:
    return (
        str(_candidate_value(candidate, "updated", "") or ""),
        str(_candidate_value(candidate, "path", "") or ""),
    )



def _candidate_fields(candidate: CandidateLike) -> dict[str, Any]:
    """Copy the complete candidate contract, normalizing legacy dictionaries."""
    values = {}
    coercers = {"str": str, "float": float, "int": int, "bool": bool,
                "list[str]": list, "set[str]": set}
    for field in fields(RetrievalCandidate):
        value = _candidate_value(candidate, field.name)
        coerce = coercers.get(field.type)
        if isinstance(candidate, dict) and coerce is not None:
            value = coerce(value) if value else coerce()
        elif isinstance(value, (list, set)):
            value = value.copy()
        values[field.name] = value
    return values



def _copy_candidate(candidate: CandidateLike) -> RetrievalCandidate:
    return RetrievalCandidate(**_candidate_fields(candidate))



def _scored_result(
    candidate: CandidateLike,
    score: float,
    reasons: list[str],
    components: ScoreComponents,
) -> ScoredResult:
    return ScoredResult(
        **_candidate_fields(candidate),
        score=score,
        reasons=list(reasons),
        components=components,
    )


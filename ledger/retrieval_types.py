"""Structured retrieval result types.

The shared result contract (``ScoreComponents``, ``RetrievalCandidate``,
``ScoredResult``) moved to the separately installable ``memcore`` package —
yaams consumes it too — and is re-exported here so existing imports keep
working. The engine-internal types (``TimingInfo``, ``RetrievalResult``)
describe one ledger retrieval run and stay local.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from memcore.schema import (  # noqa: F401  (re-exported contract types)
    RetrievalCandidate,
    ScoreComponents,
    ScoredResult,
)

__all__ = [
    "ScoreComponents",
    "RetrievalCandidate",
    "ScoredResult",
    "TimingInfo",
    "RetrievalResult",
]


@dataclass
class TimingInfo:
    expand_ms: float = 0.0
    candidates_ms: float = 0.0
    prefilter_ms: float = 0.0
    shortlist_ms: float = 0.0
    score_ms: float = 0.0
    total_ms: float = 0.0


@dataclass
class RetrievalResult:
    query: str
    scope: str
    retrieval_mode: str
    progressive_top_n: int
    expanded_tokens: list[str]
    expansion_events: list[dict[str, object]]
    candidate_pool_size: int
    indexed_pool_size: int | None
    prefilter_size: int
    shortlist_size: int
    results: list[ScoredResult]
    effective_retrieval_mode: str = ""
    semantic: dict | None = None
    timing: TimingInfo = field(default_factory=TimingInfo)

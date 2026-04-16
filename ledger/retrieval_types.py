"""Structured retrieval result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ScoreComponents:
    bm25_score: float = 0.0
    lexical_match: float = 0.0
    tag_overlap: float = 0.0
    scope_match: float = 0.0
    recency: float = 0.0
    confidence: float = 0.0
    semantic_similarity: float = 0.0
    lexical_score: float = 0.0
    scope_component: float = 0.0
    recency_component: float = 0.0


@dataclass
class RetrievalCandidate:
    path: str
    rel_path: str
    type: str
    title: str
    statement: str
    body: str
    updated: str
    updated_ts: datetime | None
    confidence: float
    source: str
    scope: str
    status: str
    tags: list[str]
    note_tokens: set[str]
    tag_tokens: set[str]
    attention_tokens: set[str]
    snippet: str
    has_next_action_checkbox: bool
    word_count: int = 0


@dataclass
class ScoredResult(RetrievalCandidate):
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    components: ScoreComponents = field(default_factory=ScoreComponents)
    disclosure_level: str = ""


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

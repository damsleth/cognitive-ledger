"""Structured retrieval result types shared by ledger and yaams.

Moved verbatim from ``ledger/retrieval_types.py``. ``ScoredResult`` is the
wire schema yaams mirrors in its hybrid retrieval; changing a field here is a
cross-repo contract change.

The ledger-engine-internal types (``TimingInfo``, ``RetrievalResult``) stay
in ``ledger/retrieval_types.py`` — they describe one engine run, not the
shared result contract.
"""

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
    prior_score: float = 0.0
    signal_score: float = 0.0


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
    # Bitemporal validity fields (optional; absent on legacy notes)
    valid_from: str = ""
    valid_to: str = ""
    superseded_by: str = ""
    created_ts: datetime | None = None
    """Parsed creation timestamp from the `created` frontmatter field.

    Optional; None when the field is absent. Used by the prior score to
    derive note age from creation rather than last-update date.
    Notes without this field fall back to ``updated_ts`` for age estimation.
    """
    # Provenance-weighted confidence inputs (plan 42; optional/back-compatible).
    provenance: str = ""
    """Origin-act trust class (explicit_statement|validated|corrected|observed|
    imported|inferred). Empty on legacy notes — derived from source/via at score
    time via ``ledger.scoring.derive_provenance``."""
    via: str = ""
    """Ingest channel (claude-memory|obsidian|yaams|folder|manual). Used to derive
    a default provenance for legacy notes; empty when unknown."""
    validation_count: float = 0.0
    """Per-note affirmation count snapshot. Forward-compatible field; the live
    score path reads the authoritative count from the signal summary and only
    falls back to this when no summary is in scope."""


@dataclass
class ScoredResult(RetrievalCandidate):
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    components: ScoreComponents = field(default_factory=ScoreComponents)
    disclosure_level: str = ""
    # Display-only trust verdict (plan 46); never feeds ranking. None until
    # populated post-scoring. Typed loosely to avoid importing scoring here.
    trust: object | None = None

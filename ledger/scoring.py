"""Composable scoring primitives for Cognitive Ledger retrieval.

This module contains pure, stateless functions that compute individual scoring
components (lexical overlap, scope match, intent adjustments, clamping).  It
deliberately has **no config reads and no I/O** — every function takes plain
Python values and returns plain Python values so the components can be unit-
tested in isolation and reused across retrieval modes.

Two intent ladders are provided:

- ``FINE_LADDER`` — used by ``score_candidate`` (lexical full-ranking pass).
  Larger deltas because the full-ranking pass can absorb bigger adjustments
  without flipping the relevance order.

- ``COARSE_LADDER`` — used by ``coarse_candidate_score`` (shortlist pre-pass).
  Smaller deltas to avoid over-filtering before we have the full signal.

Callers import the ladder that matches their pass and call
``intent_adjustments`` with it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Protocol, Any


# ---------------------------------------------------------------------------
# QueryCtx
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QueryCtx:
    """Per-query inputs shared by all scorers.

    Constructed once per retrieval call and threaded through scoring functions
    to avoid passing many positional arguments.
    """

    query_tokens: frozenset[str]
    query_scope: str
    history_mode: bool
    loop_mode: bool
    preference_mode: bool
    now_dt: dt.datetime


# ---------------------------------------------------------------------------
# Overlap components
# ---------------------------------------------------------------------------

def overlap_components(
    note_tokens: set[str],
    tag_tokens: set[str],
    query_tokens: set[str] | frozenset[str],
) -> tuple[float, float, int, int]:
    """Compute normalised lexical and tag overlap ratios.

    Returns ``(lexical_match, tag_overlap, lexical_overlap_count,
    tag_overlap_count)`` where:

    - ``lexical_match`` = ``|note_tokens ∩ query_tokens| / |query_tokens|``
    - ``tag_overlap``   = ``|tag_tokens  ∩ query_tokens| / |query_tokens|``

    All ratios are 0.0 when *query_tokens* is empty.
    """
    if query_tokens:
        lexical_overlap_count = len(note_tokens & query_tokens)
        lexical_match = lexical_overlap_count / len(query_tokens)
        tag_overlap_count = len(tag_tokens & query_tokens)
        tag_overlap = tag_overlap_count / len(query_tokens)
    else:
        lexical_overlap_count = 0
        lexical_match = 0.0
        tag_overlap_count = 0
        tag_overlap = 0.0

    return lexical_match, tag_overlap, lexical_overlap_count, tag_overlap_count


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------

def canonical_scope(scope: Any) -> str:
    """Normalise a raw scope value to a canonical lowercase string."""
    lowered = str(scope or "").strip().lower()
    if lowered == "life":
        return "personal"
    return lowered


def scope_matches(note_scope: str, query_scope: str) -> bool:
    """Return True if *note_scope* satisfies *query_scope*."""
    if query_scope == "all":
        return True
    return canonical_scope(note_scope) == query_scope


def scope_component(note_scope: str, query_scope: str) -> float:
    """Return 1.0 when scopes match, 0.0 otherwise.

    *query_scope* ``"all"`` always returns 1.0.
    """
    return 1.0 if scope_matches(note_scope, query_scope) else 0.0


# ---------------------------------------------------------------------------
# Intent ladder
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _IntentRung:
    """A single rung in an intent ladder."""

    condition: str   # human-readable label (used in tests)
    delta: float     # adjustment applied when condition fires
    reason: str      # emitted into reasons list when include_reasons=True


@dataclass(frozen=True)
class IntentLadder:
    """An immutable sequence of intent adjustment rules.

    Each rung is evaluated independently; multiple rungs may fire for the
    same candidate.  Use ``intent_adjustments`` to evaluate the ladder.
    """

    rungs: tuple[_IntentRung, ...]


def intent_adjustments(
    ladder: IntentLadder,
    candidate_type: str,
    status: str,
    query_scope: str,
    sm: float,          # scope_match value (1.0 or 0.0)
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    include_reasons: bool = True,
) -> tuple[float, list[str]]:
    """Apply *ladder* rules and return ``(total_delta, fired_reasons)``.

    Each rung in the ladder is checked in order; multiple may fire.  The
    function returns the cumulative delta and, when *include_reasons* is True,
    a list of reason strings for the rules that fired.
    """
    total = 0.0
    reasons: list[str] = []

    for rung in ladder.rungs:
        cond = rung.condition
        fired = False

        if cond == "scope_boost":
            fired = query_scope != "all" and sm >= 1.0
        elif cond == "scope_demote":
            fired = query_scope != "all" and sm < 1.0
        elif cond == "closed_loop_penalty":
            fired = candidate_type == "loop" and status == "closed" and not history_mode
        elif cond == "history_closed_loop_boost":
            fired = history_mode and candidate_type == "loop" and status == "closed"
        elif cond == "history_open_loop_demote":
            fired = history_mode and candidate_type == "loop" and status == "open"
        elif cond == "open_loop_intent_boost":
            fired = loop_mode and candidate_type == "loop" and status == "open"
        elif cond == "preference_intent_boost":
            fired = preference_mode and candidate_type == "pref"
        elif cond == "preference_non_pref_demote":
            # 'fact' notes are NOT demoted even in preference mode — they
            # are neutral.  Only non-pref, non-fact types are penalised.
            fired = preference_mode and candidate_type not in {"pref", "fact"}
        # coarse-only rungs
        elif cond == "coarse_scope_boost":
            fired = query_scope != "all" and sm >= 1.0
        elif cond == "coarse_scope_demote":
            fired = query_scope != "all" and sm < 1.0
        elif cond == "coarse_closed_loop_penalty":
            fired = candidate_type == "loop" and status == "closed" and not history_mode
        elif cond == "coarse_history_closed_loop_boost":
            fired = history_mode and candidate_type == "loop" and status == "closed"
        elif cond == "coarse_loop_intent_boost":
            fired = loop_mode and candidate_type == "loop" and status == "open"
        elif cond == "coarse_preference_intent_boost":
            fired = preference_mode and candidate_type == "pref"

        if fired:
            total += rung.delta
            if include_reasons:
                reasons.append(rung.reason)

    return total, reasons


# ---------------------------------------------------------------------------
# Standard ladders
# ---------------------------------------------------------------------------

FINE_LADDER: IntentLadder = IntentLadder(
    rungs=(
        _IntentRung("scope_boost",               +0.05, "scope_boost"),
        _IntentRung("scope_demote",              -0.05, "scope_demote"),
        _IntentRung("closed_loop_penalty",       -0.20, "closed_loop_penalty"),
        _IntentRung("history_closed_loop_boost", +0.12, "history_closed_loop_boost"),
        _IntentRung("history_open_loop_demote",  -0.05, "history_open_loop_demote"),
        _IntentRung("open_loop_intent_boost",    +0.07, "open_loop_intent_boost"),
        _IntentRung("preference_intent_boost",   +0.07, "preference_intent_boost"),
        _IntentRung("preference_non_pref_demote",-0.05, "preference_non_pref_demote"),
    )
)

COARSE_LADDER: IntentLadder = IntentLadder(
    rungs=(
        _IntentRung("coarse_scope_boost",                +0.03, "scope_boost"),
        _IntentRung("coarse_scope_demote",               -0.03, "scope_demote"),
        _IntentRung("coarse_closed_loop_penalty",        -0.05, "closed_loop_penalty"),
        _IntentRung("coarse_history_closed_loop_boost",  +0.05, "history_closed_loop_boost"),
        _IntentRung("coarse_loop_intent_boost",          +0.04, "open_loop_intent_boost"),
        _IntentRung("coarse_preference_intent_boost",    +0.04, "preference_intent_boost"),
    )
)


# ---------------------------------------------------------------------------
# Clamp helper
# ---------------------------------------------------------------------------

def clamp01(value: float) -> float:
    """Clamp *value* to [0.0, 1.0]."""
    return max(0.0, min(1.0, value))


# ---------------------------------------------------------------------------
# Provenance-weighted confidence (plan 42)
# ---------------------------------------------------------------------------

# Trustworthiness of the *origin act* that created a note, independent of the
# `source`/`via` channel fields (which answer "who/what channel"). Absent on
# legacy notes — `derive_provenance` supplies a default from source/via.
PROVENANCE_WEIGHTS: dict[str, float] = {
    "explicit_statement": 1.00,   # user stated it directly
    "validated": 0.95,            # confirmed against another source
    "corrected": 0.90,            # written as a correction of a prior note
    "observed": 0.85,             # inferred from observed behaviour
    "imported": 0.80,             # bulk-imported (obsidian/folder/claude-memory)
    "inferred": 0.70,             # model-inferred, unconfirmed
}

# Weight applied to a note whose provenance is unknown/unrecognised. Equal to
# the most conservative known weight ("inferred") so an unrecognised value never
# scores a note *higher* than an explicit low-trust provenance.
PROVENANCE_WEIGHT_FLOOR: float = 0.70


def derive_provenance(source: str, via: str, provenance: str) -> str:
    """Resolve a note's provenance class.

    An explicit, recognised ``provenance`` value always wins. Otherwise derive a
    default from the existing ``source``/``via`` frontmatter so legacy notes
    (which predate the field) keep sensible weights:

    - ``source == "user"``      → ``explicit_statement``
    - ``source == "inferred"``  → ``inferred``
    - ``via in {obsidian, claude-memory, folder}`` → ``imported``
    - otherwise                 → ``observed``

    Pure; no I/O.
    """
    explicit = (provenance or "").strip().lower()
    if explicit in PROVENANCE_WEIGHTS:
        return explicit
    s = (source or "").strip().lower()
    if s == "user":
        return "explicit_statement"
    if s == "inferred":
        return "inferred"
    v = (via or "").strip().lower()
    if v in {"obsidian", "claude-memory", "folder"}:
        return "imported"
    return "observed"


def effective_confidence(
    base_confidence: float,
    provenance: str,
    validation_count: float,
    *,
    boost_per_signal: float = 0.03,
    boost_cap: float = 0.15,
) -> float:
    """Derive an effective confidence from base confidence, provenance and validations.

    ``effective = base × provenance_weight + min(boost_per·validations, boost_cap)``

    where ``provenance_weight`` comes from ``PROVENANCE_WEIGHTS`` (falling back to
    ``PROVENANCE_WEIGHT_FLOOR`` for unrecognised values). ``validation_count`` is
    the per-note affirmation count (corrections are *not* counted — they flip a
    note's provenance toward ``corrected`` elsewhere, they do not boost it).

    Result is clamped to [0.0, 1.0]. Pure; no I/O.
    """
    base = clamp01(base_confidence)
    weight = PROVENANCE_WEIGHTS.get((provenance or "").strip().lower(), PROVENANCE_WEIGHT_FLOOR)
    boost = min(max(0.0, boost_per_signal) * max(0.0, validation_count), max(0.0, boost_cap))
    return clamp01(base * weight + boost)


# ---------------------------------------------------------------------------
# Scorer protocol (for future A/B wrapping)
# ---------------------------------------------------------------------------

class Scorer(Protocol):
    """Interface for a scoring component.

    A ``Scorer`` takes a candidate (as a plain dict) and a ``QueryCtx`` and
    returns a ``(score_delta, reason_label_or_empty)`` pair.  Multiple scorers
    can be composed by summing their deltas.
    """

    def __call__(
        self,
        candidate: dict[str, Any],
        ctx: QueryCtx,
    ) -> tuple[float, str]: ...

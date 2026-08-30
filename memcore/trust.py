"""Trust verdicts for retrieval results.

``TrustVerdict`` and ``trust_verdict`` moved verbatim from
``ledger/scoring.py`` (plan 46). ``attach_trust_verdicts`` is the
generalized core of ``ledger/retrieval.py``'s function of the same name:
the ledger-specific inputs (config gate, provenance-weighted confidence,
signal-summary lookups) are injected as callables, so ledger and yaams can
share the annotation logic while resolving those inputs their own way.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def candidate_value(candidate: Any, field: str, default: Any = None) -> Any:
    """Read ``field`` off a result that may be a dict or a dataclass/object."""
    if isinstance(candidate, dict):
        return candidate.get(field, default)
    return getattr(candidate, field, default)


@dataclass(frozen=True)
class TrustVerdict:
    """A human-readable trust assessment for a retrieval result.

    ``level`` is one of ``high`` | ``medium`` | ``low``. ``reason`` is a short
    sentence explaining the level. ``score`` is a continuous [0,1] proxy for
    sorting/debugging only — it never feeds ranking.
    """

    level: str
    reason: str
    score: float = 0.0


def _affirmations(count: float) -> float | int:
    """Render an affirmation count without a spurious trailing .0."""
    return int(count) if count == int(count) else round(count, 1)


def trust_verdict(
    *,
    effective_confidence: float,
    validation_count: float,
    contradicted: bool,
    superseded: bool,
    recency: float,
    high_confidence: float = 0.85,
    medium_confidence: float = 0.60,
) -> TrustVerdict:
    """Collapse trust signals into a verdict. Pure, deterministic, display-only.

    Precedence:
    1. superseded or contradicted → ``low`` (names the issue)
    2. high confidence and at least one affirmation → ``high``
    3. moderate confidence → ``medium``
    4. otherwise → ``low`` (``stale`` when recency is very low)
    """
    conf = _clamp01(effective_confidence)
    if superseded:
        return TrustVerdict("low", "superseded by a newer note", conf * 0.5)
    if contradicted:
        return TrustVerdict("low", "contradicted by another note", conf * 0.5)
    if conf >= high_confidence and validation_count >= 1:
        return TrustVerdict("high", f"high-confidence, affirmed {_affirmations(validation_count)}×", conf)
    if conf >= high_confidence:
        return TrustVerdict("high", "high-confidence", conf)
    if conf >= medium_confidence:
        if validation_count >= 1:
            return TrustVerdict(
                "medium", f"moderate confidence, affirmed {_affirmations(validation_count)}×", conf
            )
        return TrustVerdict("medium", "moderate confidence, unaffirmed", conf)
    if recency < 0.15:
        return TrustVerdict("low", "low confidence and stale", conf)
    return TrustVerdict("low", "low confidence", conf)


def attach_trust_verdicts(
    results: Iterable[Any],
    *,
    confidence_of: Callable[[Any], float],
    validation_count_of: Callable[[str], float] | None = None,
    contradiction_count_of: Callable[[str], float] | None = None,
    high_confidence: float = 0.85,
    medium_confidence: float = 0.60,
) -> list[Any]:
    """Attach a display-only trust verdict to each result.

    Sets ``result.trust`` in place and returns the results as a list. This
    NEVER reorders results — it only annotates them — so it is safe to call
    after the final ranking is fixed.

    The caller injects how the per-result inputs are resolved:

    - ``confidence_of(result)``: effective confidence in [0, 1] (the ledger
      passes provenance-weighted confidence; a plain reader can return the
      raw ``confidence`` field).
    - ``validation_count_of(rel_path)`` / ``contradiction_count_of(rel_path)``:
      per-note counts from a feedback store; omit either (None) when no such
      store is in scope and 0.0 / not-contradicted is assumed.

    Recency is read from ``result.components.recency`` and supersession from
    the result's ``superseded_by`` field, matching the ledger's result schema.
    """
    results = list(results)
    for result in results:
        conf = confidence_of(result)
        components = getattr(result, "components", None)
        recency = float(getattr(components, "recency", 0.0) or 0.0)
        rel_path = str(candidate_value(result, "rel_path", "") or "")
        validations = 0.0
        contradicted = False
        if rel_path:
            if validation_count_of is not None:
                validations = float(validation_count_of(rel_path))
            if contradiction_count_of is not None:
                contradicted = float(contradiction_count_of(rel_path)) > 0
        superseded = bool(str(candidate_value(result, "superseded_by", "") or ""))
        result.trust = trust_verdict(
            effective_confidence=conf,
            validation_count=validations,
            contradicted=contradicted,
            superseded=superseded,
            recency=recency,
            high_confidence=high_confidence,
            medium_confidence=medium_confidence,
        )
    return results

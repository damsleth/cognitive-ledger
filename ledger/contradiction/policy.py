from __future__ import annotations
from dataclasses import dataclass
from ledger.layout import NOTE_LAYOUTS
from pathlib import Path
from typing import Any
import datetime as dt
import enum


_SCAN_NOTE_TYPES = frozenset(
    layout.name for layout in NOTE_LAYOUTS.values()
    if layout.bitemporal and layout is not NOTE_LAYOUTS["preferences"]
)


_AUTO_SUPERSEDE_SUBTYPES = _SCAN_NOTE_TYPES - {NOTE_LAYOUTS["identity"].name}


_IDENTITY_SUBDIR = NOTE_LAYOUTS['identity'].subdir


_TIMESTAMP_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _from_iso(s: str) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s, _TIMESTAMP_FMT).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


class Decision(enum.Enum):
    """Action produced by decide() for a contradiction pair."""
    IGNORE = "ignore"
    REVIEW = "review"
    SUPERSEDE = "supersede"  # old_note superseded by new_note


@dataclass
class PairFacts:
    """All facts about a (candidate_note, neighbor_note) pair needed to decide.

    Designed so that decide() is a pure function with no IO.

    Attributes:
        contradiction_score: Bidirectional NLI contradiction probability in [0,1].
        is_candidate_newer: True when candidate's temporal anchor is strictly
            later than neighbor's (different timestamps); None when ambiguous
            (equal or both absent).
        candidate_confidence: Confidence of the candidate note (0-1).
        neighbor_confidence: Confidence of the neighbor note (0-1).
        neighbor_is_identity: True when neighbor lives in 01_identity/.
        candidate_is_identity: True when candidate lives in 01_identity/.
        either_lang_no: True when either note has lang:no (or mixed lang:no).
        neighbor_already_superseded: True when neighbor.superseded_by is set
            (stale note that was already replaced — should be skipped).
    """
    contradiction_score: float
    is_candidate_newer: bool | None  # None = ambiguous
    candidate_confidence: float
    neighbor_confidence: float
    neighbor_is_identity: bool
    candidate_is_identity: bool
    either_lang_no: bool
    neighbor_already_superseded: bool = False


def decide(
    pair: PairFacts,
    *,
    auto_threshold: float,
    review_threshold: float,
    protect_higher_confidence: bool,
    auto_supersede: bool = False,
) -> Decision:
    """Decide what action to take for a contradiction pair.

    Pure function (no IO, no config access) — all thresholds/flags are explicit
    parameters so the full decision matrix can be tested without side effects.

    Decision rules (in priority order):
    1. Already superseded neighbor → IGNORE.
    2. Score < review_threshold → IGNORE.
    3. Either note is identity → REVIEW (hard rule 1).
    4. Auto-supersede off, or either note lang:no → REVIEW.
    5. Score >= auto threshold AND unambiguous ordering:
       a. If protect_higher_confidence AND neighbor is OLDER AND has strictly
          higher confidence than candidate → downgrade to REVIEW.
       b. Otherwise → SUPERSEDE.
    6. Score in [review_threshold, auto_threshold) OR ambiguous ordering
       → REVIEW.
    """
    if pair.neighbor_already_superseded:
        return Decision.IGNORE

    score = pair.contradiction_score

    if score < review_threshold:
        return Decision.IGNORE

    # Hard rule: identity notes always go to review
    if pair.neighbor_is_identity or pair.candidate_is_identity:
        return Decision.REVIEW

    if not auto_supersede or pair.either_lang_no:
        return Decision.REVIEW

    if score >= auto_threshold and pair.is_candidate_newer is True:
        # Candidate is strictly newer than neighbor. Check confidence guard.
        if (
            protect_higher_confidence
            and pair.neighbor_confidence > pair.candidate_confidence
        ):
            return Decision.REVIEW
        return Decision.SUPERSEDE

    return Decision.REVIEW


def _temporal_anchor(fm: dict[str, Any]) -> dt.datetime | None:
    """Return the note's temporal anchor for ordering.

    Priority: valid_from > created > None.
    """
    for key in ("valid_from", "created"):
        raw = str(fm.get(key, "")).strip()
        if raw and raw not in ("null", "~", "None", ""):
            ts = _from_iso(raw)
            if ts is not None:
                return ts
            # Try date-only
            try:
                d = dt.date.fromisoformat(raw.split("T")[0])
                return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
            except ValueError:
                pass
    return None


def _is_lang_no(fm: dict[str, Any]) -> bool:
    """Return True if the note's lang field is 'no' or starts with 'no'."""
    lang = str(fm.get("lang", "")).strip().lower()
    return lang == "no" or lang.startswith("no-")


def _is_superseded(fm: dict[str, Any]) -> bool:
    """Return True if superseded_by is set to a non-null value."""
    val = str(fm.get("superseded_by", "")).strip()
    return val not in ("", "null", "~", "None")


def _is_identity_path(abs_path: Path) -> bool:
    return abs_path.parent.name == _IDENTITY_SUBDIR


def _type_compatible(type_a: str | None, type_b: str | None) -> bool:
    """True when two note types can meaningfully contradict each other.

    Same-type pairs are always compatible.  Cross-type pairs are not (a
    preference cannot contradict a goal in a meaningful NLI sense).
    """
    return type_a is not None and type_a == type_b


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _confidence(fm: dict[str, Any]) -> float:
    """Return a lint-tolerant confidence value for decision guards."""
    try:
        return _clamp(float(fm.get("confidence", 0.5)))
    except (TypeError, ValueError):
        return 0.5

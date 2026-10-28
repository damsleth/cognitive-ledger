"""Filters operations for retrieval."""

from __future__ import annotations

from typing import Any
from ledger.layout import NOTE_LAYOUTS
from ledger.retrieval_types import RetrievalCandidate
import warnings as _warnings
import datetime as dt
from ledger.scoring import scope_matches
from ledger.ranking.adapters import CandidateLike, _candidate_sort_key, _candidate_value

from ledger.ranking.settings import _cfg


def apply_temporal_filter(
    candidates: list[RetrievalCandidate],
    *,
    as_of: dt.datetime | None,
    now_dt: dt.datetime,
) -> list[RetrievalCandidate]:
    """Filter candidates by valid-time interval.

    Default path (as_of=None):
    - Notes with NO validity fields pass through unchanged (golden requirement).
    - Notes with a valid_to in the past (< now_dt) are excluded — they are
      superseded/expired facts that should not appear in current retrieval.

    --as-of path (as_of is a datetime):
    - Keep only notes where valid_from <= as_of AND (valid_to null OR valid_to >= as_of).
    - Notes with null valid_from are valid for all time (never silently dropped;
      a warning is emitted to stderr so the user knows migration would help).
    - This path naturally INCLUDES archive notes (if they are in the candidate list).

    Args:
        candidates: Full candidate pool.
        as_of:      Target instant for temporal query, or None for current retrieval.
        now_dt:     Current UTC datetime (used for the default-path expiry check).

    Returns:
        Filtered list of candidates.
    """
    from ledger.bitemporal import is_valid_at, parse_validity

    filtered: list[RetrievalCandidate] = []
    _unmigrated_count = 0  # track notes with no valid_from for a single summary warning
    for c in candidates:
        vf_raw = c.valid_from
        vt_raw = c.valid_to

        superseded_by_raw = c.superseded_by
        has_any_validity_fields = bool(vf_raw or vt_raw or superseded_by_raw)

        if as_of is None:
            # Default path: pass-through notes with no validity fields.
            if not has_any_validity_fields:
                filtered.append(c)
                continue
            # Notes with valid_to in the past are hidden in current retrieval.
            fm_stub: dict[str, Any] = {}
            if vf_raw:
                fm_stub["valid_from"] = vf_raw
            if vt_raw:
                fm_stub["valid_to"] = vt_raw
            if is_valid_at(fm_stub, now_dt):
                filtered.append(c)
            # else: expired — skip silently
        else:
            # --as-of path: apply temporal filter strictly.
            if not vf_raw:
                # Null valid_from = valid for all time (open left bound).
                # Count rather than warn per-note to avoid spam.
                _unmigrated_count += 1
                # Still apply valid_to check if present.
                fm_stub: dict[str, Any] = {}
                if vt_raw:
                    fm_stub["valid_to"] = vt_raw
                if is_valid_at(fm_stub, as_of):
                    filtered.append(c)
                continue
            fm_stub = {}
            if vf_raw:
                fm_stub["valid_from"] = vf_raw
            if vt_raw:
                fm_stub["valid_to"] = vt_raw
            if is_valid_at(fm_stub, as_of):
                filtered.append(c)

    # Emit at most one warning per call for all unmigrated notes.
    if _unmigrated_count:
        _warnings.warn(
            f"{_unmigrated_count} note(s) have no valid_from; treating as valid "
            "for all time. Run `ledger migrate bitemporal --check` to see "
            "migration candidates.",
            stacklevel=4,
        )
    return filtered



def apply_changed_since_filter(
    candidates: list[RetrievalCandidate],
    *,
    since: dt.datetime | None,
) -> list[RetrievalCandidate]:
    """Keep candidates recorded on/after *since* (plan 47).

    Filters on **record time** (``created_ts`` or ``updated_ts`` — either
    qualifies), distinct from ``apply_temporal_filter`` which filters on
    valid-time. ``since=None`` is a no-op (returns the list unchanged), so this
    is behaviour-neutral unless a ``--changed-since`` bound is supplied. Composes
    with the valid-time filter: call both to get "valid at X AND changed since Y".
    """
    if since is None:
        return candidates
    kept: list[RetrievalCandidate] = []
    for c in candidates:
        created = _candidate_value(c, "created_ts")
        updated = _candidate_value(c, "updated_ts")
        newest = max((t for t in (created, updated) if t is not None), default=None)
        if newest is not None and newest >= since:
            kept.append(c)
    return kept



def prefilter_candidates_by_scope_and_type(
    candidates: list[CandidateLike],
    query_tokens: set[str],
    query_scope: str,
    history_mode: bool,
    loop_mode: bool,
    preference_mode: bool,
    limit: int,
) -> list[CandidateLike]:
    """Filter obvious noise before shortlist/final scoring."""
    if not candidates:
        return []

    filtered = []
    for candidate in candidates:
        token_overlap = has_token_overlap(candidate, query_tokens)
        candidate_type = str(_candidate_value(candidate, "type", "") or "")

        if query_scope != "all" and not scope_matches(str(_candidate_value(candidate, "scope", "") or ""), query_scope) and not token_overlap:
            continue
        if preference_mode and candidate_type not in {NOTE_LAYOUTS["preferences"].label, NOTE_LAYOUTS["concepts"].label} and not token_overlap:
            continue
        if loop_mode and candidate_type not in {NOTE_LAYOUTS["loops"].label, NOTE_LAYOUTS["goals"].label} and not token_overlap:
            continue
        if history_mode and candidate_type not in {NOTE_LAYOUTS["loops"].label, NOTE_LAYOUTS["facts"].label, NOTE_LAYOUTS["concepts"].label} and not token_overlap:
            continue

        filtered.append(candidate)

    if not filtered:
        return candidates

    if query_scope == "all" and (history_mode or loop_mode or preference_mode):
        return filtered

    minimum_pool = min(len(candidates), max(_cfg().shortlist_min_candidates, max(1, limit) * 2))
    if len(filtered) >= minimum_pool:
        return filtered

    seen_paths = {str(_candidate_value(item, "path", "") or "") for item in filtered}
    remainder = [item for item in candidates if str(_candidate_value(item, "path", "") or "") not in seen_paths]
    remainder.sort(key=_candidate_sort_key, reverse=True)
    for candidate in remainder:
        filtered.append(candidate)
        if len(filtered) >= minimum_pool:
            break

    return filtered



def has_token_overlap(candidate: CandidateLike, query_tokens: set[str]) -> bool:
    """Whether candidate has overlap with note/tag tokens."""
    if not query_tokens:
        return False
    note_tokens = set(_candidate_value(candidate, "note_tokens", set()) or set())
    tag_tokens = set(_candidate_value(candidate, "tag_tokens", set()) or set())
    return bool((note_tokens & query_tokens) or (tag_tokens & query_tokens))


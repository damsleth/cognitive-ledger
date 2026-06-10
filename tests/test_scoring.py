"""Unit tests for ledger.scoring composable primitives.

Tests are table-driven where practical.  Each test section covers one
exported function.  All tests are pure: no I/O, no config, no filesystem.
"""

from __future__ import annotations

import pytest

from ledger.scoring import (
    COARSE_LADDER,
    FINE_LADDER,
    IntentLadder,
    QueryCtx,
    _IntentRung,
    canonical_scope,
    clamp01,
    intent_adjustments,
    overlap_components,
    scope_component,
    scope_matches,
)


# ---------------------------------------------------------------------------
# canonical_scope
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("work",    "work"),
    ("Work",    "work"),
    ("  WORK ", "work"),
    ("life",    "personal"),   # 'life' alias
    ("Life",    "personal"),
    ("personal","personal"),
    ("",        ""),
    (None,      ""),
])
def test_canonical_scope(raw, expected):
    assert canonical_scope(raw) == expected


# ---------------------------------------------------------------------------
# scope_matches
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("note_scope, query_scope, expected", [
    ("work",     "all",      True),
    ("personal", "all",      True),
    ("",         "all",      True),
    ("work",     "work",     True),
    ("Work",     "work",     True),
    ("personal", "work",     False),
    ("life",     "personal", True),
    ("life",     "work",     False),
])
def test_scope_matches(note_scope, query_scope, expected):
    assert scope_matches(note_scope, query_scope) is expected


# ---------------------------------------------------------------------------
# scope_component
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("note_scope, query_scope, expected", [
    ("work",     "all",  1.0),
    ("work",     "work", 1.0),
    ("personal", "work", 0.0),
    ("",         "all",  1.0),
])
def test_scope_component(note_scope, query_scope, expected):
    assert scope_component(note_scope, query_scope) == expected


# ---------------------------------------------------------------------------
# overlap_components — empty query_tokens edge case
# ---------------------------------------------------------------------------

def test_overlap_components_empty_query():
    lm, to, lc, tc = overlap_components({"foo", "bar"}, {"tag"}, set())
    assert lm == 0.0
    assert to == 0.0
    assert lc == 0
    assert tc == 0


def test_overlap_components_ratio_math():
    # 2 of 4 query tokens in note, 1 of 4 in tags
    q = {"a", "b", "c", "d"}
    note = {"a", "b", "x"}
    tags = {"c", "y"}
    lm, to, lc, tc = overlap_components(note, tags, q)
    assert lm == pytest.approx(2 / 4)
    assert to == pytest.approx(1 / 4)
    assert lc == 2
    assert tc == 1


def test_overlap_components_full_overlap():
    q = {"a", "b"}
    lm, to, lc, tc = overlap_components(q, set(), q)
    assert lm == pytest.approx(1.0)
    assert to == 0.0


def test_overlap_components_tag_full_overlap():
    q = {"tag1", "tag2"}
    lm, to, lc, tc = overlap_components(set(), q, q)
    assert lm == 0.0
    assert to == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# clamp01
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    (0.5,   0.5),
    (0.0,   0.0),
    (1.0,   1.0),
    (-0.1,  0.0),
    (1.1,   1.0),
    (-9999, 0.0),
    (9999,  1.0),
])
def test_clamp01(value, expected):
    assert clamp01(value) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# intent_adjustments — FINE_LADDER
# ---------------------------------------------------------------------------

# Helper: check every rung of the fine ladder individually.
_FINE_CASES = [
    # (condition, kwargs, expected_delta, expected_reason_in)
    (
        "scope_boost",
        dict(candidate_type="fact", status="", query_scope="work", sm=1.0,
             history_mode=False, loop_mode=False, preference_mode=False),
        +0.05,
        "scope_boost",
    ),
    (
        "scope_demote",
        dict(candidate_type="fact", status="", query_scope="work", sm=0.0,
             history_mode=False, loop_mode=False, preference_mode=False),
        -0.05,
        "scope_demote",
    ),
    (
        "closed_loop_penalty",
        dict(candidate_type="loop", status="closed", query_scope="all", sm=1.0,
             history_mode=False, loop_mode=False, preference_mode=False),
        -0.20,
        "closed_loop_penalty",
    ),
    (
        "history_closed_loop_boost",
        dict(candidate_type="loop", status="closed", query_scope="all", sm=1.0,
             history_mode=True, loop_mode=False, preference_mode=False),
        +0.12,
        "history_closed_loop_boost",
    ),
    (
        "history_open_loop_demote",
        dict(candidate_type="loop", status="open", query_scope="all", sm=1.0,
             history_mode=True, loop_mode=False, preference_mode=False),
        -0.05,
        "history_open_loop_demote",
    ),
    (
        "open_loop_intent_boost",
        dict(candidate_type="loop", status="open", query_scope="all", sm=1.0,
             history_mode=False, loop_mode=True, preference_mode=False),
        +0.07,
        "open_loop_intent_boost",
    ),
    (
        "preference_intent_boost",
        dict(candidate_type="pref", status="", query_scope="all", sm=1.0,
             history_mode=False, loop_mode=False, preference_mode=True),
        +0.07,
        "preference_intent_boost",
    ),
    (
        "preference_non_pref_demote",
        dict(candidate_type="loop", status="open", query_scope="all", sm=1.0,
             history_mode=False, loop_mode=False, preference_mode=True),
        -0.05,
        "preference_non_pref_demote",
    ),
]


@pytest.mark.parametrize("label,kwargs,expected_delta,expected_reason", _FINE_CASES)
def test_fine_ladder_individual_rungs(label, kwargs, expected_delta, expected_reason):
    delta, reasons = intent_adjustments(FINE_LADDER, include_reasons=True, **kwargs)
    # Only the expected rung should have fired (other conditions blocked by kwargs).
    assert delta == pytest.approx(expected_delta), f"[{label}] delta mismatch"
    assert expected_reason in reasons, f"[{label}] reason not found in {reasons}"


# ---------------------------------------------------------------------------
# intent_adjustments — COARSE_LADDER
# ---------------------------------------------------------------------------

_COARSE_CASES = [
    (
        "coarse_scope_boost",
        dict(candidate_type="fact", status="", query_scope="work", sm=1.0,
             history_mode=False, loop_mode=False, preference_mode=False),
        +0.03,
        "scope_boost",
    ),
    (
        "coarse_scope_demote",
        dict(candidate_type="fact", status="", query_scope="work", sm=0.0,
             history_mode=False, loop_mode=False, preference_mode=False),
        -0.03,
        "scope_demote",
    ),
    (
        "coarse_closed_loop_penalty",
        dict(candidate_type="loop", status="closed", query_scope="all", sm=1.0,
             history_mode=False, loop_mode=False, preference_mode=False),
        -0.05,
        "closed_loop_penalty",
    ),
    (
        "coarse_history_closed_loop_boost",
        dict(candidate_type="loop", status="closed", query_scope="all", sm=1.0,
             history_mode=True, loop_mode=False, preference_mode=False),
        +0.05,
        "history_closed_loop_boost",
    ),
    (
        "coarse_loop_intent_boost",
        dict(candidate_type="loop", status="open", query_scope="all", sm=1.0,
             history_mode=False, loop_mode=True, preference_mode=False),
        +0.04,
        "open_loop_intent_boost",
    ),
    (
        "coarse_preference_intent_boost",
        dict(candidate_type="pref", status="", query_scope="all", sm=1.0,
             history_mode=False, loop_mode=False, preference_mode=True),
        +0.04,
        "preference_intent_boost",
    ),
]


@pytest.mark.parametrize("label,kwargs,expected_delta,expected_reason", _COARSE_CASES)
def test_coarse_ladder_individual_rungs(label, kwargs, expected_delta, expected_reason):
    delta, reasons = intent_adjustments(COARSE_LADDER, include_reasons=True, **kwargs)
    assert delta == pytest.approx(expected_delta), f"[{label}] delta mismatch"
    assert expected_reason in reasons, f"[{label}] reason not found in {reasons}"


# ---------------------------------------------------------------------------
# intent_adjustments — include_reasons=False suppresses reason list
# ---------------------------------------------------------------------------

def test_intent_adjustments_no_reasons():
    delta, reasons = intent_adjustments(
        FINE_LADDER,
        candidate_type="loop", status="closed", query_scope="all", sm=1.0,
        history_mode=False, loop_mode=False, preference_mode=False,
        include_reasons=False,
    )
    assert delta == pytest.approx(-0.20)
    assert reasons == []


# ---------------------------------------------------------------------------
# Multiple rungs can fire simultaneously
# ---------------------------------------------------------------------------

def test_multiple_rungs_accumulate():
    # scope demote + preference non-pref demote both fire
    # 'loop' open is not in {"pref","fact"} so both demote rungs fire
    delta, reasons = intent_adjustments(
        FINE_LADDER,
        candidate_type="loop", status="open", query_scope="work", sm=0.0,
        history_mode=False, loop_mode=False, preference_mode=True,
        include_reasons=True,
    )
    # -0.05 (scope_demote) + -0.05 (preference_non_pref_demote)
    assert delta == pytest.approx(-0.10)
    assert "scope_demote" in reasons
    assert "preference_non_pref_demote" in reasons


# ---------------------------------------------------------------------------
# Back-compat: scope_matches importable from ledger.retrieval
# ---------------------------------------------------------------------------

def test_back_compat_import():
    from ledger.retrieval import scope_matches as sm_retro, canonical_scope as cs_retro
    assert sm_retro("work", "work") is True
    assert cs_retro("life") == "personal"

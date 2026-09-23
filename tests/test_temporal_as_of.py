"""Auto as-of from an explicit year cue (plan 06b)."""

from __future__ import annotations

import datetime as dt

from ledger.query import temporal_as_of

NOW = dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc)


def test_past_year_after_preposition_engages():
    assert temporal_as_of("Where did Alex live in 2023?", NOW) == dt.datetime(
        2023, 7, 1, tzinfo=dt.timezone.utc
    )
    assert temporal_as_of("Hvor bodde jeg i 2021?", NOW).year == 2021


def test_ambiguous_or_current_cues_do_not_engage():
    for query in (
        "Why does the 2019 server keep crashing?",  # bare year, no preposition
        "Where did Alex live before moving to Oslo?",  # relative cue
        "What am I working on in 2026?",  # current year = default lens
        "Plans for 2030",
        "What is my employer?",
    ):
        assert temporal_as_of(query, NOW) is None, query

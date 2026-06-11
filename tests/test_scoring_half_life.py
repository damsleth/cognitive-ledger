"""Tests for per-type recency half-life resolution (plan 43)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ledger.scoring import canonical_note_type, half_life_for_type


class TestHalfLifeForType:
    def test_empty_map_falls_back_to_default(self):
        assert half_life_for_type("fact", by_type={}, default_days=180.0) == 180.0

    def test_uses_override_by_label(self):
        assert half_life_for_type("pref", by_type={"pref": 90.0}, default_days=180.0) == 90.0

    def test_uses_override_by_folder_name(self):
        # config key written as the folder name, candidate carries the short label
        assert half_life_for_type("pref", by_type={"preferences": 90.0}, default_days=180.0) == 90.0
        assert half_life_for_type("fact", by_type={"facts": 365.0}, default_days=180.0) == 365.0

    def test_absent_type_falls_back_to_default(self):
        assert half_life_for_type("goal", by_type={"facts": 365.0}, default_days=180.0) == 180.0

    def test_minimum_one_day(self):
        # guard against div-by-zero in ln(2)/half_life
        assert half_life_for_type("fact", by_type={"fact": 0.0}, default_days=180.0) == 1.0
        assert half_life_for_type("fact", by_type={}, default_days=0.0) == 1.0

    def test_malformed_value_falls_back(self):
        assert half_life_for_type("fact", by_type={"fact": "oops"}, default_days=180.0) == 180.0


class TestCanonicalNoteType:
    def test_folder_names_map_to_labels(self):
        assert canonical_note_type("preferences") == "pref"
        assert canonical_note_type("facts") == "fact"
        assert canonical_note_type("open_loops") == "loop"
        assert canonical_note_type("identity") == "id"

    def test_labels_pass_through(self):
        for label in ("fact", "pref", "loop", "id", "goal", "concept"):
            assert canonical_note_type(label) == label

    def test_unknown_passes_through_lowercased(self):
        assert canonical_note_type("WEIRD") == "weird"

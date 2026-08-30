"""Tests for ledger.patterns (WikiSkill-style signal pattern mining)."""

from __future__ import annotations

import json

from ledger.patterns import (
    CATEGORY_FAILURE,
    CATEGORY_STRATEGY,
    KIND_CONTRADICTED,
    KIND_CORRECTION_PRONE,
    KIND_HIGH_VALUE,
    KIND_REPEATED_MISS,
    mine_patterns,
    render_patterns_md,
    write_patterns,
)


def _sig(sig_type: str, ts: str, **extra):
    return {"ts": ts, "type": sig_type, **extra}


def _kinds(result):
    return {(p["kind"], p["subject"]) for p in result["patterns"]}


# --- failure modes ----------------------------------------------------------- #

def test_repeated_miss_crosses_threshold():
    signals = [
        _sig("retrieval_miss", f"2026-08-0{i}T00:00:00Z", query="deploy window")
        for i in range(1, 4)
    ]
    result = mine_patterns(signals)
    assert (KIND_REPEATED_MISS, "deploy window") in _kinds(result)
    pattern = result["patterns"][0]
    assert pattern["category"] == CATEGORY_FAILURE
    assert pattern["first_seen"] == "2026-08-01T00:00:00Z"
    assert pattern["last_seen"] == "2026-08-03T00:00:00Z"
    assert pattern["suggested_action"]


def test_below_threshold_miss_is_not_a_pattern():
    signals = [
        _sig("retrieval_miss", "2026-08-01T00:00:00Z", query="deploy window"),
        _sig("retrieval_miss", "2026-08-02T00:00:00Z", query="deploy window"),
    ]
    assert mine_patterns(signals)["patterns"] == []


def test_correction_prone_and_contradicted_notes():
    note = "notes/02_facts/fact__deploy.md"
    signals = [
        _sig("correction", "2026-08-01T00:00:00Z", note=note),
        _sig("correction", "2026-08-02T00:00:00Z", note=note),
        _sig("contradiction_flagged", "2026-08-03T00:00:00Z", note=note),
    ]
    kinds = _kinds(mine_patterns(signals))
    assert (KIND_CORRECTION_PRONE, note) in kinds
    assert (KIND_CONTRADICTED, note) in kinds


# --- strategies --------------------------------------------------------------- #

def test_high_value_note_becomes_strategy_pattern():
    note = "notes/03_preferences/pref__style.md"
    signals = [
        _sig("retrieval_hit", f"2026-08-0{i}T00:00:00Z", note=note, query="style")
        for i in range(1, 7)
    ] + [
        _sig("affirmation", "2026-08-07T00:00:00Z", note=note),
        _sig("affirmation", "2026-08-08T00:00:00Z", note=note),
    ]
    result = mine_patterns(signals)
    strategy = [p for p in result["patterns"] if p["category"] == CATEGORY_STRATEGY]
    assert len(strategy) == 1
    assert strategy[0]["kind"] == KIND_HIGH_VALUE
    assert strategy[0]["subject"] == note
    assert result["_meta"]["strategy_count"] == 1


def test_failures_sorted_before_strategies_and_by_weight():
    note = "notes/03_preferences/pref__style.md"
    signals = (
        [_sig("retrieval_miss", "2026-08-01T00:00:00Z", query="rare") for _ in range(3)]
        + [_sig("retrieval_miss", "2026-08-01T00:00:00Z", query="common") for _ in range(5)]
        + [
            _sig("retrieval_hit", f"2026-08-0{i}T00:00:00Z", note=note, query="style")
            for i in range(1, 7)
        ]
        + [_sig("affirmation", "2026-08-07T00:00:00Z", note=note)]
    )
    patterns = mine_patterns(signals)["patterns"]
    assert [p["subject"] for p in patterns] == ["common", "rare", note]


# --- rendering + writing ------------------------------------------------------ #

def test_render_patterns_md_sections():
    signals = [
        _sig("retrieval_miss", f"2026-08-0{i}T00:00:00Z", query="deploy window")
        for i in range(1, 4)
    ]
    md = render_patterns_md(mine_patterns(signals))
    assert "# Signal patterns" in md
    assert "## Failure modes" in md
    assert "### Repeated retrieval misses" in md
    assert "deploy window" in md
    assert "## Strategies" in md
    assert "*None detected.*" in md


def test_write_patterns_creates_directory_files(tmp_path):
    notes_dir = tmp_path / "notes"
    indices = notes_dir / "08_indices"
    indices.mkdir(parents=True)
    signals_path = indices / "signals.jsonl"
    lines = [
        json.dumps(_sig("retrieval_miss", f"2026-08-0{i}T00:00:00Z", query="deploy window"))
        for i in range(1, 4)
    ]
    signals_path.write_text("\n".join(lines) + "\n")

    json_path, md_path, result = write_patterns(notes_dir, signals_path=signals_path)

    assert json_path == indices / "patterns.json"
    assert md_path == indices / "patterns.md"
    stored = json.loads(json_path.read_text())
    assert stored["_meta"]["failure_count"] == 1
    assert "deploy window" in md_path.read_text()
    assert result["patterns"][0]["kind"] == KIND_REPEATED_MISS

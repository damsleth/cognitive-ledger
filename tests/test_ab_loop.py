"""Unit tests for the autonomous A/B loop pure logic (no probing/subprocess)."""

from __future__ import annotations

import pytest

from ledger import ab
from ledger import ab_loop


# --- compute_objective ----------------------------------------------------- #

def test_compute_objective_default_weights():
    # 0.6*mrr + 0.4*hitk
    obj = ab.compute_objective({"hit1": 1.0, "hitk": 0.5, "mrr": 0.8})
    assert obj == pytest.approx(0.6 * 0.8 + 0.4 * 0.5)


def test_compute_objective_custom_weights_and_missing_metric():
    obj = ab.compute_objective({"mrr": 0.5}, weights={"mrr": 1.0, "hitk": 2.0})
    # hitk missing -> 0.0; unknown keys ignored
    assert obj == pytest.approx(0.5)


def test_compute_objective_ignores_unknown_weight_keys():
    obj = ab.compute_objective({"mrr": 1.0}, weights={"mrr": 1.0, "bogus": 99.0})
    assert obj == pytest.approx(1.0)


# --- load_space ------------------------------------------------------------ #

def test_load_space_range_inclusive_and_int_format(tmp_path):
    p = tmp_path / "space.yaml"
    p.write_text("params:\n  score_weight_signal:\n    min: 0.0\n    max: 0.2\n    step: 0.1\n")
    space = ab_loop.load_space(p)
    assert space["score_weight_signal"] == ["0", "0.1", "0.2"]


def test_load_space_explicit_list(tmp_path):
    p = tmp_path / "space.yaml"
    p.write_text("params:\n  retrieval_mode: [legacy, semantic_hybrid]\n")
    assert ab_loop.load_space(p)["retrieval_mode"] == ["legacy", "semantic_hybrid"]


def test_load_space_rejects_empty(tmp_path):
    p = tmp_path / "space.yaml"
    p.write_text("params: {}\n")
    with pytest.raises(ValueError):
        ab_loop.load_space(p)


# --- next_proposal (coordinate descent) ------------------------------------ #

def test_next_proposal_skips_current_and_returns_first_untried():
    space = {"a": ["1", "2", "3"]}
    # champion already at a=1 -> propose a=2
    assert ab_loop.next_proposal(space, {"a": "1"}, []) == {"a": "2"}


def test_next_proposal_skips_tried():
    space = {"a": ["1", "2", "3"]}
    history = [{"mutation": {"a": "2"}}]
    assert ab_loop.next_proposal(space, {"a": "1"}, history) == {"a": "3"}


def test_next_proposal_exhausted_returns_none():
    space = {"a": ["1", "2"]}
    history = [{"mutation": {"a": "2"}}]
    # champion at a=1, only other value (2) already tried
    assert ab_loop.next_proposal(space, {"a": "1"}, history) is None


def test_next_proposal_advances_across_coordinates():
    space = {"a": ["1", "2"], "b": ["5", "6"]}
    # a=1 (current), a=2 tried -> move to coordinate b
    history = [{"mutation": {"a": "2"}}]
    assert ab_loop.next_proposal(space, {"a": "1", "b": "5"}, history) == {"b": "6"}

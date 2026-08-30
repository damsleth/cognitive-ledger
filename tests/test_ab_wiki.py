"""Unit tests for the persistent A/B wiki layer (WikiSkill adaptation)."""

from __future__ import annotations

from ledger import ab_loop
from ledger.ab_wiki import (
    AVOID_MIN_TRIES,
    VERDICT_AVOID,
    VERDICT_EXPLORE,
    VERDICT_PRIORITIZE,
    VERDICT_STRUGGLING,
    ParamImpact,
    Wiki,
)


def _trial(key: str, value: str, decision: str, delta: float, trial: int = 0):
    return {
        "trial": trial,
        "timestamp": "2026-08-30T00:00:00Z",
        "mutation": {key: value},
        "screen_delta": delta,
        "decision": decision,
        "accepted": decision == "beneficial",
        "holdout_delta": 0.0 if decision in ("beneficial", "overfit") else None,
    }


# --- ParamImpact verdicts ---------------------------------------------------- #

def test_verdict_untried_is_explore():
    assert ParamImpact().verdict == VERDICT_EXPLORE


def test_verdict_prioritize_after_accept():
    impact = ParamImpact()
    impact.record("regression", -0.01, "1")
    impact.record("beneficial", 0.02, "2")
    assert impact.verdict == VERDICT_PRIORITIZE


def test_verdict_struggling_then_avoid_after_consistent_failures():
    impact = ParamImpact()
    for i in range(AVOID_MIN_TRIES - 1):
        impact.record("regression", -0.01, str(i))
        assert impact.verdict == VERDICT_STRUGGLING
    impact.record("regression", -0.01, "last")
    assert impact.verdict == VERDICT_AVOID


def test_verdict_overfit_with_screen_win_stays_explore():
    # An overfit won the screen: evidence is mixed, keep exploring the param.
    impact = ParamImpact()
    for i in range(AVOID_MIN_TRIES):
        impact.record("overfit", 0.05, str(i))
    assert impact.verdict == VERDICT_EXPLORE


# --- Wiki: persistence across rollbacks -------------------------------------- #

def test_rejected_trials_still_update_wiki(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    wiki.observe(_trial("a", "2", "regression", -0.02), log=False)
    assert wiki.params["a"].tries == 1
    assert wiki.params["a"].regressions == 1
    assert wiki.trials_seen == 1


def test_open_rebuilds_stats_from_history(tmp_path):
    history = [
        _trial("a", "2", "regression", -0.02, trial=0),
        _trial("b", "6", "beneficial", 0.03, trial=1),
    ]
    wiki = Wiki.open(tmp_path / "wiki", history)
    assert wiki.trials_seen == 2
    assert wiki.verdict("a") == VERDICT_STRUGGLING
    assert wiki.verdict("b") == VERDICT_PRIORITIZE
    # Rebuild must not touch the append-only evolution log.
    assert not wiki.evolution_path.exists()


def test_save_load_roundtrip(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    wiki.observe(_trial("a", "2", "beneficial", 0.03), log=False)
    wiki.save()
    loaded = Wiki.load(tmp_path / "wiki")
    assert loaded.trials_seen == 1
    assert loaded.params["a"].accepted == 1
    assert loaded.verdict("a") == VERDICT_PRIORITIZE


def test_evolution_log_appends_on_live_observe(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    for i in range(AVOID_MIN_TRIES):
        wiki.observe(_trial("a", str(i + 2), "regression", -0.01, trial=i))
    text = wiki.evolution_path.read_text()
    assert "trial 0" in text
    assert "regression" in text
    # The verdict flip to avoid is logged as a pattern line.
    assert "**avoid**" in text


# --- Wiki-informed proposal ordering ------------------------------------------ #

def test_proposal_order_empty_wiki_keeps_space_order(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    assert wiki.proposal_order(["a", "b", "c"]) == ["a", "b", "c"]


def test_proposal_order_ranks_prioritize_first_avoid_last(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    for i in range(AVOID_MIN_TRIES):
        wiki.observe(_trial("a", str(i), "regression", -0.01), log=False)
    wiki.observe(_trial("c", "9", "beneficial", 0.02), log=False)
    assert wiki.proposal_order(["a", "b", "c"]) == ["c", "b", "a"]


def test_next_proposal_consults_wiki(tmp_path):
    space = {"a": ["1", "2", "3", "4", "5"], "b": ["5", "6"]}
    history = [
        _trial("a", "2", "regression", -0.01, trial=0),
        _trial("a", "3", "regression", -0.01, trial=1),
        _trial("a", "4", "regression", -0.01, trial=2),
    ]
    wiki = Wiki.open(tmp_path / "wiki", history)
    champion_env = {"a": "1", "b": "5"}
    # Blind coordinate descent would keep grinding on a (a=5 untried);
    # the wiki has marked a "avoid" so b is explored first.
    assert ab_loop.next_proposal(space, champion_env, history) == {"a": "5"}
    assert ab_loop.next_proposal(space, champion_env, history, wiki) == {"b": "6"}


def test_next_proposal_wiki_still_exhausts_avoided_params(tmp_path):
    space = {"a": ["1", "2"]}
    history = []
    wiki = Wiki(tmp_path / "wiki")
    for i in range(AVOID_MIN_TRIES):
        record = _trial("a", "2", "regression", -0.01, trial=i)
        history.append(record)
        wiki.observe(record, log=False)
    # a is avoided but it is the only param: ranked last, not dropped …
    assert wiki.proposal_order(["a"]) == ["a"]
    # … and its already-tried values stay excluded.
    assert ab_loop.next_proposal(space, {"a": "1"}, history, wiki) is None


def test_summary_lines_mention_verdicts(tmp_path):
    wiki = Wiki(tmp_path / "wiki")
    wiki.observe(_trial("a", "2", "beneficial", 0.02), log=False)
    lines = "\n".join(wiki.summary_lines())
    assert "prioritize" in lines
    assert "a" in lines

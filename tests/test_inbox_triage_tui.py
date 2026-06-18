"""Tests for the subtraction-model triage TUI (ledger/inbox_triage.py)."""
from __future__ import annotations

import pytest

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.inbox import load_candidates_for_triage
from ledger.inbox_triage import _build_textual

_NOTE = """---
created: 2026-06-18T00:00:00Z
updated: 2026-06-18T00:00:00Z
tags: [test]
confidence: 0.7
source: inferred
scope: personal
lang: en
promoted_by: user
---

# {title}

## Statement
{title} statement body.
"""


def _setup(tmp_path):
    notes_dir = tmp_path / "notes"
    for folder in (
        "00_inbox", "01_identity", "02_facts", "03_preferences",
        "04_goals", "05_open_loops", "06_concepts", "07_projects",
        "08_indices", "09_archive",
    ):
        (notes_dir / folder).mkdir(parents=True)
    (notes_dir / "08_indices" / "timeline.md").write_text("# Timeline\n", encoding="utf-8")
    for i, t in enumerate(["Alpha fact", "Beta fact", "Gamma fact"]):
        (notes_dir / "00_inbox" / f"fact__{i}.md").write_text(_NOTE.format(title=t), encoding="utf-8")
    cfg = LedgerConfig(
        ledger_root=tmp_path,
        ledger_notes_dir=notes_dir,
        source_notes_dir=tmp_path / "source",
    )
    set_config(cfg)
    return notes_dir


@pytest.fixture
def notes_dir(tmp_path):
    nd = _setup(tmp_path)
    yield nd
    reset_config()


def _app(notes_dir):
    cands = load_candidates_for_triage(notes_dir)
    return _build_textual()(cands, notes_dir, {}), cands


def test_default_all_accept(notes_dir):
    app, cands = _app(notes_dir)
    actions = app.build_actions()
    assert len(actions) == 3
    assert all(a.action == "accept" for a in actions)
    assert [a.row for a in actions] == [1, 2, 3]


def test_exclude_becomes_reject(notes_dir):
    app, cands = _app(notes_dir)
    app.excluded.add(1)  # exclude the 2nd (row 2)
    actions = {a.row: a for a in app.build_actions()}
    assert actions[2].action == "reject"
    assert actions[1].action == "accept"
    assert actions[3].action == "accept"


def test_type_override_carries_into_accept(notes_dir):
    app, cands = _app(notes_dir)
    app.types[0] = "concepts"
    actions = {a.row: a for a in app.build_actions()}
    assert actions[1].action == "accept"
    assert actions[1].target_type == "concepts"


def test_pilot_toggle_and_commit(notes_dir):
    """Drive the real TUI: exclude one note, accept the rest, verify the move."""
    import asyncio

    app, cands = _app(notes_dir)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("down")      # move to row 2
            await pilot.press("space")     # exclude it
            assert app.excluded == {1}
            await pilot.press("A")         # commit -> confirm modal
            await pilot.press("y")         # proceed
            await pilot.pause()

    asyncio.run(scenario())
    assert app.summary is not None
    assert app.summary["accepted"] == 2
    assert app.summary["rejected"] == 1
    # two facts promoted, inbox emptied
    assert len(list((notes_dir / "02_facts").glob("*.md"))) == 2
    assert len(list((notes_dir / "00_inbox").glob("*.md"))) == 0

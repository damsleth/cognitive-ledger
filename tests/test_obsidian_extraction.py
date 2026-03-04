from __future__ import annotations

from pathlib import Path

from ledger.obsidian.extraction import extract_candidates
from ledger.obsidian.importer import _filter_file_candidates


def test_extract_candidates_classifies_preference_decision_and_loop():
    content = """
# Worklog

I prefer concise responses with explicit tradeoffs.
Decision: We will consolidate note parsing into one shared module.
- [ ] Investigate flaky CI integration tests across environments.
"""

    candidates = extract_candidates(content)
    by_kind = {candidate.kind for candidate in candidates}

    assert "pref" in by_kind
    assert "fact" in by_kind
    assert "loop" in by_kind
    assert any(candidate.kind == "pref" and candidate.confidence >= 0.9 for candidate in candidates)


def test_filter_drops_weak_journal_loops_without_ownership_or_decision_signal(tmp_path):
    journal_path = tmp_path / "90-journal" / "2026-03-02-monday.md"
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    content = """
# Journal

- [ ] Investigate flaky CI integration tests across environments.
"""

    candidates = extract_candidates(content)
    filtered = _filter_file_candidates(journal_path, content, candidates)

    assert not any(candidate.kind == "loop" for candidate in filtered)

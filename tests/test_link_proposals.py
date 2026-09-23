"""Plan 11: sleep proposes [[links]] between related notes, links only."""

from __future__ import annotations

import numpy as np
import pytest

from ledger import link_proposals as lp
from ledger.config import LedgerConfig, reset_config, set_config

NOTE = "---\ncreated: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\n---\n\n# {name}\n\nBody.\n{links}"


@pytest.fixture()
def corpus(tmp_path, monkeypatch):
    cfg = LedgerConfig(ledger_root=tmp_path, ledger_notes_dir=tmp_path / "notes")
    set_config(cfg)
    facts = cfg.ledger_notes_dir / "02_facts"
    facts.mkdir(parents=True)
    names = ["fact__a", "fact__b", "fact__far", "fact__dup"]
    for n in names:
        (facts / f"{n}.md").write_text(NOTE.format(name=n, links=""), encoding="utf-8")
    # a~b related (0.9), far unrelated, dup a near-duplicate of a (above ceiling).
    vectors = np.array([[1.0, 0.0, 0.0], [0.9, 0.436, 0.0], [0.0, 0.0, 1.0], [0.999, 0.045, 0.0]], dtype=np.float32)
    index = {"items": [{"rel_path": f"notes/02_facts/{n}.md"} for n in names]}
    monkeypatch.setattr(lp, "load_semantic_index", lambda *a: (index, vectors))
    yield cfg
    reset_config()


def test_proposes_related_pairs_both_ways_and_skips_unrelated_and_duplicates(corpus):
    pairs = {(p.source.split("/")[-1], p.target.split("/")[-1]) for p in lp.propose_links()}
    assert ("fact__a.md", "fact__b.md") in pairs and ("fact__b.md", "fact__a.md") in pairs
    assert not any("fact__far" in s + t for s, t in pairs)
    assert ("fact__a.md", "fact__dup.md") not in pairs  # merge candidate, not a link


def test_apply_writes_links_section_and_is_idempotent(corpus):
    for proposal in lp.propose_links():
        lp.apply_link(proposal, "2026-09-23T00:00:00Z")
    text = (corpus.ledger_notes_dir / "02_facts" / "fact__a.md").read_text(encoding="utf-8")
    assert "## Links\n\n- [[fact__b]]\n" in text
    assert "updated: 2026-09-23T00:00:00Z" in text
    assert "Body." in text  # links only: the note's content is untouched

    assert not [p for p in lp.propose_links() if p.source.endswith("fact__a.md")]


def test_appends_to_an_existing_links_section(corpus):
    path = corpus.ledger_notes_dir / "02_facts" / "fact__a.md"
    path.write_text(NOTE.format(name="a", links="\n## Links\n\n- [[fact__far]]\n\n## Notes\n\nx\n"), encoding="utf-8")
    lp.apply_link(lp.LinkProposal("notes/02_facts/fact__a.md", "notes/02_facts/fact__b.md", 0.9), "t")
    text = path.read_text(encoding="utf-8")
    assert "## Links\n\n- [[fact__far]]\n- [[fact__b]]\n\n## Notes" in text

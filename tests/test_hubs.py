from pathlib import Path

import pytest

from ledger import hubs
from ledger.parsing.frontmatter import parse_frontmatter_text


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    (tmp_path / "08_indices").mkdir()
    (tmp_path / "02_facts").mkdir()
    return tmp_path


def _fm(path: Path) -> tuple[dict, str]:
    return parse_frontmatter_text(path.read_text())


def test_person_hub_lifecycle(notes, monkeypatch):
    monkeypatch.setattr(hubs, "_yaams_canonical", lambda name: None)
    path = hubs.create("people", "ingrid_x", "Ingrid X", fields={"entity": "Ingrid X", "spheres": ["brkh"]},
                       sections={"Who": "Fagleder."}, notes_dir=notes)
    assert path == notes / "10_people" / "person__ingrid_x.md"
    with pytest.raises(FileExistsError):
        hubs.create("people", "ingrid_x", "Ingrid X", notes_dir=notes)

    hubs.log("people", "ingrid_x", "Takes vakt in November.", date="2026-10-20", notes_dir=notes)
    hubs.log("people", "ingrid_x", "Prefers SMS over Teams.", agent=True, date="2026-10-28", notes_dir=notes)
    fm, body = _fm(path)
    assert fm["last_interaction"] == "2026-10-28"
    log = body.split("## Log", 1)[1]
    assert "- 2026-10-20 Takes vakt in November.\n- 2026-10-28 (agent) Prefers SMS over Teams." in log
    assert "## Who\nFagleder." in body
    assert hubs.unreviewed_count(fm, body) == 1

    (notes / "02_facts" / "fact__a.md").write_text("---\npeople: [ingrid_x]\n---\n# A\n")
    (notes / "02_facts" / "fact__b.md").write_text("---\ntags: [x]\n---\n# B\nsee [[person__ingrid_x]]\n")
    (notes / "02_facts" / "fact__c.md").write_text("---\ntags: [x]\n---\n# C\n")
    assert hubs.connected("people", "ingrid_x", notes) == ["notes/02_facts/fact__a.md", "notes/02_facts/fact__b.md"]

    assert hubs.resolve("people", "ingrid x", notes) == "ingrid_x"
    assert hubs.resolve("people", "nobody", notes) is None

    assert hubs.unreviewed(notes) == [{"path": "notes/10_people/person__ingrid_x.md", "unreviewed": 1}]
    assert hubs.accept(path, notes) == 1
    fm, body = _fm(path)
    assert "- 2026-10-28 Prefers SMS over Teams." in body
    assert hubs.unreviewed(notes) == []


def test_agent_created_project_and_state_rewrite_are_unreviewed(notes):
    path = hubs.create("projects", "nocos", "NOCOS", sections={"Goal": "Ship 26.1."}, agent=True, notes_dir=notes)
    fm, _ = _fm(path)
    assert fm["reviewed"] is False and fm["source"] == "assistant" and fm["status"] == "active"
    hubs.accept(path, notes)
    fm, _ = _fm(path)
    assert "reviewed" not in fm and fm["source"] == "user"

    hubs.set_section("projects", "nocos", "State", "Prod release 06.10 done.", agent=True, notes_dir=notes)
    fm, body = _fm(path)
    assert fm["reviewed"] is False
    assert "## State\nProd release 06.10 done.\n\n## Decisions" in body
    with pytest.raises(ValueError):
        hubs.set_section("projects", "nocos", "Log", "x", notes_dir=notes)


def test_hub_notes_validate_and_index_unreviewed(notes):
    from ledger.validation import validate_frontmatter_fields

    path = hubs.create("people", "p", "P", agent=True, notes_dir=notes)
    fm, _ = _fm(path)
    issues = validate_frontmatter_fields(fm, "notes/10_people/person__p.md")
    assert issues == ["person hub missing 'entity' (yaams canonical name) [warning]"]

    proj = hubs.create("projects", "q", "Q", fields={"status": "someday"}, notes_dir=notes)
    fm, _ = _fm(proj)
    assert "invalid status: someday" in validate_frontmatter_fields(fm, "notes/07_projects/project__q.md")

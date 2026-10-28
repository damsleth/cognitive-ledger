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


def _hub(notes: Path, text: str) -> Path:
    path = notes / "10_people" / "person__x.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def test_mutations_keep_untouched_frontmatter_bytes(notes):
    fm = '---\ncreated: 2026-01-01T00:00:00Z\nupdated: 2026-01-01T00:00:00Z\nentity: "O\'Brien \\"OB\\""\nnote: |\n  multi\n  line\n---\n'
    path = _hub(notes, fm + "# X\n\n## Log\n")
    hubs.log("people", "x", "hi", date="2026-10-28", agent=True, notes_dir=notes)
    text = path.read_text()
    assert 'entity: "O\'Brien \\"OB\\""\nnote: |\n  multi\n  line\n' in text
    assert text.count("## Log") == 1 and text.endswith("## Log\n- 2026-10-28 (agent) hi\n")
    hubs.accept("notes/10_people/person__x.md", notes)
    assert "note: |\n  multi\n  line\n" in path.read_text()


def test_section_edges(notes):
    path = _hub(notes, "---\nupdated: x\n---\n# X\n\n## Who   \nold\n# Appendix\nkeep\n\n## Log   \n- 2026-01-01 a\n")
    hubs.set_section("people", "x", "Who", "new", notes_dir=notes)
    hubs.set_section("people", "x", "Working with them", "calm", notes_dir=notes)
    body = path.read_text()
    assert "## Who   \nnew\n\n# Appendix\nkeep\n" in body
    assert "## Working with them\ncalm\n\n## Log   \n- 2026-01-01 a\n" in body
    _hub(notes, "---\nupdated: x\n---\n# X\n## Log\n- a\n## Log\n- b\n")
    with pytest.raises(ValueError):
        hubs.log("people", "x", "c", notes_dir=notes)


def test_accept_refuses_paths_outside_corpus(notes, tmp_path_factory):
    outside = tmp_path_factory.mktemp("elsewhere") / "x.md"
    outside.write_text("---\nreviewed: false\n---\n")
    for bad in (outside, "notes/../../x.md"):
        with pytest.raises(ValueError):
            hubs.accept(bad, notes)


def test_wikilink_must_match_whole_target(notes):
    hubs.create("people", "ann", "Ann", notes_dir=notes)
    (notes / "02_facts" / "fact__a.md").write_text("---\ntags: [x]\n---\n[[person__annette]] [[person__ann|Ann]]\n")
    (notes / "02_facts" / "fact__b.md").write_text("---\ntags: [x]\n---\n[[person__annette]]\n")
    assert hubs.connected("people", "ann", notes) == ["notes/02_facts/fact__a.md"]


def test_log_refuses_private_and_multiline_frontmatter(notes):
    path = _hub(notes, "---\nupdated: x\nlast_interaction:\nnote: |\n  a\n---\n# X\n\n## Log\n")
    hubs.log("people", "x", "fine", date="2026-10-28", notes_dir=notes)  # empty value is single-line
    with pytest.raises(ValueError):
        hubs.log("people", "x", "<private>secret</private>", notes_dir=notes)
    with pytest.raises(ValueError):
        hubs._patch_frontmatter("---\nlast_interaction: |\n  2026-01-01\n---\n", {"last_interaction": "2026-10-28"})
    with pytest.raises(ValueError):
        hubs._patch_frontmatter("---\nspheres:\n  - work\n---\n", {"spheres": ["brkh"]})

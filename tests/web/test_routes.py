"""Phase 1 route tests for the cognitive-ledger web UI."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestRoot:
    def test_redirects_to_browse(self, client: TestClient) -> None:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code in (307, 308)
        assert resp.headers["location"] == "/browse"

    def test_full_follow(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Recent" in resp.text or "Notes" in resp.text


class TestBrowseIndex:
    def test_browse_renders(self, client: TestClient) -> None:
        resp = client.get("/browse")
        assert resp.status_code == 200
        assert "Recent" in resp.text
        # Sample notes appear in the recent list
        assert "fact__sample" in resp.text
        assert "loop__open_test" in resp.text

    def test_browse_sidebar_lists_types(self, client: TestClient) -> None:
        resp = client.get("/browse")
        assert resp.status_code == 200
        # Sidebar links to each type-keyed browse page
        assert 'href="/browse/facts"' in resp.text
        assert 'href="/browse/loops"' in resp.text

    def test_browse_sidebar_shows_counts(self, client: TestClient) -> None:
        resp = client.get("/browse")
        # facts type has 2 notes in the fixture
        assert 'href="/browse/facts"' in resp.text
        assert ">2<" in resp.text  # count cell rendered


class TestBrowseByType:
    def test_facts_listing(self, client: TestClient) -> None:
        resp = client.get("/browse/facts")
        assert resp.status_code == 200
        assert "fact__sample" in resp.text
        assert "fact__other_one" in resp.text
        # Loop note must NOT appear in the facts listing
        assert "loop__open_test" not in resp.text

    def test_loops_default_shows_both(self, client: TestClient) -> None:
        resp = client.get("/browse/loops")
        assert resp.status_code == 200
        assert "loop__open_test" in resp.text
        assert "loop__closed_test" in resp.text

    def test_loops_status_filter_open(self, client: TestClient) -> None:
        resp = client.get("/browse/loops?status=open")
        assert resp.status_code == 200
        assert "loop__open_test" in resp.text
        assert "loop__closed_test" not in resp.text

    def test_loops_status_filter_closed(self, client: TestClient) -> None:
        resp = client.get("/browse/loops?status=closed")
        assert resp.status_code == 200
        assert "loop__closed_test" in resp.text
        assert "loop__open_test" not in resp.text

    def test_all_aggregates_types(self, client: TestClient) -> None:
        resp = client.get("/browse/all")
        assert resp.status_code == 200
        assert "fact__sample" in resp.text
        assert "loop__open_test" in resp.text

    def test_unknown_type_404s(self, client: TestClient) -> None:
        resp = client.get("/browse/banana")
        assert resp.status_code == 404


class TestNoteDetail:
    def test_render_known_note(self, client: TestClient) -> None:
        resp = client.get("/note/fact__sample")
        assert resp.status_code == 200
        assert "Sample Fact" in resp.text
        # Frontmatter rendered in the meta dl
        assert "0.9" in resp.text  # confidence
        assert "dev" in resp.text  # scope
        assert "alpha" in resp.text  # tag

    def test_wikilink_rewriting_works(self, client: TestClient) -> None:
        resp = client.get("/note/fact__sample")
        assert resp.status_code == 200
        # Resolved wikilink -> /note/{stem}
        assert 'href="/note/fact__other_one"' in resp.text

    def test_wikilink_display_is_target_title(self, client: TestClient) -> None:
        """Bare [[stem]] renders with the target note's title, not the stem."""
        resp = client.get("/note/fact__sample")
        assert resp.status_code == 200
        # fact__other_one.md has "# Other Fact" as its H1
        assert '>Other Fact</a>' in resp.text
        # Raw stem must NOT appear as the link text
        assert '>fact__other_one</a>' not in resp.text

    def test_leading_h1_stripped_from_body(self, client: TestClient) -> None:
        """The template already renders the title; the body's leading H1 is dropped."""
        resp = client.get("/note/fact__sample")
        assert resp.status_code == 200
        # Exactly one <h1> on the page (the template header), not two.
        assert resp.text.count("<h1>") == 1

    def test_broken_wikilink_rendered_as_span(self, client: TestClient) -> None:
        resp = client.get("/note/fact__sample")
        assert resp.status_code == 200
        assert 'class="broken-link"' in resp.text
        assert "fact__does_not_exist" in resp.text
        # Broken stems also surface in the side panel
        assert "Broken links" in resp.text

    def test_unknown_note_404s(self, client: TestClient) -> None:
        resp = client.get("/note/no_such_note")
        assert resp.status_code == 404

    def test_loop_note_renders_status(self, client: TestClient) -> None:
        resp = client.get("/note/loop__open_test")
        assert resp.status_code == 200
        assert "Test Open Loop" in resp.text
        assert "open" in resp.text


class TestStaticAssets:
    def test_stylesheet_served(self, client: TestClient) -> None:
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "type-link" in resp.text  # one of the rules we defined


class TestRenderHelpers:
    """Direct unit tests for the rendering layer."""

    def test_wikilink_with_alias(self, client: TestClient, web_ledger_root) -> None:
        # Add a note that uses an aliased wikilink and verify it renders.
        notes_dir = web_ledger_root / "notes"
        path = notes_dir / "02_facts" / "fact__alias_demo.md"
        path.write_text(
            """---
created: 2026-02-13T10:00:00Z
updated: 2026-04-12T10:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Alias Demo

## Statement

See [[fact__other_one|the other note]] for details.
"""
        )
        resp = client.get("/note/fact__alias_demo")
        assert resp.status_code == 200
        assert 'href="/note/fact__other_one"' in resp.text
        assert "the other note" in resp.text


class TestCorpusService:
    """Direct unit tests of the Corpus service to lock its contract."""

    def test_note_types_counts(self, web_ledger_root) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        types = {t.key: t.count for t in corpus.note_types()}
        assert types["facts"] == 2
        assert types["loops"] == 2

    def test_get_by_stem_round_trip(self, web_ledger_root) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        item = corpus.get_by_stem("fact__sample")
        assert item is not None
        assert item.title == "Sample Fact"
        assert item.type == "facts"

    def test_get_by_stem_missing(self, web_ledger_root) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        assert corpus.get_by_stem("not_there") is None

    def test_reload_picks_up_new_files(self, web_ledger_root) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        assert corpus.get_by_stem("fact__newly_added") is None
        new_path = web_ledger_root / "notes" / "02_facts" / "fact__newly_added.md"
        new_path.write_text(
            """---
created: 2026-04-12T10:00:00Z
updated: 2026-04-12T10:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Newly Added

## Statement

Added after server boot.
"""
        )
        # First get_by_stem refreshes lazily on miss
        item = corpus.get_by_stem("fact__newly_added")
        assert item is not None
        assert item.title == "Newly Added"


class TestStatusbar:
    def test_statusbar_shows_corpus_size(self, client) -> None:
        resp = client.get("/browse")
        assert resp.status_code == 200
        body = resp.text
        # notes count in statusbar
        assert "notes" in body
        # reload button present
        assert 'hx-post="/admin/reload"' in body

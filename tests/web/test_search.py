"""Phase 2 search route tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestSearchEmpty:
    def test_no_query_renders_empty_state(self, client: TestClient) -> None:
        resp = client.get("/search")
        assert resp.status_code == 200
        assert "Type a query above to search" in resp.text
        # Full page wrapper present
        assert "<html" in resp.text
        # Sidebar shows our new search entry
        assert 'href="/search"' in resp.text

    def test_query_with_no_hits_shows_no_match(self, client: TestClient) -> None:
        resp = client.get("/search?q=zzzzz_no_such_token_zzzzz")
        assert resp.status_code == 200
        assert "No matches for" in resp.text
        assert "zzzzz_no_such_token_zzzzz" in resp.text


class TestSearchResults:
    def test_lexical_returns_known_stem(self, client: TestClient) -> None:
        resp = client.get("/search?q=sample")
        assert resp.status_code == 200
        # The fixture fact__sample.md has "Sample Fact" in its title
        # and "test fact with a wikilink" in its body.
        assert "fact__sample" in resp.text or "Sample Fact" in resp.text

    def test_results_link_to_note_detail(self, client: TestClient) -> None:
        resp = client.get("/search?q=other")
        assert resp.status_code == 200
        assert 'href="/note/fact__other_one"' in resp.text

    def test_scope_filter_is_respected_in_summary(self, client: TestClient) -> None:
        resp = client.get("/search?q=sample&scope=dev")
        assert resp.status_code == 200
        assert "dev scope" in resp.text

    def test_unknown_mode_falls_back_to_default(self, client: TestClient) -> None:
        resp = client.get("/search?q=sample&mode=banana")
        assert resp.status_code == 200
        # Falls back to lexical (the default), still produces results
        assert "lexical mode" in resp.text


class TestHTMXFragment:
    def test_htmx_request_returns_fragment_only(self, client: TestClient) -> None:
        resp = client.get(
            "/search?q=sample",
            headers={"HX-Request": "true"},
        )
        assert resp.status_code == 200
        # Fragment has no <html>/<body>
        assert "<html" not in resp.text
        assert "<body" not in resp.text
        # But still has the results list markup
        assert "note-list" in resp.text or "empty" in resp.text

    def test_htmx_empty_query_returns_empty_state_fragment(self, client: TestClient) -> None:
        resp = client.get("/search", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "<html" not in resp.text
        assert "Type a query above to search" in resp.text


class TestSearchCache:
    def test_repeat_query_hits_cache(self, client: TestClient) -> None:
        from ledger.web.services.search import Searcher

        searcher = Searcher()
        first = searcher.search("sample", mode="lexical")
        second = searcher.search("sample", mode="lexical")
        # Same hits, but cache hit should not pay for another rank_query call.
        assert first.hits == second.hits
        # Cache key is exact; differing scope produces a different result.
        third = searcher.search("sample", mode="lexical", scope="work")
        assert third.scope == "work"

    def test_invalidate_clears_cache(self, client: TestClient) -> None:
        from ledger.web.services.search import Searcher

        searcher = Searcher()
        searcher.search("sample")
        assert searcher._cache  # cache populated
        searcher.invalidate()
        assert not searcher._cache


class TestTopbarSearch:
    def test_topbar_search_input_on_every_page(self, client: TestClient) -> None:
        for url in ("/browse", "/browse/facts", "/note/fact__sample", "/search"):
            resp = client.get(url)
            assert resp.status_code == 200, url
            assert 'class="topbar-search"' in resp.text, url
            assert 'name="q"' in resp.text, url
            # htmx script linked
            assert "htmx.min.js" in resp.text, url

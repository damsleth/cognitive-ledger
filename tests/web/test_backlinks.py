"""Phase 3 backlinks tests - data plumbing and template rendering."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


class TestCorpusBacklinks:
    """Data-layer tests so backlinks are verifiable without HTML."""

    def test_outgoing_links_resolved(self, web_ledger_root: Path) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        # fact__sample.md links to fact__other_one (resolves) and
        # fact__does_not_exist (broken).
        assert corpus.outgoing_stems("fact__sample") == ["fact__other_one"]

    def test_broken_outgoing_captured(self, web_ledger_root: Path) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        assert "fact__does_not_exist" in corpus.broken_outgoing("fact__sample")

    def test_incoming_lists_referrer(self, web_ledger_root: Path) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        assert corpus.incoming_stems("fact__other_one") == ["fact__sample"]

    def test_incoming_empty_for_unreferenced(self, web_ledger_root: Path) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        assert corpus.incoming_stems("loop__open_test") == []

    def test_reload_updates_backlinks(self, web_ledger_root: Path) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        new_path = web_ledger_root / "notes" / "02_facts" / "fact__new_referrer.md"
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

# New Referrer

## Statement

Mentions [[fact__other_one]] to extend the backlink graph.
"""
        )
        corpus.reload()
        assert "fact__new_referrer" in corpus.incoming_stems("fact__other_one")

    def test_link_titles_pairs_with_titles(self, web_ledger_root: Path) -> None:
        from ledger.web.services.corpus import Corpus

        corpus = Corpus()
        pairs = corpus.link_titles(["fact__other_one"])
        assert pairs == [("fact__other_one", "Other Fact")]


class TestNoteTemplateBacklinks:
    def test_linked_from_panel_shows_incoming(self, client: TestClient) -> None:
        resp = client.get("/note/fact__other_one")
        assert resp.status_code == 200
        assert "Linked from" in resp.text
        assert 'href="/note/fact__sample"' in resp.text

    def test_links_out_panel_shows_outgoing(self, client: TestClient) -> None:
        resp = client.get("/note/fact__sample")
        assert resp.status_code == 200
        assert "Links out" in resp.text
        assert 'href="/note/fact__other_one"' in resp.text

    def test_empty_incoming_renders_hint(self, client: TestClient) -> None:
        resp = client.get("/note/loop__open_test")
        assert resp.status_code == 200
        assert "Linked from" in resp.text
        assert "No other notes link here yet" in resp.text

    def test_broken_links_panel_still_renders(self, client: TestClient) -> None:
        resp = client.get("/note/fact__sample")
        assert resp.status_code == 200
        assert "Broken links" in resp.text
        assert "fact__does_not_exist" in resp.text

    def test_admin_reload_refreshes_backlinks(
        self, client: TestClient, web_ledger_root: Path
    ) -> None:
        # Before adding the new referrer, fact__other_one has one incoming link.
        before = client.get("/note/fact__other_one").text
        assert before.count('href="/note/fact__sample"') >= 1
        assert "fact__new_via_reload" not in before

        new_path = web_ledger_root / "notes" / "02_facts" / "fact__new_via_reload.md"
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

# New Via Reload

## Statement

Adds a fresh backlink to [[fact__other_one]] after the server booted.
"""
        )
        # Server-side state still stale before /admin/reload fires.
        resp = client.post("/admin/reload")
        assert resp.status_code == 200

        after = client.get("/note/fact__other_one").text
        assert 'href="/note/fact__new_via_reload"' in after

"""Tests for the /graph and /graph/data.json endpoints (Phase 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


PRIVATE_NOTE = """---
created: 2026-02-13T10:00:00Z
tags: [test]
scope: dev
lang: en
---

# Private Links Note

## Statement

This note has a wikilink inside a private fence.

<private>
[[fact__sample]]
</private>

No public links here.
"""


class TestGraphPage:
    def test_graph_page_renders(self, client: TestClient) -> None:
        resp = client.get("/graph")
        assert resp.status_code == 200
        body = resp.text
        # Canvas element with data-source
        assert "graph-canvas" in body
        assert "data-source" in body
        # d3 script included
        assert "d3.v7.min.js" in body
        # graph.js included
        assert "graph.js" in body
        # at least one chip button
        assert "chip" in body

    def test_graph_page_sidebar_active(self, client: TestClient) -> None:
        resp = client.get("/graph")
        assert resp.status_code == 200
        # The graph link should be active in the sidebar
        assert "/graph" in resp.text


class TestGraphData:
    def test_data_shape(self, client: TestClient) -> None:
        resp = client.get("/graph/data.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "links" in data

        stems = {n["id"] for n in data["nodes"]}
        assert "fact__sample" in stems
        assert "fact__other_one" in stems

        # fact__sample should be typed "facts"
        sample = next(n for n in data["nodes"] if n["id"] == "fact__sample")
        assert sample["type"] == "facts"

        # Each node has required fields
        for node in data["nodes"]:
            assert "id" in node
            assert "title" in node
            assert "type" in node
            assert "scope" in node
            assert "status" in node
            assert "incoming" in node

    def test_broken_link_excluded_from_links(self, client: TestClient) -> None:
        resp = client.get("/graph/data.json")
        data = resp.json()
        link_targets = {
            lnk["target"] if isinstance(lnk["target"], str) else lnk["target"]["id"]
            for lnk in data["links"]
        }
        # fact__does_not_exist is a broken link from SAMPLE_FACT — must not appear
        assert "fact__does_not_exist" not in link_targets
        # fact__does_not_exist should not be a node either
        node_ids = {n["id"] for n in data["nodes"]}
        assert "fact__does_not_exist" not in node_ids

    def test_private_links_excluded(
        self, client: TestClient, web_ledger_root: Path
    ) -> None:
        notes_dir = web_ledger_root / "notes"
        priv_path = notes_dir / "02_facts" / "fact__private_demo.md"
        priv_path.write_text(PRIVATE_NOTE)

        # Reload the corpus to pick up the new note
        client.post("/admin/reload")

        resp = client.get("/graph/data.json")
        data = resp.json()

        # fact__private_demo should appear as a node (it is a real note)
        node_ids = {n["id"] for n in data["nodes"]}
        assert "fact__private_demo" in node_ids

        # But there should be NO link from fact__private_demo to fact__sample,
        # because that wikilink is inside a <private> fence.
        priv_links = [
            lnk
            for lnk in data["links"]
            if (
                lnk["source"] == "fact__private_demo"
                or (
                    isinstance(lnk["source"], dict)
                    and lnk["source"]["id"] == "fact__private_demo"
                )
            )
        ]
        assert priv_links == [], (
            "Expected no outgoing links from fact__private_demo, got: %r" % priv_links
        )

    def test_reload_picks_up_new_notes(
        self, client: TestClient, web_ledger_root: Path
    ) -> None:
        notes_dir = web_ledger_root / "notes"
        new_path = notes_dir / "02_facts" / "fact__brand_new.md"
        new_path.write_text(
            "---\ncreated: 2026-02-13T10:00:00Z\ntags: [test]\nlang: en\n---\n\n"
            "# Brand New Fact\n\n## Statement\n\nAdded after server start.\n"
        )

        # Before reload the note should not appear
        resp_before = client.get("/graph/data.json")
        ids_before = {n["id"] for n in resp_before.json()["nodes"]}
        assert "fact__brand_new" not in ids_before

        # Reload
        reload_resp = client.post("/admin/reload")
        assert reload_resp.status_code == 200
        assert reload_resp.json().get("ok") is True

        # Now it should appear
        resp_after = client.get("/graph/data.json")
        ids_after = {n["id"] for n in resp_after.json()["nodes"]}
        assert "fact__brand_new" in ids_after

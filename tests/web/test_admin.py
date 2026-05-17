"""Tests for /healthz and /admin/reload."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


class TestHealthz:
    def test_returns_ok(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    def test_notes_loaded_matches_fixture(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        data = resp.json()
        # Fixture has 2 facts + 2 loops = 4 notes total.
        assert data["notes_loaded"] == 4

    def test_embeddings_disabled_without_index(self, client: TestClient) -> None:
        # Fixture corpus has no semantic indexes built.
        resp = client.get("/healthz")
        assert resp.json()["embeddings_enabled"] is False

    def test_index_built_at_present(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        data = resp.json()
        # No index built in the fixture, so string is empty rather than missing.
        assert "index_built_at" in data
        assert isinstance(data["index_built_at"], str)


class TestAdminReload:
    def test_reload_returns_ok(self, client: TestClient) -> None:
        resp = client.post("/admin/reload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["notes_loaded"] >= 4

    def test_reload_picks_up_new_note(
        self, client: TestClient, web_ledger_root: Path
    ) -> None:
        before = client.get("/healthz").json()["notes_loaded"]
        new_path = web_ledger_root / "notes" / "02_facts" / "fact__via_reload.md"
        new_path.write_text(
            """---
created: 2026-04-15T10:00:00Z
updated: 2026-04-15T10:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Reload Demo

## Statement

Added after the server booted, picked up via /admin/reload.
"""
        )
        resp = client.post("/admin/reload")
        assert resp.status_code == 200
        assert resp.json()["notes_loaded"] == before + 1

    def test_reload_busts_search_cache(self, client: TestClient) -> None:
        # Prime the search cache via a /search request.
        first = client.get("/search?q=sample")
        assert first.status_code == 200

        searcher = client.app.state.searcher  # type: ignore[attr-defined]
        # Cache populated by the /search request.
        assert searcher._cache

        resp = client.post("/admin/reload")
        assert resp.status_code == 200
        assert resp.json()["search_cache_cleared"] is True
        # Cache cleared, so the searcher has no stale entries.
        assert not searcher._cache

"""Signals dashboard route (/signals)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient


class TestSignalsDashboard:
    def test_renders_with_no_signals(self, client: TestClient) -> None:
        resp = client.get("/signals")
        assert resp.status_code == 200
        assert "Signal feedback" in resp.text
        assert "Coverage" in resp.text
        # Activation state line is present (accruing when below threshold).
        assert "accruing" in resp.text
        # Sidebar link is marked active.
        assert 'href="/signals"' in resp.text

    def test_reflects_logged_signals(self, client: TestClient) -> None:
        from ledger import signals

        signals.append_signal("affirmation", note="notes/02_facts/fact__sample.md")
        resp = client.get("/signals")
        assert resp.status_code == 200
        assert "1 signal recorded" in resp.text
        assert "affirmation" in resp.text

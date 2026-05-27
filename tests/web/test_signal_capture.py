"""Use-time signal capture in the web UI, gated on signals_auto_capture.

Search-with-no-hits logs a retrieval_miss; opening a note from a search
result logs a retrieval_hit. Both only when the flag is enabled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from ledger import signals
from ledger.config import get_config
from ledger.web.server import create_app


@pytest.fixture
def capture_client(web_ledger_root: Path) -> Generator[TestClient, None, None]:
    """Client whose config has signals_auto_capture enabled."""
    cfg = get_config()
    cfg.signals_auto_capture = True
    app = create_app(config=cfg)
    with TestClient(app) as c:
        yield c


class TestMissCapture:
    def test_empty_search_logs_miss_when_enabled(self, capture_client: TestClient) -> None:
        capture_client.get("/search?q=zzzzz_no_such_token_zzzzz")
        sigs = signals.read_signals()
        assert len(sigs) == 1
        assert sigs[0]["type"] == "retrieval_miss"
        assert sigs[0]["query"] == "zzzzz_no_such_token_zzzzz"

    def test_hit_search_does_not_log_miss(self, capture_client: TestClient) -> None:
        capture_client.get("/search?q=sample")
        misses = [s for s in signals.read_signals() if s["type"] == "retrieval_miss"]
        assert misses == []

    def test_disabled_logs_nothing(self, client: TestClient) -> None:
        # default `client` fixture has signals_auto_capture off
        client.get("/search?q=zzzzz_no_such_token_zzzzz")
        assert signals.read_signals() == []


class TestHitCapture:
    def test_open_from_search_logs_hit(self, capture_client: TestClient) -> None:
        capture_client.get("/note/fact__sample?from=search&q=sample")
        hits = [s for s in signals.read_signals() if s["type"] == "retrieval_hit"]
        assert len(hits) == 1
        assert hits[0]["query"] == "sample"
        assert hits[0]["note"].endswith("fact__sample.md")

    def test_direct_open_logs_nothing(self, capture_client: TestClient) -> None:
        capture_client.get("/note/fact__sample")
        assert signals.read_signals() == []

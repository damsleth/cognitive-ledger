"""Tests for the MCP server (plan 44). Skipped when the `mcp` extra is absent."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("mcp", reason="requires the optional [mcp] extra")

from ledger.mcp.server import create_server, scrub_for_egress


def _tool_names(server) -> set[str]:
    return {t.name for t in asyncio.run(server.list_tools())}


class TestScrub:
    def test_strips_private_from_strings(self):
        assert scrub_for_egress("a <private>secret</private> b") == "a  b"

    def test_recurses_dicts_and_lists(self):
        out = scrub_for_egress({"k": ["x <private>Y</private>", {"n": "<private>Z</private>q"}]})
        assert "Y" not in str(out) and "Z" not in str(out)
        assert out["k"][1]["n"] == "q"

    def test_passes_through_non_strings(self):
        assert scrub_for_egress({"n": 3, "f": 1.5, "b": True}) == {"n": 3, "f": 1.5, "b": True}


class TestToolRegistration:
    def test_read_tools_present(self):
        names = _tool_names(create_server(allow_write=False))
        assert {"ledger_query", "ledger_recall_as_of", "ledger_changed_since",
                "ledger_context", "ledger_answer"} <= names

    def test_remember_absent_when_write_disabled(self):
        assert "ledger_remember" not in _tool_names(create_server(allow_write=False))

    def test_remember_present_when_write_enabled(self):
        assert "ledger_remember" in _tool_names(create_server(allow_write=True))

    def test_yaams_tool_gated(self):
        assert "yaams_query" not in _tool_names(create_server())
        assert "yaams_query" in _tool_names(create_server(with_yaams=True))


class TestToolsAgainstCorpus:
    @pytest.fixture
    def ledger(self, tmp_path, monkeypatch):
        """Minimal corpus with one note containing a private fence."""
        from ledger.config import LedgerConfig, set_config, reset_config
        notes = tmp_path / "notes"
        facts = notes / "02_facts"
        facts.mkdir(parents=True)
        (facts / "fact__deploy.md").write_text(
            "---\ncreated: 2026-06-01T00:00:00Z\nupdated: 2026-06-01T00:00:00Z\n"
            "tags: [deploy]\nconfidence: 0.9\nsource: user\nscope: all\n---\n"
            "# Deploy\n\n## Statement\n\nDeploys run nightly. "
            "<private>the cron secret is hunter2</private>\n",
            encoding="utf-8",
        )
        cfg = LedgerConfig(ledger_root=tmp_path, ledger_notes_dir=notes)
        set_config(cfg)
        yield cfg
        reset_config()

    def test_query_tool_returns_scrubbed_results(self, ledger):
        server = create_server(config=ledger)
        result = asyncio.run(server.call_tool("ledger_query", {"query": "deploy", "limit": 3}))
        # FastMCP returns (content, structured) or content; assert no leak in the raw text.
        text = str(result)
        assert "hunter2" not in text
        assert "deploy" in text.lower()

    def test_remember_writes_to_inbox(self, ledger):
        server = create_server(config=ledger, allow_write=True)
        asyncio.run(server.call_tool("ledger_remember", {"text": "remember to ship", "tags": "todo"}))
        inbox = ledger.ledger_notes_dir / "00_inbox"
        captures = list(inbox.glob("*.md"))
        assert captures, "expected an inbox capture"
        assert "remember to ship" in captures[0].read_text(encoding="utf-8")

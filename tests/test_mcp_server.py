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
                "ledger_context"} <= names

    def test_answer_registered_only_with_a_real_backend(self, tmp_path):
        from ledger.config import LedgerConfig, reset_config
        try:
            dummy = LedgerConfig(ledger_root=tmp_path, synth_backend="dummy")
            assert "ledger_answer" not in _tool_names(create_server(config=dummy))
            real = LedgerConfig(ledger_root=tmp_path, synth_backend="ollama")
            assert "ledger_answer" in _tool_names(create_server(config=real))
        finally:
            reset_config()

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

    def test_remember_capture_is_lint_clean_and_logged(self, ledger):
        from ledger.mcp.server import _write_inbox_note
        from ledger.parsing import parse_frontmatter_text
        from ledger.validation import validate_frontmatter_fields

        first = Path(_write_inbox_note("ship the release og sjekk loggen", title="Ship it")["created"])
        second = Path(_write_inbox_note("ship the release og sjekk loggen", title="Ship it")["created"])
        assert first.name == "note__ship_it.md" and second.name == "note__ship_it_1.md"
        fm, _ = parse_frontmatter_text(first.read_text(encoding="utf-8"))
        assert validate_frontmatter_fields(fm, rel_path="notes/00_inbox/" + first.name) == []
        assert fm["scope"] == "meta" and fm["via"] == "mcp" and fm["lang"] in {"en", "no", "mixed"}
        timeline = ledger.timeline_path.with_suffix(".jsonl")
        logged = timeline.read_text(encoding="utf-8") if timeline.exists() else ledger.timeline_path.read_text(encoding="utf-8")
        assert "note__ship_it.md" in logged

    def test_failed_capture_leaves_no_empty_note(self, ledger, monkeypatch):
        from ledger import text as text_mod
        from ledger.mcp.server import _write_inbox_note

        def boom(*_args, **_kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(text_mod, "write_markdown", boom)
        with pytest.raises(OSError):
            _write_inbox_note("will fail", title="Doomed")
        assert not list((ledger.ledger_notes_dir / "00_inbox").glob("note__doomed*.md"))

    def test_query_reply_is_compact_unless_verbose(self, ledger):
        import json

        server = create_server(config=ledger)

        def reply(args):
            result = asyncio.run(server.call_tool("ledger_query", args))
            content = result[0] if isinstance(result, tuple) else result
            return json.loads(content[0].text)

        compact = reply({"query": "deploy", "limit": 3})
        verbose = reply({"query": "deploy", "limit": 3, "verbose": True})
        assert set(compact) == {"query", "scope", "effective_retrieval_mode", "results"}
        assert compact["results"] and "reasons" not in compact["results"][0]
        assert "timing" in verbose and "reasons" in verbose["results"][0]
        assert len(json.dumps(compact)) < len(json.dumps(verbose))
        assert len(json.dumps(compact)) < 2000

    def test_view_is_validated(self, ledger):
        server = create_server(config=ledger)
        with pytest.raises(Exception):
            asyncio.run(server.call_tool("ledger_changed_since",
                                         {"query": "deploy", "since": "2026-01-01", "view": "everything"}))

    def test_signal_tool_gated_and_validated(self, ledger):
        import json

        assert "ledger_signal" not in _tool_names(create_server(config=ledger))
        server = create_server(config=ledger, allow_write=True)
        assert "ledger_signal" in _tool_names(server)

        asyncio.run(server.call_tool("ledger_signal", {
            "signal_type": "retrieval_hit", "note": "notes/02_facts/fact__deploy.md", "query": "deploy"}))
        logged = [json.loads(line) for line in ledger.signals_path.read_text(encoding="utf-8").splitlines()]
        assert logged[-1]["type"] == "retrieval_hit" and logged[-1]["channel"] == "mcp"

        for bad in (
            {"signal_type": "llm_judged", "note": "notes/02_facts/fact__deploy.md"},
            {"signal_type": "retrieval_hit", "note": "notes/02_facts/fact__missing.md"},
            {"signal_type": "retrieval_hit", "note": "../etc/passwd"},
            {"signal_type": "retrieval_miss"},
            {"signal_type": "rating"},
        ):
            with pytest.raises(Exception):
                asyncio.run(server.call_tool("ledger_signal", bad))
        assert len(ledger.signals_path.read_text(encoding="utf-8").splitlines()) == 1

    def test_remember_rejects_all_scope(self, ledger):
        server = create_server(config=ledger, allow_write=True)
        with pytest.raises(Exception):
            asyncio.run(server.call_tool("ledger_remember", {"text": "x", "scope": "all"}))

    @pytest.mark.parametrize("tool,args", [
        ("ledger_query", {"query": ""}),
        ("ledger_query", {"query": "   "}),
        ("ledger_recall_as_of", {"query": "deploy", "as_of": "garbage"}),
        ("ledger_changed_since", {"query": "deploy", "since": "last tuesday"}),
    ])
    def test_bad_input_is_a_tool_error(self, ledger, tool, args):
        server = create_server(config=ledger)
        with pytest.raises(Exception) as exc:
            asyncio.run(server.call_tool(tool, args))
        assert "empty" in str(exc.value) or "YYYY-MM-DD" in str(exc.value)

    @pytest.mark.parametrize("limit", [0, -1, 51, 100000])
    def test_limit_is_clamped(self, ledger, limit):
        from ledger.mcp.server import MAX_LIMIT, _check_query
        assert 1 <= _check_query("deploy", limit) <= MAX_LIMIT
        server = create_server(config=ledger)
        result = asyncio.run(server.call_tool("ledger_query", {"query": "deploy", "limit": limit}))
        assert "deploy" in str(result).lower()

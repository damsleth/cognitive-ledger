"""Tests for `ledger embed search --batch` (JSONL in, JSONL out)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.semantic import batch_semantic_search_lines


def _ok_search(query, **kwargs):
    """Stand-in search_fn: echoes back what it was asked."""
    return {
        "query": query,
        "target": kwargs.get("target"),
        "limit": kwargs.get("limit"),
        "available": True,
    }


class BatchLibraryTests(unittest.TestCase):
    """batch_semantic_search_lines: ordering, defaults, and error isolation."""

    def test_results_come_back_in_input_order(self):
        lines = ['{"query": "first"}', '{"query": "second"}', '{"query": "third"}']
        out = list(batch_semantic_search_lines(lines, search_fn=_ok_search))
        self.assertEqual([o["query"] for o in out], ["first", "second", "third"])

    def test_blank_lines_are_skipped(self):
        lines = ["", '{"query": "a"}', "   ", '{"query": "b"}', "\n"]
        out = list(batch_semantic_search_lines(lines, search_fn=_ok_search))
        self.assertEqual([o["query"] for o in out], ["a", "b"])

    def test_per_line_limit_and_target_override_defaults(self):
        lines = [
            '{"query": "a"}',
            '{"query": "b", "limit": 2, "target": "source"}',
        ]
        out = list(
            batch_semantic_search_lines(
                lines, default_target="ledger", default_limit=7, search_fn=_ok_search
            )
        )
        self.assertEqual((out[0]["target"], out[0]["limit"]), ("ledger", 7))
        self.assertEqual((out[1]["target"], out[1]["limit"]), ("source", 2))

    def test_invalid_json_line_emits_error_and_continues(self):
        lines = ["not json", '{"query": "after"}']
        out = list(batch_semantic_search_lines(lines, search_fn=_ok_search))
        self.assertEqual(len(out), 2)
        self.assertIn("error", out[0])
        self.assertEqual(out[1]["query"], "after")

    def test_non_object_and_bad_target_and_empty_query_lines_error(self):
        lines = [
            '["query"]',
            '{"query": "x", "target": "bogus"}',
            '{"query": ""}',
            '{}',
            '{"query": "ok"}',
        ]
        out = list(batch_semantic_search_lines(lines, search_fn=_ok_search))
        self.assertEqual(len(out), 5)
        for bad in out[:4]:
            self.assertEqual(sorted(bad), ["error"], f"expected error line, got {bad}")
        self.assertEqual(out[4]["query"], "ok")

    def test_search_exception_does_not_kill_the_batch(self):
        def flaky(query, **kwargs):
            if query == "boom":
                raise RuntimeError("encoder exploded")
            return _ok_search(query, **kwargs)

        lines = ['{"query": "boom"}', '{"query": "fine"}']
        out = list(batch_semantic_search_lines(lines, search_fn=flaky))
        self.assertEqual(out[0], {"error": "encoder exploded"})
        self.assertEqual(out[1]["query"], "fine")


class BatchCliTests(unittest.TestCase):
    """End-to-end handler run: real search path against a missing index."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        set_config(
            LedgerConfig(
                ledger_root=tmp / "repo",
                ledger_notes_dir=tmp / "ledger-notes",
                source_notes_dir=tmp / "notes",
            )
        )
        import ledger.cli as cli
        self.cli = cli

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_batch_writes_one_json_line_per_request(self):
        args = SimpleNamespace(
            batch=True,
            query=None,
            target="ledger",
            limit=5,
            embed_backend=None,
            embed_model=None,
            allow_api_on_source=False,
            json=False,
        )
        stdin = io.StringIO('{"query": "alpha"}\nnot json\n{"query": "beta"}\n')
        out = io.StringIO()
        with patch("sys.stdin", stdin), contextlib.redirect_stdout(out):
            self.cli.handle_embed_search_command(args)
        lines = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(lines), 3)
        # No index built: real payloads say available: false, in input order.
        self.assertFalse(lines[0]["available"])
        self.assertEqual(sorted(lines[1]), ["error"])
        self.assertFalse(lines[2]["available"])
        # Every output line is compact single-line JSON (JSONL contract).
        for raw in out.getvalue().splitlines():
            self.assertNotIn("\n", raw)
            json.loads(raw)

    def test_parser_accepts_batch_and_rejects_batch_plus_query(self):
        parser_err = io.StringIO()
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(parser_err):
            self.cli.main(["embed", "search", "--batch", "--query", "x"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("not allowed with", parser_err.getvalue())

    def test_parser_requires_query_or_batch(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(io.StringIO()):
            self.cli.main(["embed", "search"])
        self.assertEqual(cm.exception.code, 2)

    def test_batch_documented_in_help(self):
        buf = io.StringIO()
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
            self.cli.main(["embed", "search", "--help"])
        help_text = buf.getvalue()
        self.assertIn("--batch", help_text)
        self.assertIn("JSONL", help_text)

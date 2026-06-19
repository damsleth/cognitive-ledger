"""Tests for the `ledger embed search` CLI subcommand (Part 1 of plan 38)."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ledger.config import LedgerConfig, reset_config, set_config


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_cli():
    import ledger.cli as module
    return module


def _capture(fn, *args, **kwargs):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rv = fn(*args, **kwargs)
    return rv, out.getvalue(), err.getvalue()


class EmbedSearchMissingIndexTests(unittest.TestCase):
    """Tests for embed search behaviour when no index has been built."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        config = LedgerConfig(
            ledger_root=tmp / "repo",
            ledger_notes_dir=tmp / "ledger-notes",
            source_notes_dir=tmp / "notes",
        )
        set_config(config)
        self.cli = load_cli()

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _make_args(self, **overrides):
        defaults = dict(
            query="x",
            target="ledger",
            limit=5,
            embed_backend=None,
            embed_model=None,
            allow_api_on_source=False,
            json=True,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_embed_search_missing_index_returns_available_false(self):
        """Missing index exits 0 with available: false and correct contract shape."""
        args = self._make_args(query="climate resilience")
        _, out, _ = _capture(self.cli.handle_embed_search_command, args)
        payload = json.loads(out)

        self.assertIn("available", payload)
        self.assertFalse(payload["available"])
        self.assertIn("reason", payload)
        self.assertEqual(payload["reason"], "missing_index")
        # Contract shape fields must all be present
        for field in ("target", "backend", "model", "index_built_at", "results"):
            self.assertIn(field, payload, f"missing field: {field}")
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["index_built_at"], "")

    def test_embed_search_missing_index_exits_zero(self):
        """Missing index must not raise SystemExit — it is not an error."""
        args = self._make_args(query="x", json=True)
        try:
            _, out, _ = _capture(self.cli.handle_embed_search_command, args)
        except SystemExit as exc:
            self.fail(
                f"handle_embed_search_command raised SystemExit({exc.code}) "
                "on missing index — should exit 0 (or not at all)"
            )
        payload = json.loads(out)
        self.assertFalse(payload["available"])

    def test_embed_search_validates_query_empty_raises_systemexit(self):
        """Empty query must raise SystemExit(2) via validate_query."""
        args = self._make_args(query="", json=True)
        with self.assertRaises(SystemExit) as cm:
            _capture(self.cli.handle_embed_search_command, args)
        self.assertEqual(cm.exception.code, 2)

    def test_embed_search_validates_query_none_raises_systemexit(self):
        """None query must raise SystemExit(2) via validate_query."""
        args = self._make_args(query=None, json=True)
        with self.assertRaises(SystemExit) as cm:
            _capture(self.cli.handle_embed_search_command, args)
        self.assertEqual(cm.exception.code, 2)

    def test_embed_search_human_output_on_missing_index(self):
        """Human-readable output shows available: no and reason."""
        args = self._make_args(query="x", json=False)
        _, out, _ = _capture(self.cli.handle_embed_search_command, args)
        self.assertIn("available", out)
        self.assertIn("missing_index", out)

    def test_embed_search_parser_wiring(self):
        """cli.main(['embed', 'search', '--query', 'x', '--json']) exits 0."""
        try:
            exit_code = self.cli.main(["embed", "search", "--query", "x", "--json"])
        except SystemExit as exc:
            if exc.code not in (0, None):
                self.fail(
                    f"cli.main exited with code {exc.code!r}; "
                    "expected 0 (missing index is not an error)"
                )
            exit_code = exc.code or 0
        self.assertIn(exit_code, (0, None))

    def test_embed_search_parser_lists_in_help(self):
        """embed --help output must list 'search' alongside build/status/clean."""
        with self.assertRaises(SystemExit):
            _capture(self.cli.main, ["embed", "--help"])
        # argparse writes help to stdout; also tolerate stderr
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                self.cli.main(["embed", "--help"])
        except SystemExit:
            pass
        combined = out_buf.getvalue() + err_buf.getvalue()
        self.assertIn("search", combined)

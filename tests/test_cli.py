"""Tests for the ledger CLI module (ledger/cli.py).

Covers the dispatch and error-handling surface that the per-handler library
tests don't reach: argument validation, subcommand routing, JSON-vs-human
output paths, and exit-code contracts.
"""

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


class CLIPathsCommandTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        config = LedgerConfig(
            ledger_root=tmp / "repo",
            ledger_notes_dir=tmp / "ledger-notes",
            source_notes_dir=tmp / "notes",
        )
        set_config(config)
        self.config = config
        self.cli = load_cli()

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_paths_no_field_no_json_prints_all_fields(self):
        _, out, _ = _capture(
            self.cli.handle_paths_command,
            SimpleNamespace(field=None, json=False),
        )
        self.assertIn("ledger_root:", out)
        self.assertIn("ledger_notes_dir:", out)
        self.assertIn("source_notes_dir:", out)
        self.assertIn("timeline_path:", out)
        self.assertIn(str(self.config.ledger_notes_dir), out)

    def test_paths_json_returns_parseable_payload(self):
        _, out, _ = _capture(
            self.cli.handle_paths_command,
            SimpleNamespace(field=None, json=True),
        )
        payload = json.loads(out)
        self.assertEqual(payload["ledger_root"], str(self.config.ledger_root))
        self.assertEqual(payload["ledger_notes_dir"], str(self.config.ledger_notes_dir))
        self.assertEqual(payload["source_notes_dir"], str(self.config.source_notes_dir))
        self.assertIn("timeline_path", payload)

    def test_paths_field_prints_bare_value(self):
        _, out, _ = _capture(
            self.cli.handle_paths_command,
            SimpleNamespace(field="source_notes_dir", json=False),
        )
        self.assertEqual(out.strip(), str(self.config.source_notes_dir))


class CLIQueryValidationTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()

    def _args(self, **overrides):
        defaults = dict(
            text="hello",
            scope="all",
            limit=8,
            retrieval_mode="legacy",
            embed_backend="local",
            embed_model=None,
            view="context",
            json=False,
            bundle=False,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_empty_query_exits_with_code_2(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(self.cli.handle_query_command, self._args(text="   "))
        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_scope_exits_with_code_2(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(self.cli.handle_query_command, self._args(scope="bogus-scope"))
        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_limit_exits_with_code_2(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(self.cli.handle_query_command, self._args(limit=0))
        self.assertEqual(ctx.exception.code, 2)

    def test_limit_above_max_exits_with_code_2(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(self.cli.handle_query_command, self._args(limit=99999))
        self.assertEqual(ctx.exception.code, 2)


class CLIDiscoverSourceValidationTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()

    def _args(self, **overrides):
        defaults = dict(
            text="search term",
            limit=5,
            source_notes_dir=".",
            embed_backend="local",
            embed_model=None,
            allow_api_on_source=False,
            json=False,
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_empty_query_exits_with_code_2(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(self.cli.handle_discover_source_command, self._args(text=""))
        self.assertEqual(ctx.exception.code, 2)

    def test_invalid_limit_exits_with_code_2(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(self.cli.handle_discover_source_command, self._args(limit=-1))
        self.assertEqual(ctx.exception.code, 2)


class CLISubcommandRoutingTests(unittest.TestCase):
    """Subcommand-less dispatch must surface a usage hint and a non-zero exit."""

    def setUp(self):
        self.cli = load_cli()

    def test_signal_command_no_subcommand_exits_1(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(
                self.cli.handle_signal_command,
                SimpleNamespace(signal_command=None),
            )
        self.assertEqual(ctx.exception.code, 1)

    def test_signal_command_no_subcommand_prints_usage(self):
        out_buf = io.StringIO()
        with contextlib.redirect_stdout(out_buf):
            with contextlib.suppress(SystemExit):
                self.cli.handle_signal_command(SimpleNamespace(signal_command=None))
        self.assertIn("Usage: ledger signal", out_buf.getvalue())

    def test_inbox_command_no_subcommand_exits_1(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(
                self.cli.handle_inbox_command,
                SimpleNamespace(inbox_command=None),
            )
        self.assertEqual(ctx.exception.code, 1)

    def test_voice_dna_command_no_subcommand_exits_1(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(
                self.cli.handle_voice_dna_command,
                SimpleNamespace(voice_command=None),
            )
        self.assertEqual(ctx.exception.code, 1)

    def test_ingest_command_no_subcommand_exits_1(self):
        with self.assertRaises(SystemExit) as ctx:
            _capture(
                self.cli.handle_ingest_command,
                SimpleNamespace(ingest_command=None, source_notes_dir=None),
            )
        self.assertEqual(ctx.exception.code, 1)


class CLIEvalBaselinePathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        config = LedgerConfig(
            ledger_root=tmp / "repo",
            ledger_notes_dir=tmp / "repo" / "notes",
            source_notes_dir=tmp / "source",
        )
        config.ledger_root.mkdir(parents=True, exist_ok=True)
        set_config(config)
        self.config = config
        self.cli = load_cli()

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_write_baseline_outside_ledger_root_exits_2(self):
        # Path completely outside the ledger root should be rejected before any work happens
        outside = Path(self._tmp.name) / "elsewhere" / "baseline.json"
        args = SimpleNamespace(
            cases="/nonexistent/cases.yaml",
            k=3,
            retrieval_mode="legacy",
            embed_backend="local",
            embed_model=None,
            strict_cases=False,
            baseline=None,
            write_baseline=str(outside),
            json=True,
            emit_ranks=False,
        )
        with self.assertRaises(SystemExit) as ctx:
            _capture(self.cli.handle_eval_command, args)
        self.assertEqual(ctx.exception.code, 2)


class CLIListingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        notes_dir = tmp / "notes"
        for folder in (
            "00_inbox", "01_identity", "02_facts", "03_preferences",
            "04_goals", "05_open_loops", "06_concepts", "07_projects",
            "08_indices", "09_archive",
        ):
            (notes_dir / folder).mkdir(parents=True)
        config = LedgerConfig(
            ledger_root=tmp,
            ledger_notes_dir=notes_dir,
            source_notes_dir=tmp / "source",
        )
        set_config(config)
        self.cli = load_cli()

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_list_items_empty_prints_no_notes(self):
        args = SimpleNamespace(limit=10, width=80, paths=False)
        _, out, _ = _capture(self.cli.list_items, args, "facts")
        self.assertIn("No notes found.", out)

    def test_verbose_items_empty_prints_no_notes(self):
        args = SimpleNamespace(limit=10, width=80, paths=False)
        _, out, _ = _capture(self.cli.verbose_items, args, "facts")
        self.assertIn("No notes found.", out)

    def test_list_items_for_loops_empty_prints_no_notes(self):
        args = SimpleNamespace(limit=10, width=80, paths=False)
        _, out, _ = _capture(self.cli.list_items, args, "loops", loop_status="open")
        self.assertIn("No notes found.", out)


class CLIMainEntryPointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        config = LedgerConfig(
            ledger_root=tmp / "repo",
            ledger_notes_dir=tmp / "notes",
            source_notes_dir=tmp / "source",
        )
        set_config(config)
        self.cli = load_cli()

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_no_command_returns_zero_and_prints_help(self):
        rv, out, _ = _capture(self.cli.main, [])
        self.assertEqual(rv, 0)
        self.assertIn("Cognitive Ledger", out)

    def test_unknown_command_argparse_exits(self):
        # argparse exits with 2 for unknown commands
        with self.assertRaises(SystemExit):
            _capture(self.cli.main, ["definitely-not-a-command"])

    def test_paths_via_main_field_flag(self):
        from ledger.config import get_config
        rv, out, _ = _capture(self.cli.main, ["paths", "--field", "ledger_notes_dir"])
        self.assertEqual(rv, 0)
        self.assertEqual(out.strip(), str(get_config().ledger_notes_dir))


class CLIVersionConsistencyTests(unittest.TestCase):
    """The package __version__ must match the pyproject distribution version."""

    def test_init_version_matches_pyproject(self):
        import ledger
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        # Parse the first `version = "..."` line under [project]
        import re
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, flags=re.MULTILINE)
        assert m is not None, "pyproject.toml must declare version"
        self.assertEqual(ledger.__version__, m.group(1))


if __name__ == "__main__":
    unittest.main()

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
import time
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


class CLIHandlerCoverageTests(unittest.TestCase):
    """Exercise individual handler functions end-to-end with a temp ledger."""

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
        (notes_dir / "08_indices" / "timeline.md").write_text("# Timeline\n", encoding="utf-8")
        config = LedgerConfig(
            ledger_root=tmp,
            ledger_notes_dir=notes_dir,
            source_notes_dir=tmp / "source",
        )
        set_config(config)
        self.cfg = config
        self.cli = load_cli()
        self._tmp_path = tmp

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    # ---- list_items / verbose_items with real notes ----------------------

    def _write_fact(self, slug: str, statement: str = "A fact.") -> Path:
        path = self.cfg.ledger_notes_dir / "02_facts" / f"fact__{slug}.md"
        path.write_text(
            f"---\ncreated: 2026-04-30T00:00:00Z\nupdated: 2026-04-30T00:00:00Z\n"
            f"tags: [test]\nconfidence: 0.9\nsource: user\nscope: dev\nlang: en\n---\n\n"
            f"# Fact: {slug}\n\n## Statement\n\n{statement}\n",
            encoding="utf-8",
        )
        return path

    def test_list_items_with_facts_prints_count_and_lines(self):
        self._write_fact("alpha")
        self._write_fact("beta")
        args = SimpleNamespace(limit=10, width=120, paths=False)
        _, out, _ = _capture(self.cli.list_items, args, "facts")
        self.assertIn("facts (2)", out)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_verbose_items_includes_detail_block(self):
        self._write_fact("gamma", statement="Specific marker text.")
        args = SimpleNamespace(limit=10, width=120, paths=False)
        _, out, _ = _capture(self.cli.verbose_items, args, "facts")
        self.assertIn("gamma", out)

    def test_list_items_loops_with_status_filter(self):
        loop = self.cfg.ledger_notes_dir / "05_open_loops" / "loop__active.md"
        loop.write_text(
            "---\ncreated: 2026-04-30T00:00:00Z\nupdated: 2026-04-30T00:00:00Z\n"
            "tags: [test]\nconfidence: 0.9\nsource: user\nscope: dev\nlang: en\n"
            "status: open\n---\n\n# Loop: active\n\n## Question or task\n\nstuff\n",
            encoding="utf-8",
        )
        args = SimpleNamespace(limit=10, width=120, paths=False)
        _, out, _ = _capture(self.cli.list_items, args, "loops", loop_status="open")
        self.assertIn("[open]", out)
        self.assertIn("active", out)

    # ---- handle_signal_command ---------------------------------------------

    def test_signal_add_prints_json_entry(self):
        args = SimpleNamespace(
            signal_command="add",
            type="affirmation",
            query=None, note="notes/02_facts/fact__alpha.md",
            detail=None, rating=None, session=None,
        )
        _, out, _ = _capture(self.cli.handle_signal_command, args)
        # The first stdout line should be a JSON object with at least these keys
        first_line = out.strip().splitlines()[0]
        entry = json.loads(first_line)
        self.assertEqual(entry["type"], "affirmation")

    def test_signal_stats_runs_with_empty_signals(self):
        args = SimpleNamespace(signal_command="stats")
        _, out, _ = _capture(self.cli.handle_signal_command, args)
        self.assertIn("Total signals", out)

    def test_signal_summarize_runs(self):
        args = SimpleNamespace(signal_command="summarize")
        _, out, _ = _capture(self.cli.handle_signal_command, args)
        self.assertIn("Signal summary written", out)

    # ---- handle_inbox_command ---------------------------------------------

    def test_inbox_list_empty(self):
        args = SimpleNamespace(inbox_command="list")
        _, out, _ = _capture(self.cli.handle_inbox_command, args)
        self.assertIn("Inbox is empty", out)

    def test_inbox_list_with_items(self):
        inbox_item = self.cfg.ledger_notes_dir / "00_inbox" / "fact__sample.md"
        inbox_item.write_text("---\n---\n\n# Sample\n\nbody\n", encoding="utf-8")
        args = SimpleNamespace(inbox_command="list")
        _, out, _ = _capture(self.cli.handle_inbox_command, args)
        self.assertIn("fact__sample.md", out)
        self.assertIn("Sample", out)

    def test_inbox_triage_empty(self):
        args = SimpleNamespace(inbox_command="triage")
        _, out, _ = _capture(self.cli.handle_inbox_command, args)
        self.assertIn("Inbox is empty", out)

    def test_inbox_triage_with_items(self):
        inbox_item = self.cfg.ledger_notes_dir / "00_inbox" / "pref_thing.md"
        inbox_item.write_text("---\n---\n\n# Pref\n\nI prefer two-space indent\n", encoding="utf-8")
        args = SimpleNamespace(inbox_command="triage")
        _, out, _ = _capture(self.cli.handle_inbox_command, args)
        self.assertIn("preferences", out)

    # ---- handle_voice_dna_command -----------------------------------------

    def test_voice_dna_show_when_no_profile_exits_1(self):
        args = SimpleNamespace(voice_command="show")
        with self.assertRaises(SystemExit) as ctx:
            _capture(self.cli.handle_voice_dna_command, args)
        self.assertEqual(ctx.exception.code, 1)

    def test_voice_dna_import_writes_identity_note(self):
        json_path = self._tmp_path / "voice.json"
        json_path.write_text(json.dumps({"tone": "direct"}), encoding="utf-8")
        args = SimpleNamespace(voice_command="import", json_path=str(json_path))
        _, out, _ = _capture(self.cli.handle_voice_dna_command, args)
        self.assertIn("Voice DNA imported", out)

    def test_voice_dna_show_after_import_returns_profile(self):
        json_path = self._tmp_path / "voice.json"
        json_path.write_text(json.dumps({"tone": "direct"}), encoding="utf-8")
        _capture(
            self.cli.handle_voice_dna_command,
            SimpleNamespace(voice_command="import", json_path=str(json_path)),
        )
        _, out, _ = _capture(
            self.cli.handle_voice_dna_command,
            SimpleNamespace(voice_command="show"),
        )
        payload = json.loads(out)
        self.assertEqual(payload["tone"], "direct")

    # ---- handle_briefing_command ------------------------------------------

    def test_briefing_daily_runs(self):
        args = SimpleNamespace(weekly=False)
        _, out, _ = _capture(self.cli.handle_briefing_command, args)
        self.assertGreater(len(out), 0)

    def test_briefing_weekly_runs(self):
        args = SimpleNamespace(weekly=True)
        _, out, _ = _capture(self.cli.handle_briefing_command, args)
        self.assertGreater(len(out), 0)

    # ---- handle_embed_search_command -----------------------------------------

    def test_embed_search_json_prints_contract_payload(self):
        """handle_embed_search_command --json prints the contract shape."""
        import ledger.cli as cli_mod
        import ledger.semantic as sem_mod

        canned = {
            "target": "ledger",
            "backend": "local",
            "model": "BAAI/bge-m3",
            "available": True,
            "reason": "",
            "index_built_at": "2026-06-09T10:00:00Z",
            "index_item_count": 3,
            "results": [
                {
                    "rel_path": "02_facts/fact__test.md",
                    "type": "fact",
                    "scope": "work",
                    "status": "active",
                    "lang": "en",
                    "updated": "2026-05-01T00:00:00Z",
                    "cosine_similarity": 0.91,
                }
            ],
        }
        original = sem_mod.semantic_search_target
        try:
            sem_mod.semantic_search_target = lambda *a, **kw: canned
            cli_mod.semantic_lib = sem_mod
            args = SimpleNamespace(
                query="test query",
                target="ledger",
                limit=5,
                embed_backend=None,
                embed_model=None,
                allow_api_on_source=False,
                json=True,
            )
            _, out, _ = _capture(cli_mod.handle_embed_search_command, args)
        finally:
            sem_mod.semantic_search_target = original

        payload = json.loads(out)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["target"], "ledger")
        self.assertIn("index_built_at", payload)
        self.assertIn("results", payload)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["rel_path"], "02_facts/fact__test.md")

    def test_embed_search_validates_query(self):
        """handle_embed_search_command raises SystemExit on empty query."""
        import ledger.cli as cli_mod

        args = SimpleNamespace(
            query="",
            target="ledger",
            limit=5,
            embed_backend=None,
            embed_model=None,
            allow_api_on_source=False,
            json=True,
        )
        with self.assertRaises(SystemExit):
            _capture(cli_mod.handle_embed_search_command, args)

    def test_embed_search_parser_wiring(self):
        """main(['embed', 'search', '--query', 'x', '--json']) dispatches without error."""
        import ledger.cli as cli_mod
        import ledger.semantic as sem_mod

        canned = {
            "target": "ledger",
            "backend": "local",
            "model": "BAAI/bge-m3",
            "available": False,
            "reason": "missing_index",
            "index_built_at": "",
            "index_item_count": 0,
            "results": [],
        }
        original = sem_mod.semantic_search_target
        try:
            sem_mod.semantic_search_target = lambda *a, **kw: canned
            cli_mod.semantic_lib = sem_mod
            rv, out, _ = _capture(cli_mod.main, ["embed", "search", "--query", "x", "--json"])
        finally:
            sem_mod.semantic_search_target = original

        self.assertEqual(rv, 0)
        payload = json.loads(out)
        self.assertFalse(payload["available"])
        self.assertEqual(payload["reason"], "missing_index")

    # ---- handle_ingest_command --------------------------------------------

    def test_ingest_scan_with_empty_source(self):
        # Create the source notes dir but leave it empty
        (self._tmp_path / "source").mkdir(exist_ok=True)
        args = SimpleNamespace(
            ingest_command="scan",
            source_notes_dir=str(self._tmp_path / "source"),
        )
        _, out, _ = _capture(self.cli.handle_ingest_command, args)
        self.assertIn("No source files found", out)

    # ---- handle_context_command (boot/identity/json) ----------------------
    # These were dropped in 0266184/a8d33b9 because the cached _config in
    # cli.py module-load shadowed the test config. Plan 34 dropped that
    # cache, so these handlers now read live config and these tests pass.

    def test_context_format_identity_with_no_notes(self):
        args = SimpleNamespace(
            context_command=None, format="identity",
            ledger_notes_dir=None, output=None, output_dir=None,
        )
        _, out, _ = _capture(self.cli.handle_context_command, args)
        self.assertIn("No identity notes found", out)

    def test_context_format_json_runs(self):
        args = SimpleNamespace(
            context_command=None, format="json",
            ledger_notes_dir=None, output=None, output_dir=None,
        )
        _, out, _ = _capture(self.cli.handle_context_command, args)
        # JSON output should be parseable (empty list is fine for empty corpus)
        parsed = json.loads(out)
        self.assertIsInstance(parsed, list)


class CLILiveConfigPropagationTests(unittest.TestCase):
    """Regression tests for plan 34 - the module-level _config cache.

    These tests verify that set_config(...) mid-run actually changes
    handler behavior. Before plan 34, cli._config was bound at module
    import and these would all fail.
    """

    def test_module_level_constants_reflect_live_config(self):
        """cli.SHORTLIST_MIN_CANDIDATES etc. must read live config every time."""
        from ledger.config import LedgerConfig, get_config, reset_config, set_config
        import ledger.cli as cli

        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            try:
                cfg_a = LedgerConfig(
                    ledger_root=Path(tmp1),
                    ledger_notes_dir=Path(tmp1) / "notes",
                    source_notes_dir=Path(tmp1) / "src",
                    shortlist_min_candidates=7,
                    progressive_rationale_top=11,
                )
                set_config(cfg_a)
                self.assertEqual(cli.SHORTLIST_MIN_CANDIDATES, 7)
                self.assertEqual(cli.PROGRESSIVE_RATIONALE_TOP, 11)

                cfg_b = LedgerConfig(
                    ledger_root=Path(tmp2),
                    ledger_notes_dir=Path(tmp2) / "notes",
                    source_notes_dir=Path(tmp2) / "src",
                    shortlist_min_candidates=99,
                    progressive_rationale_top=42,
                )
                set_config(cfg_b)
                # Same module-level name, different result - this proves
                # the constant is not import-time frozen.
                self.assertEqual(cli.SHORTLIST_MIN_CANDIDATES, 99)
                self.assertEqual(cli.PROGRESSIVE_RATIONALE_TOP, 42)
            finally:
                reset_config()

    def test_module_level_unknown_attribute_raises(self):
        """__getattr__ should not silently return None for typos."""
        import ledger.cli as cli
        with self.assertRaises(AttributeError):
            _ = cli.NOT_A_REAL_CONSTANT

    def test_handle_query_command_uses_live_indices_dir(self):
        """handle_query_command must read derived paths (aliases_path) from live config.

        aliases_path is a @property derived from indices_dir, so we set
        ledger_notes_dir to a tempdir and verify the derived path used by
        rank_query matches.
        """
        from ledger.config import LedgerConfig, get_config, reset_config, set_config
        import ledger.cli as cli
        from unittest.mock import patch

        captured = {}

        def fake_rank_query(**kwargs):
            captured.update(kwargs)
            return {"results": [], "timing": None}

        with tempfile.TemporaryDirectory() as tmp:
            try:
                cfg = LedgerConfig(
                    ledger_root=Path(tmp),
                    ledger_notes_dir=Path(tmp) / "notes",
                    source_notes_dir=Path(tmp) / "src",
                )
                set_config(cfg)

                args = SimpleNamespace(
                    text="hello", scope="all", limit=8,
                    retrieval_mode="legacy", embed_backend="local", embed_model=None,
                    view="context", json=True, bundle=False,
                )
                with patch.object(cli, "rank_query", side_effect=fake_rank_query):
                    with patch.object(cli, "query_lib") as mock_lib:
                        mock_lib.query_result_to_json.return_value = {}
                        mock_lib.format_query_results_human.return_value = ""
                        _capture(cli.handle_query_command, args)

                # The aliases_path passed to rank_query must be derived from
                # the test config (resolves under tmp), not the user's real config.
                resolved_tmp = str(Path(tmp).resolve())
                self.assertTrue(
                    str(captured["aliases_path"]).startswith(resolved_tmp),
                    f"aliases_path should be under {resolved_tmp}, got {captured['aliases_path']}",
                )
                self.assertEqual(captured["aliases_path"], get_config().aliases_path)
            finally:
                reset_config()


class CLIInboxCleanupHandlerTests(unittest.TestCase):
    """Coverage for the `ledger inbox cleanup` subcommand (added in 09c6473)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        notes_dir = tmp / "notes"
        for folder in (
            "00_inbox", "01_identity", "02_facts", "08_indices",
        ):
            (notes_dir / folder).mkdir(parents=True)
        (notes_dir / "08_indices" / "timeline.md").write_text("# Timeline\n", encoding="utf-8")
        config = LedgerConfig(
            ledger_root=tmp,
            ledger_notes_dir=notes_dir,
            source_notes_dir=tmp / "source",
        )
        set_config(config)
        self.cfg = config
        self.cli = load_cli()

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_cleanup_dry_run_with_empty_inbox(self):
        args = SimpleNamespace(inbox_command="cleanup", days=14, apply=False)
        _, out, _ = _capture(self.cli.handle_inbox_command, args)
        self.assertIn("Nothing to clean up", out)

    def test_cleanup_dry_run_reports_orphaned_locks(self):
        # Orphan lock - no .md sibling
        (self.cfg.ledger_notes_dir / "00_inbox" / "orphan.md.lock").write_text("", encoding="utf-8")

        args = SimpleNamespace(inbox_command="cleanup", days=14, apply=False)
        _, out, _ = _capture(self.cli.handle_inbox_command, args)
        self.assertIn("Would remove", out)
        self.assertIn("orphan.md.lock", out)
        self.assertIn("--apply", out)
        # Dry run: file still there
        self.assertTrue((self.cfg.ledger_notes_dir / "00_inbox" / "orphan.md.lock").exists())

    def test_cleanup_apply_actually_deletes(self):
        (self.cfg.ledger_notes_dir / "00_inbox" / "orphan.md.lock").write_text("", encoding="utf-8")

        args = SimpleNamespace(inbox_command="cleanup", days=14, apply=True)
        _, out, _ = _capture(self.cli.handle_inbox_command, args)
        self.assertIn("Removed", out)
        self.assertFalse((self.cfg.ledger_notes_dir / "00_inbox" / "orphan.md.lock").exists())

    def test_cleanup_reports_stale_auto_items(self):
        import os
        path = self.cfg.ledger_notes_dir / "00_inbox" / "uncommitted_note_changes.md"
        path.write_text("---\n---\n\n# x\n", encoding="utf-8")
        # Backdate to 30 days ago
        old = time.time() - (30 * 86400)
        os.utime(path, (old, old))

        args = SimpleNamespace(inbox_command="cleanup", days=14, apply=False)
        _, out, _ = _capture(self.cli.handle_inbox_command, args)
        self.assertIn("uncommitted_note_changes.md", out)
        self.assertIn("stale", out)

    def test_cleanup_invalid_subcommand_exits_1(self):
        args = SimpleNamespace(inbox_command="bogus")
        with self.assertRaises(SystemExit) as ctx:
            _capture(self.cli.handle_inbox_command, args)
        self.assertEqual(ctx.exception.code, 1)


class CLIContextBootFormatTests(unittest.TestCase):
    """handle_context_command's boot format - the big block at lines 340-418.

    Now reachable thanks to the live-config refactor (plan 34); previously
    the handler read from the stale module-level cache and tests against
    a temp corpus would silently use the user's real ledger.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        notes_dir = tmp / "notes"
        for folder in (
            "00_inbox", "01_identity", "02_facts", "03_preferences",
            "04_goals", "05_open_loops", "06_concepts", "08_indices",
        ):
            (notes_dir / folder).mkdir(parents=True)
        (notes_dir / "08_indices" / "timeline.md").write_text("# Timeline\n", encoding="utf-8")
        config = LedgerConfig(
            ledger_root=tmp,
            ledger_notes_dir=notes_dir,
            source_notes_dir=tmp / "source",
        )
        set_config(config)
        self.cfg = config
        self.cli = load_cli()

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_boot_format_runs_with_empty_corpus(self):
        args = SimpleNamespace(
            context_command=None, format="boot",
            ledger_notes_dir=None, output=None, output_dir=None,
        )
        _, out, _ = _capture(self.cli.handle_context_command, args)
        # Boot output always contains a header from build_context
        self.assertGreater(len(out), 0)

    def test_boot_format_includes_signals_summary_when_present(self):
        # Write a signals.jsonl file so the signals branch fires
        signals_path = self.cfg.ledger_notes_dir / "08_indices" / "signals.jsonl"
        signals_path.write_text(
            '{"type": "retrieval_hit", "ts": "2026-04-30T00:00:00Z", "note": "fact__x"}\n',
            encoding="utf-8",
        )
        args = SimpleNamespace(
            context_command=None, format="boot",
            ledger_notes_dir=None, output=None, output_dir=None,
        )
        _, out, _ = _capture(self.cli.handle_context_command, args)
        self.assertIn("Signals", out)


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


class CLIRetrievalCaptureTests(unittest.TestCase):
    """Use-time signal capture in the query path, gated on signals_auto_capture."""

    def setUp(self):
        self.cli = load_cli()
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        (tmp / "notes" / "08_indices").mkdir(parents=True)
        self._tmp_path = tmp

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def _use_config(self, *, auto_capture: bool):
        config = LedgerConfig(ledger_root=self._tmp_path)
        config.signals_auto_capture = auto_capture
        set_config(config)
        return config

    def _read_signals(self):
        from ledger import signals
        return signals.read_signals()

    def test_miss_logged_on_empty_results_when_enabled(self):
        self._use_config(auto_capture=True)
        self.cli._capture_retrieval_miss("nonexistent topic", [])
        sigs = self._read_signals()
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["type"], "retrieval_miss")
        self.assertEqual(sigs[0]["query"], "nonexistent topic")

    def test_miss_not_logged_when_disabled(self):
        self._use_config(auto_capture=False)
        self.cli._capture_retrieval_miss("nonexistent topic", [])
        self.assertEqual(self._read_signals(), [])

    def test_miss_logged_when_top_score_below_floor(self):
        config = self._use_config(auto_capture=True)
        results = [{"score": config.signals_miss_score_floor - 0.01, "rel_path": "notes/02_facts/fact__x.md"}]
        self.cli._capture_retrieval_miss("weak match", results)
        self.assertEqual(len(self._read_signals()), 1)

    def test_no_miss_when_strong_result(self):
        config = self._use_config(auto_capture=True)
        results = [{"score": config.signals_miss_score_floor + 0.5, "rel_path": "notes/02_facts/fact__x.md"}]
        self.cli._capture_retrieval_miss("good match", results)
        self.assertEqual(self._read_signals(), [])


if __name__ == "__main__":
    unittest.main()

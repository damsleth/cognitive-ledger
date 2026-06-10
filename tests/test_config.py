"""Tests for ledger.config module."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ledger.config import (
    LedgerConfig,
    get_config,
    reset_config,
    set_config,
)


class TestLedgerConfig(unittest.TestCase):
    """Tests for LedgerConfig."""

    def setUp(self):
        reset_config()
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")

    def tearDown(self):
        reset_config()
        # Clean up env vars
        for key in list(os.environ.keys()):
            if key.startswith("LEDGER_"):
                del os.environ[key]
        # Restore XDG_CONFIG_HOME (tests point it at temp dirs)
        if self._orig_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg

    def _write_user_config(self, base_dir: Path, content: str) -> Path:
        """Write a user config to the canonical XDG location under base_dir."""
        os.environ["XDG_CONFIG_HOME"] = str(base_dir)
        config_path = Path(base_dir) / "ledger" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(content, encoding="utf-8")
        return config_path

    def test_default_values(self):
        """Test default configuration values."""
        config = LedgerConfig()

        self.assertEqual(config.shortlist_min_candidates, 24)
        self.assertEqual(config.shortlist_max_candidates, 36)
        self.assertEqual(config.attention_shortlist_min, 32)
        self.assertEqual(config.attention_shortlist_max, 72)
        self.assertEqual(config.detailed_reasons_limit, 20)
        self.assertEqual(config.progressive_rationale_top, 3)
        self.assertEqual(config.retrieval_mode, "semantic_hybrid")
        self.assertEqual(config.embed_backend, "local")
        self.assertIsNone(config.embed_model)

    def test_score_weights_sum_to_one(self):
        """Test that lexical score weights sum to approximately 1.0."""
        config = LedgerConfig()

        lexical_sum = (
            config.score_weight_bm25
            + config.score_weight_lexical
            + config.score_weight_tag
            + config.score_weight_scope
            + config.score_weight_recency
            + config.score_weight_confidence
        )
        self.assertAlmostEqual(lexical_sum, 1.0, places=2)

    def test_semantic_weights_sum_to_one(self):
        """Test that semantic score weights sum to approximately 1.0."""
        config = LedgerConfig()

        semantic_sum = (
            config.semantic_weight_vector
            + config.semantic_weight_lexical
            + config.semantic_weight_scope
            + config.semantic_weight_recency
        )
        self.assertAlmostEqual(semantic_sum, 1.0, places=2)

    def test_note_type_dir(self):
        """Test note_type_dir method."""
        config = LedgerConfig()

        facts_dir = config.note_type_dir("facts")
        self.assertTrue(str(facts_dir).endswith("notes/02_facts"))

    def test_note_type_dir_invalid(self):
        """Test note_type_dir raises for invalid type."""
        config = LedgerConfig()

        with self.assertRaises(ValueError):
            config.note_type_dir("invalid_type")

    def test_env_override_integer(self):
        """Test environment variable override for integers."""
        os.environ["LEDGER_SHORTLIST_MIN"] = "50"

        config = LedgerConfig.from_env()
        self.assertEqual(config.shortlist_min_candidates, 50)

        del os.environ["LEDGER_SHORTLIST_MIN"]

    def test_env_override_float(self):
        """Test environment variable override for floats."""
        os.environ["LEDGER_WEIGHT_LEXICAL"] = "0.55"

        config = LedgerConfig.from_env()
        self.assertAlmostEqual(config.score_weight_lexical, 0.55)

        del os.environ["LEDGER_WEIGHT_LEXICAL"]

    def test_env_override_bm25_weight(self):
        """Test environment variable override for BM25 weight."""
        os.environ["LEDGER_WEIGHT_BM25"] = "0.45"

        config = LedgerConfig.from_env()
        self.assertAlmostEqual(config.score_weight_bm25, 0.45)

        del os.environ["LEDGER_WEIGHT_BM25"]

    def test_env_override_invalid_ignored(self):
        """Test that invalid env values are ignored."""
        os.environ["LEDGER_SHORTLIST_MIN"] = "not_a_number"

        config = LedgerConfig.from_env()
        # Should fall back to default
        self.assertEqual(config.shortlist_min_candidates, 24)

        del os.environ["LEDGER_SHORTLIST_MIN"]

    def test_path_env_overrides_use_canonical_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ledger"
            notes = Path(tmpdir) / "corpus"
            source = Path(tmpdir) / "source"
            os.environ["LEDGER_ROOT"] = str(root)
            os.environ["LEDGER_NOTES_DIR"] = str(notes)
            os.environ["LEDGER_SOURCE_NOTES_DIR"] = str(source)

            config = LedgerConfig.from_env()

            self.assertEqual(config.ledger_root, root.resolve())
            self.assertEqual(config.ledger_notes_dir, notes.resolve())
            self.assertEqual(config.source_notes_dir, source.resolve())

    def test_removed_env_var_fails_with_migration_error(self):
        os.environ["LEDGER_ROOT_DIR"] = "/tmp/legacy-root"

        with self.assertRaises(RuntimeError) as ctx:
            LedgerConfig.from_env()

        self.assertIn("LEDGER_ROOT", str(ctx.exception))

    def test_yaml_with_canonical_path_keys_loads(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ledger"
            self._write_user_config(
                Path(tmpdir) / "xdg",
                "\n".join(
                    [
                        f"ledger_root: {root}",
                        f"ledger_notes_dir: {root / 'corpus'}",
                        f"source_notes_dir: {root / 'source'}",
                    ]
                )
                + "\n",
            )

            config = LedgerConfig.from_env()

            self.assertEqual(config.ledger_root, root.resolve())
            self.assertEqual(config.ledger_notes_dir, (root / "corpus").resolve())
            self.assertEqual(config.source_notes_dir, (root / "source").resolve())

    def test_yaml_retrieval_defaults_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ledger"
            self._write_user_config(
                Path(tmpdir) / "xdg",
                "\n".join(
                    [
                        f"ledger_root: {root}",
                        "retrieval_mode: precomputed_index",
                        "embed_backend: openai",
                        "embed_model: text-embedding-3-small",
                    ]
                )
                + "\n",
            )

            config = LedgerConfig.from_env()

            self.assertEqual(config.retrieval_mode, "precomputed_index")
            self.assertEqual(config.embed_backend, "openai")
            self.assertEqual(config.embed_model, "text-embedding-3-small")

    def test_retrieval_mode_env_overrides_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ledger"
            self._write_user_config(
                Path(tmpdir) / "xdg",
                f"ledger_root: {root}\nretrieval_mode: precomputed_index\n",
            )
            os.environ["LEDGER_RETRIEVAL_MODE"] = "two_stage"

            config = LedgerConfig.from_env()

            self.assertEqual(config.retrieval_mode, "two_stage")

    def test_removed_yaml_key_fails_with_migration_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_user_config(Path(tmpdir) / "xdg", "root_dir: ~/legacy-ledger\n")

            with self.assertRaises(RuntimeError) as ctx:
                LedgerConfig.from_env()

            self.assertIn("ledger_root", str(ctx.exception))

    def test_missing_yaml_support_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_user_config(Path(tmpdir) / "xdg", "ledger_root: ~/ledger\n")

            real_import = __import__

            def guarded_import(name, *args, **kwargs):
                if name == "yaml":
                    raise ImportError("yaml unavailable")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=guarded_import):
                with self.assertRaises(RuntimeError) as ctx:
                    LedgerConfig.from_env()

            self.assertIn("PyYAML", str(ctx.exception))

    def test_repo_root_config_is_not_read(self):
        # Config must live with the installation, not the codebase: a
        # config.yaml inside ledger_root is intentionally ignored.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "repo"
            root.mkdir()
            (root / "config.yaml").write_text(
                "retrieval_mode: precomputed_index\n", encoding="utf-8"
            )
            os.environ["LEDGER_ROOT"] = str(root)
            # Point XDG at an empty dir so nothing else is picked up.
            os.environ["XDG_CONFIG_HOME"] = str(Path(tmpdir) / "empty-xdg")

            config = LedgerConfig.from_env()

            # The repo config's retrieval_mode is ignored; default stands.
            self.assertEqual(config.retrieval_mode, "semantic_hybrid")

    def test_canonical_xdg_overrides_legacy_xdg(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "xdg"
            os.environ["XDG_CONFIG_HOME"] = str(base)
            legacy = base / "cognitive-ledger" / "config.yaml"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text("retrieval_mode: legacy\n", encoding="utf-8")
            canonical = base / "ledger" / "config.yaml"
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_text("retrieval_mode: two_stage\n", encoding="utf-8")

            config = LedgerConfig.from_env()

            self.assertEqual(config.retrieval_mode, "two_stage")


class TestConfigSingleton(unittest.TestCase):
    """Tests for config singleton functions."""

    def setUp(self):
        reset_config()

    def tearDown(self):
        reset_config()

    def test_get_config_returns_same_instance(self):
        """Test that get_config returns the same instance."""
        config1 = get_config()
        config2 = get_config()

        self.assertIs(config1, config2)


class TestThings3Config(unittest.TestCase):
    """Tests for Things3 sync config keys."""

    def setUp(self):
        reset_config()
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")

    def tearDown(self):
        reset_config()
        for key in list(os.environ.keys()):
            if key.startswith("LEDGER_"):
                del os.environ[key]
        if self._orig_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg

    def _write_user_config(self, base_dir: Path, content: str) -> None:
        os.environ["XDG_CONFIG_HOME"] = str(base_dir)
        config_path = Path(base_dir) / "ledger" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(content, encoding="utf-8")

    def test_defaults(self):
        config = LedgerConfig()
        self.assertFalse(config.things3_sync_enabled)
        self.assertEqual(config.things3_db_path, "")
        self.assertEqual(config.things3_default_project, "")
        self.assertEqual(config.things3_blocked_project, "")
        self.assertEqual(config.things3_scope_routing, {})
        self.assertEqual(config.things3_marker_prefix, "ledger:")
        self.assertEqual(config.things3_completed_maps_to, "closed")
        self.assertEqual(config.things3_canceled_maps_to, "snoozed")
        self.assertEqual(config.things3_orphan_action, "flag")

    def test_scope_routing_yaml_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_user_config(Path(tmpdir), (
                "things3_sync_enabled: true\n"
                "things3_scope_routing:\n"
                "  work: Work Tasks\n"
                "  dev: Dev\n"
            ))
            config = LedgerConfig.from_env()
            self.assertTrue(config.things3_sync_enabled)
            self.assertEqual(config.things3_scope_routing.get("work"), "Work Tasks")
            self.assertEqual(config.things3_scope_routing.get("dev"), "Dev")

    def test_env_overrides(self):
        os.environ["LEDGER_THINGS3_SYNC_ENABLED"] = "true"
        os.environ["LEDGER_THINGS3_DEFAULT_PROJECT"] = "My Project"
        os.environ["LEDGER_THINGS3_ORPHAN_ACTION"] = "cancel"
        os.environ["LEDGER_THINGS3_COMPLETED_MAPS_TO"] = "snoozed"
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["XDG_CONFIG_HOME"] = str(Path(tmpdir) / "empty")
            config = LedgerConfig.from_env()
        self.assertTrue(config.things3_sync_enabled)
        self.assertEqual(config.things3_default_project, "My Project")
        self.assertEqual(config.things3_orphan_action, "cancel")
        self.assertEqual(config.things3_completed_maps_to, "snoozed")

    def test_reset_config_clears_singleton(self):
        """Test that reset_config clears the singleton."""
        config1 = get_config()
        reset_config()
        config2 = get_config()

        self.assertIsNot(config1, config2)

    def test_set_config_injects_custom_config(self):
        """Test that set_config allows injecting config."""
        custom = LedgerConfig()
        custom.shortlist_min_candidates = 100

        set_config(custom)
        config = get_config()

        self.assertEqual(config.shortlist_min_candidates, 100)

    def test_resolve_retrieval_mode_uses_config_default(self):
        from ledger.retrieval import resolve_retrieval_mode

        custom = LedgerConfig()
        custom.retrieval_mode = "precomputed_index"
        set_config(custom)

        self.assertEqual(resolve_retrieval_mode(None), "precomputed_index")


class TestConfigPaths(unittest.TestCase):
    """Tests for config path properties."""

    def test_ledger_notes_dir(self):
        """Test ledger_notes_dir property."""
        config = LedgerConfig()
        self.assertTrue(str(config.ledger_notes_dir).endswith("notes"))

    def test_aliases_path(self):
        """Test aliases_path property."""
        config = LedgerConfig()
        self.assertTrue(str(config.aliases_path).endswith("aliases.json"))

    def test_timeline_path(self):
        """Test timeline_path property."""
        config = LedgerConfig()
        self.assertTrue(str(config.timeline_path).endswith("timeline.md"))

    def test_semantic_root(self):
        """Test semantic_root property."""
        config = LedgerConfig()
        self.assertIn("semantic", str(config.semantic_root))


if __name__ == "__main__":
    unittest.main()

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

    def tearDown(self):
        reset_config()
        # Clean up env vars
        for key in list(os.environ.keys()):
            if key.startswith("LEDGER_"):
                del os.environ[key]

    def test_default_values(self):
        """Test default configuration values."""
        config = LedgerConfig()

        self.assertEqual(config.shortlist_min_candidates, 24)
        self.assertEqual(config.shortlist_max_candidates, 36)
        self.assertEqual(config.attention_shortlist_min, 32)
        self.assertEqual(config.attention_shortlist_max, 72)
        self.assertEqual(config.detailed_reasons_limit, 20)
        self.assertEqual(config.progressive_rationale_top, 3)

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
            root.mkdir()
            config_path = root / "config.yaml"
            config_path.write_text(
                "\n".join(
                    [
                        f"ledger_root: {root}",
                        f"ledger_notes_dir: {root / 'corpus'}",
                        f"source_notes_dir: {root / 'source'}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            os.environ["LEDGER_ROOT"] = str(root)

            config = LedgerConfig.from_env()

            self.assertEqual(config.ledger_root, root.resolve())
            self.assertEqual(config.ledger_notes_dir, (root / "corpus").resolve())
            self.assertEqual(config.source_notes_dir, (root / "source").resolve())

    def test_removed_yaml_key_fails_with_migration_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ledger"
            root.mkdir()
            config_path = root / "config.yaml"
            config_path.write_text("root_dir: ~/legacy-ledger\n", encoding="utf-8")
            os.environ["LEDGER_ROOT"] = str(root)

            with self.assertRaises(RuntimeError) as ctx:
                LedgerConfig.from_env()

            self.assertIn("ledger_root", str(ctx.exception))

    def test_missing_yaml_support_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "ledger"
            root.mkdir()
            config_path = root / "config.yaml"
            config_path.write_text("ledger_root: ~/ledger\n", encoding="utf-8")
            os.environ["LEDGER_ROOT"] = str(root)

            real_import = __import__

            def guarded_import(name, *args, **kwargs):
                if name == "yaml":
                    raise ImportError("yaml unavailable")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=guarded_import):
                with self.assertRaises(RuntimeError) as ctx:
                    LedgerConfig.from_env()

            self.assertIn("PyYAML", str(ctx.exception))


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

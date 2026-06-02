import os
import sys
from pathlib import Path
import tempfile
import types
import unittest

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Keep scripts dir on path as fallback for legacy imports
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def load_embeddings_module():
    try:
        from ledger import embeddings
        return embeddings
    except ImportError:
        import ledger_embeddings
        return ledger_embeddings


@unittest.skipIf(np is None, "numpy is required for ledger_embeddings tests")
class LedgerEmbeddingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.embeddings = load_embeddings_module()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name).resolve()
        self.notes_root = self.repo_root / "notes"
        self.source_root = self.repo_root / "source_notes"

        for rel in [
            "notes/02_facts",
            "notes/03_preferences",
            "notes/04_goals",
            "notes/05_open_loops",
            "notes/06_concepts",
            "notes/08_indices",
            "source_notes",
        ]:
            (self.repo_root / rel).mkdir(parents=True, exist_ok=True)

        self._write_note(
            self.repo_root / "notes/02_facts/fact__alpha.md",
            title="Alpha Fact",
            body="Alpha body",
        )
        self._write_note(
            self.repo_root / "notes/03_preferences/pref__beta.md",
            title="Beta Preference",
            body="Beta body",
        )

        self.call_log = []

        def fake_embed_texts(texts, backend, model, device=None, batch_size=None):
            self.call_log.append((backend, model, len(texts)))
            vectors = np.zeros((len(texts), 3), dtype=np.float32)
            for idx, text in enumerate(texts):
                seed = sum(ord(ch) for ch in text)
                vectors[idx, 0] = float(seed % 101)
                vectors[idx, 1] = float((seed * 3) % 101)
                vectors[idx, 2] = float((seed * 7) % 101)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return vectors / norms

        from ledger.config import LedgerConfig, reset_config, set_config

        test_config = LedgerConfig(
            ledger_root=self.repo_root,
            ledger_notes_dir=self.notes_root,
            source_notes_dir=self.source_root,
        )
        set_config(test_config)

        self._embed_texts_original = self.embeddings.embed_texts
        self.embeddings.embed_texts = fake_embed_texts
        self._reset_config = reset_config

    def tearDown(self):
        self.embeddings.clear_runtime_caches()
        self.embeddings.embed_texts = self._embed_texts_original
        self._reset_config()
        self.temp_dir.cleanup()

    def _write_note(self, path: Path, title: str, body: str, updated: str = "2026-02-13T10:00:00Z"):
        content = (
            "---\n"
            "created: 2026-02-13T09:00:00Z\n"
            f"updated: {updated}\n"
            "tags: [test, semantic]\n"
            "confidence: 0.9\n"
            "source: user\n"
            "scope: dev\n"
            "lang: en\n"
            "---\n\n"
            f"# {title}\n\n"
            f"{body}\n"
        )
        path.write_text(content, encoding="utf-8")

    def test_build_index_writes_expected_schema_and_dims(self):
        payload = self.embeddings.build_indices(
            target="ledger",
            backend="local",
            model="fake-local-model",
            source_root=self.source_root,
            write_manifest=False,
            append_timeline=False,
        )

        result = payload["results"][0]
        self.assertEqual(result["item_count"], 2)
        self.assertEqual(result["dims"], 3)

        index_data, vectors = self.embeddings.load_semantic_index(
            "ledger", "local", "fake-local-model"
        )
        self.assertIsNotNone(index_data)
        self.assertIsNotNone(vectors)
        self.assertEqual(index_data["item_count"], 2)
        self.assertEqual(index_data["dims"], 3)

        for item in index_data["items"]:
            self.assertEqual(set(item.keys()), set(self.embeddings.INDEX_ITEM_FIELDS))

        first_hashes = {
            item["id"]: item["content_hash"]
            for item in self.embeddings.collect_target_items("ledger", source_root=self.source_root)
        }
        second_hashes = {
            item["id"]: item["content_hash"]
            for item in self.embeddings.collect_target_items("ledger", source_root=self.source_root)
        }
        self.assertEqual(first_hashes, second_hashes)

    def test_configured_model_for_backend_resolution(self):
        from ledger.config import LedgerConfig, set_config

        cfb = self.embeddings.configured_model_for_backend
        default_local = self.embeddings.default_model_for_backend("local")

        # Explicit model always wins.
        set_config(LedgerConfig(
            ledger_root=self.repo_root, embed_backend="local", embed_model="BAAI/bge-m3",
        ))
        self.assertEqual(cfb("local", "intfloat/e5"), "intfloat/e5")

        # config.embed_model used when the backend matches config.embed_backend.
        self.assertEqual(cfb("local"), "BAAI/bge-m3")

        # Backend mismatch falls back to the static default (configured model is
        # backend-specific, so don't hand a local model name to openai).
        self.assertEqual(cfb("openai"), self.embeddings.default_model_for_backend("openai"))

        # No configured model -> static default.
        set_config(LedgerConfig(
            ledger_root=self.repo_root, embed_backend="local", embed_model=None,
        ))
        self.assertEqual(cfb("local"), default_local)

    def test_clean_prunes_manifest_entries(self):
        self.embeddings.build_indices(
            target="ledger",
            backend="local",
            model="fake-local-model",
            source_root=self.source_root,
            write_manifest=True,
            append_timeline=False,
        )

        manifest = self.embeddings.load_semantic_manifest()
        self.assertIn("ledger", manifest["targets"])
        target_dir = self.embeddings.get_config().semantic_root / "ledger"
        self.assertTrue(target_dir.exists())

        result = self.embeddings.clean_indices("ledger", append_timeline=False)

        # On-disk vectors removed *and* the manifest no longer points at them.
        self.assertFalse(target_dir.exists())
        self.assertEqual(result["manifest_pruned"], ["ledger"])
        pruned_manifest = self.embeddings.load_semantic_manifest()
        self.assertNotIn("ledger", pruned_manifest["targets"])

    def test_clean_missing_target_is_noop(self):
        # Cleaning a target that was never built should not error or fabricate
        # a manifest entry.
        result = self.embeddings.clean_indices("ledger", append_timeline=False)
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["manifest_pruned"], [])

    def test_incremental_rebuild_only_embeds_changed_items(self):
        first = self.embeddings.build_indices(
            target="ledger",
            backend="local",
            model="fake-local-model",
            source_root=self.source_root,
            write_manifest=False,
            append_timeline=False,
        )
        self.assertEqual(first["results"][0]["embedded_count"], 2)
        self.assertEqual(first["results"][0]["reused_count"], 0)

        second = self.embeddings.build_indices(
            target="ledger",
            backend="local",
            model="fake-local-model",
            source_root=self.source_root,
            write_manifest=False,
            append_timeline=False,
        )
        self.assertEqual(second["results"][0]["embedded_count"], 0)
        self.assertEqual(second["results"][0]["reused_count"], 2)

        self._write_note(
            self.repo_root / "notes/03_preferences/pref__beta.md",
            title="Beta Preference",
            body="Beta body changed",
            updated="2026-02-13T11:00:00Z",
        )
        third = self.embeddings.build_indices(
            target="ledger",
            backend="local",
            model="fake-local-model",
            source_root=self.source_root,
            write_manifest=False,
            append_timeline=False,
        )
        self.assertEqual(third["results"][0]["embedded_count"], 1)
        self.assertEqual(third["results"][0]["reused_count"], 1)

        # First build embeds 2 items, second build embeds 0, third build embeds 1.
        self.assertEqual([entry[2] for entry in self.call_log], [2, 1])

    def test_openai_backend_requires_api_key_without_silent_fallback(self):
        previous_key = os.environ.get("OPENAI_API_KEY")
        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]

        try:
            with self.assertRaises(RuntimeError):
                self.embeddings.build_indices(
                    target="ledger",
                    backend="openai",
                    model=self.embeddings.DEFAULT_OPENAI_MODEL,
                    source_root=self.source_root,
                    write_manifest=False,
                    append_timeline=False,
                )

            with self.assertRaises(RuntimeError):
                self.embeddings.semantic_score_map(
                    query="test",
                    target="ledger",
                    backend="openai",
                    model=self.embeddings.DEFAULT_OPENAI_MODEL,
                )
        finally:
            if previous_key is not None:
                os.environ["OPENAI_API_KEY"] = previous_key

    def test_source_openai_requires_explicit_allow_flag(self):
        with self.assertRaises(RuntimeError):
            self.embeddings.semantic_score_map(
                query="test",
                target="source",
                backend="openai",
                model=self.embeddings.DEFAULT_OPENAI_MODEL,
                allow_api_on_source=False,
            )

    def test_semantic_score_map_reuses_query_vector_for_repeated_query(self):
        self.embeddings.build_indices(
            target="ledger",
            backend="local",
            model="fake-local-model",
            source_root=self.source_root,
            write_manifest=False,
            append_timeline=False,
        )
        self.call_log.clear()
        self.embeddings.clear_runtime_caches()

        first = self.embeddings.semantic_score_map(
            query="alpha",
            target="ledger",
            backend="local",
            model="fake-local-model",
        )
        second = self.embeddings.semantic_score_map(
            query="alpha",
            target="ledger",
            backend="local",
            model="fake-local-model",
        )

        self.assertTrue(first["available"])
        self.assertTrue(second["available"])
        self.assertEqual(self.call_log, [("local", "fake-local-model", 1)])

    def test_local_encoder_cache_reuses_model_instance(self):
        created_models = []
        test_case = self

        class FakeSentenceTransformer:
            def __init__(self, model, **kwargs):
                # Tolerate local_files_only / device kwargs the real call passes.
                created_models.append(model)

            def encode(self, texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False, batch_size=None):
                test_case.assertTrue(convert_to_numpy)
                test_case.assertTrue(normalize_embeddings)
                test_case.assertFalse(show_progress_bar)
                return np.ones((len(texts), 2), dtype=np.float32)

        fake_module = types.ModuleType("sentence_transformers")
        fake_module.SentenceTransformer = FakeSentenceTransformer
        original_module = sys.modules.get("sentence_transformers")
        self.embeddings.clear_runtime_caches()
        sys.modules["sentence_transformers"] = fake_module

        try:
            first = self.embeddings._local_embed_texts(["alpha"], "fake-model")
            second = self.embeddings._local_embed_texts(["beta"], "fake-model")
        finally:
            if original_module is None:
                del sys.modules["sentence_transformers"]
            else:
                sys.modules["sentence_transformers"] = original_module

        self.assertEqual(created_models, ["fake-model"])
        self.assertEqual(first.shape, (1, 2))
        self.assertEqual(second.shape, (1, 2))


if __name__ == "__main__":
    unittest.main()

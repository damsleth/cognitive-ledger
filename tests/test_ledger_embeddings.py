import os
import sys
from pathlib import Path
import tempfile
import unittest

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"

# Ensure ledger package and scripts dir are importable
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def load_embeddings_module():
    """Load ledger_embeddings via normal import (no exec_module hack)."""
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

        def fake_embed_texts(texts, backend, model):
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

        self._patches = {
            "ROOT_DIR": self.repo_root,
            "LEDGER_NOTES_ROOT": self.notes_root,
            "SEMANTIC_ROOT": self.repo_root / ".smart-env" / "semantic",
            "LEDGER_TIMELINE_PATH": self.notes_root / "08_indices" / "timeline.md",
            "SEMANTIC_MANIFEST_PATH": self.notes_root / "08_indices" / "semantic_manifest.json",
            "DEFAULT_SOURCE_ROOT": self.source_root,
            "embed_texts": fake_embed_texts,
        }
        self._originals = {}
        for key, value in self._patches.items():
            self._originals[key] = getattr(self.embeddings, key)
            setattr(self.embeddings, key, value)

    def tearDown(self):
        for key, value in self._originals.items():
            setattr(self.embeddings, key, value)
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


if __name__ == "__main__":
    unittest.main()

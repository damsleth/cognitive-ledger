"""Golden tests for the JSON seams consumed by yaams. DO NOT change casually.

yaams (the sibling repo) shells out to this CLI and parses these payloads:

- ``ledger embed search --json`` and ``ledger embed search --batch`` —
  consumed by ``yaams/promote/dedup.py`` (near-duplicate detection over
  candidate statements; batch mode exists so it stops paying a cold
  SentenceTransformer load per statement).
- ``ledger paths --json`` — consumed by ``yaams/synthesize/summarize.py``
  (resolving the ledger corpus location).

These tests lock the exact key sets and value types of those payloads. If a
change here breaks a test, that change breaks yaams silently in another repo:
either keep the shape, or coordinate a yaams update in the same motion (see
memcore/README.md for the contract policy).
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ledger.config import LedgerConfig, reset_config, set_config
from ledger.semantic import batch_semantic_search_lines, semantic_search_target

# The locked shapes -----------------------------------------------------------

EMBED_SEARCH_PAYLOAD_KEYS = [
    "available",
    "backend",
    "index_built_at",
    "index_item_count",
    "model",
    "reason",
    "results",
    "target",
]

EMBED_SEARCH_RESULT_ITEM_KEYS = [
    "cosine_similarity",
    "lang",
    "rel_path",
    "scope",
    "status",
    "type",
    "updated",
]

PATHS_PAYLOAD_KEYS = [
    "ledger_notes_dir",
    "ledger_root",
    "source_notes_dir",
    "timeline_path",
]

BATCH_ERROR_LINE_KEYS = ["error"]


def _fake_embeddings_module():
    """An embeddings module double with one built index and one hit.

    Returns extra internal keys (abs_path, id, …) on the raw hit; the seam
    must project them away — yaams only ever sees the locked item keys.
    """
    def semantic_search(**kwargs):
        return {
            "available": True,
            "target": kwargs.get("target"),
            "backend": kwargs.get("backend"),
            "model": kwargs.get("model"),
            "reason": None,
            "index_built_at": "2026-08-30T00:00:00Z",
            "index_item_count": 2,
            "results": [
                {
                    "rel_path": "notes/02_facts/fact__deploy.md",
                    "type": "fact",
                    "scope": "dev",
                    "status": "",
                    "lang": "en",
                    "updated": "2026-08-29T10:00:00Z",
                    "cosine_similarity": 0.9321,
                    # Internal keys that must NOT leak through the seam:
                    "abs_path": "/somewhere/notes/02_facts/fact__deploy.md",
                    "id": "row-17",
                    "content_hash": "deadbeef",
                    "embedding_text": "secret internal text",
                }
            ],
        }

    return SimpleNamespace(
        configured_model_for_backend=lambda backend, model: model or "fake-model",
        semantic_search=semantic_search,
    )


def _search_with_fake_index(query, **kwargs):
    module = _fake_embeddings_module()
    return semantic_search_target(
        query,
        load_embeddings_module_fn=lambda: module,
        resolve_embed_model_fn=lambda backend, model, **_: model or "fake-model",
        **kwargs,
    )


class EmbedSearchSingleGolden(unittest.TestCase):
    """(a) The single `ledger embed search --json` payload shape."""

    def test_available_payload_shape(self):
        payload = _search_with_fake_index("deploy window", target="ledger", limit=5)
        self.assertEqual(sorted(payload), EMBED_SEARCH_PAYLOAD_KEYS)
        self.assertIsInstance(payload["available"], bool)
        self.assertIsInstance(payload["backend"], str)
        self.assertIsInstance(payload["index_built_at"], str)
        self.assertIsInstance(payload["index_item_count"], int)
        self.assertIsInstance(payload["model"], str)
        self.assertIsInstance(payload["reason"], str)
        self.assertIsInstance(payload["results"], list)
        self.assertIsInstance(payload["target"], str)

    def test_result_item_shape_and_no_internal_leaks(self):
        payload = _search_with_fake_index("deploy window", target="ledger", limit=5)
        item = payload["results"][0]
        self.assertEqual(sorted(item), EMBED_SEARCH_RESULT_ITEM_KEYS)
        self.assertIsInstance(item["cosine_similarity"], float)
        for key in ("rel_path", "type", "scope", "status", "lang", "updated"):
            self.assertIsInstance(item[key], str)

    def test_unavailable_payload_shape_matches_available(self):
        """The missing-index envelope carries the same keys (results == [])."""
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        set_config(
            LedgerConfig(
                ledger_root=tmp / "repo",
                ledger_notes_dir=tmp / "ledger-notes",
                source_notes_dir=tmp / "notes",
            )
        )
        try:
            import ledger.cli as cli
            args = SimpleNamespace(
                query="deploy window", target="ledger", limit=5,
                embed_backend=None, embed_model=None,
                allow_api_on_source=False, json=True, batch=False,
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                cli.handle_embed_search_command(args)
            payload = json.loads(out.getvalue())
            self.assertEqual(sorted(payload), EMBED_SEARCH_PAYLOAD_KEYS)
            self.assertFalse(payload["available"])
            self.assertEqual(payload["results"], [])
        finally:
            reset_config()
            self._tmp.cleanup()


class EmbedSearchBatchGolden(unittest.TestCase):
    """(b) One `ledger embed search --batch` output line."""

    def test_batch_success_line_matches_single_payload_shape(self):
        lines = ['{"query": "deploy window", "limit": 3}']
        out = list(batch_semantic_search_lines(lines, search_fn=_search_with_fake_index))
        self.assertEqual(len(out), 1)
        self.assertEqual(sorted(out[0]), EMBED_SEARCH_PAYLOAD_KEYS)
        self.assertEqual(sorted(out[0]["results"][0]), EMBED_SEARCH_RESULT_ITEM_KEYS)
        # The line must survive a JSONL round-trip as one line.
        encoded = json.dumps(out[0], ensure_ascii=False)
        self.assertNotIn("\n", encoded)
        self.assertEqual(json.loads(encoded), out[0])

    def test_batch_error_line_shape(self):
        out = list(batch_semantic_search_lines(["not json"], search_fn=_search_with_fake_index))
        self.assertEqual(sorted(out[0]), BATCH_ERROR_LINE_KEYS)
        self.assertIsInstance(out[0]["error"], str)


class PathsGolden(unittest.TestCase):
    """(c) The `ledger paths --json` payload shape."""

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

    def tearDown(self):
        reset_config()
        self._tmp.cleanup()

    def test_paths_json_shape(self):
        import ledger.cli as cli
        args = SimpleNamespace(field=None, json=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.handle_paths_command(args)
        payload = json.loads(out.getvalue())
        self.assertEqual(sorted(payload), PATHS_PAYLOAD_KEYS)
        for key in PATHS_PAYLOAD_KEYS:
            self.assertIsInstance(payload[key], str)
            self.assertTrue(payload[key], f"{key} must be a non-empty path string")

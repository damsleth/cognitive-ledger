from __future__ import annotations

import importlib

from ledger import query
from ledger.config import LedgerConfig, reset_config, set_config
from ledger.retrieval_types import RetrievalResult


def _write(path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _seed_note(path, statement: str) -> None:
    _write(
        path,
        f"""---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: dev
lang: en
---

# Query Note

## Statement

{statement}
""",
    )


def test_rank_query_semantic_hybrid_returns_typed_payload(tmp_path):
    config = LedgerConfig(root_dir=tmp_path)
    set_config(config)
    try:
        note = config.notes_dir / "02_facts" / "fact__semantic.md"
        _seed_note(note, "Ship the release when tests are green")

        class FakeEmbeddings:
            @staticmethod
            def semantic_score_map(*, query, target, backend, model):
                assert query == "release tests"
                assert target == "ledger"
                assert backend == "local"
                assert model == "fake-model"
                return {
                    "available": True,
                    "index_item_count": 1,
                    "score_by_rel_path": {
                        "notes/02_facts/fact__semantic.md": 0.93,
                    },
                }

        result = query.rank_query(
            "release tests",
            scope="dev",
            limit=5,
            retrieval_mode="semantic_hybrid",
            load_embeddings_module=lambda: FakeEmbeddings(),
            resolve_embed_model=lambda _backend, _model: "fake-model",
        )

        assert isinstance(result, RetrievalResult)
        assert result.results
        assert result.results[0].rel_path == "notes/02_facts/fact__semantic.md"
        assert result.results[0].components.semantic_similarity == 0.93
        assert result.timing.total_ms >= 0.0

        payload = query.retrieval_result_to_dict(result)
        assert payload["results"][0]["components"]["semantic_similarity"] == 0.93
    finally:
        reset_config()
        importlib.reload(query)


def test_query_result_to_json_builds_cli_shape(tmp_path):
    config = LedgerConfig(root_dir=tmp_path)
    set_config(config)
    try:
        note = config.notes_dir / "05_open_loops" / "loop__release.md"
        _seed_note(note, "Ship the release checklist")

        result = query.rank_query(
            "release checklist",
            scope="dev",
            limit=5,
            retrieval_mode="legacy",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        payload = query.query_result_to_json(result, include_bundle=True)
        assert payload["query"] == "release checklist"
        assert payload["results"]
        assert payload["results"][0]["path"].endswith(".md")
        assert "bundle" in payload
    finally:
        reset_config()
        importlib.reload(query)

from __future__ import annotations

from ledger import semantic
from ledger.semantic import SourceSearchResult


def test_semantic_search_source_returns_typed_result(tmp_path):
    class FakeEmbeddings:
        @staticmethod
        def semantic_search(**kwargs):
            assert kwargs["query"] == "release"
            assert kwargs["target"] == "source"
            assert kwargs["backend"] == "local"
            assert kwargs["model"] == "fake-model"
            assert kwargs["limit"] == 3
            return {
                "target": "source",
                "backend": "local",
                "model": "fake-model",
                "available": True,
                "results": [
                    {
                        "rel_path": "src/app.py",
                        "abs_path": str(tmp_path / "src" / "app.py"),
                        "type": "py",
                        "scope": "",
                        "status": "",
                        "lang": "en",
                        "updated": "2026-02-01T00:00:00Z",
                        "cosine_similarity": 0.91234,
                    }
                ],
            }

    result = semantic.semantic_search_source(
        "release",
        source_root=tmp_path,
        limit=3,
        embed_backend="local",
        embed_model=None,
        load_embeddings_module_fn=lambda: FakeEmbeddings(),
        resolve_embed_model_fn=lambda _backend, _model, **_kwargs: "fake-model",
    )

    assert isinstance(result, SourceSearchResult)
    assert result.available
    assert result.results[0].rel_path == "src/app.py"

    payload = semantic.source_search_result_to_dict(result)
    assert payload["results"][0]["cosine_similarity"] == 0.91234
    assert "mode: source_only" in semantic.format_source_search_human(result)


def test_semantic_index_helpers_delegate_and_format(tmp_path):
    class FakeEmbeddings:
        @staticmethod
        def default_model_for_backend(_backend):
            return "fake-model"

        @staticmethod
        def build_indices(**kwargs):
            assert kwargs["target"] == "ledger"
            assert kwargs["backend"] == "local"
            assert kwargs["model"] == "fake-model"
            return {
                "target": "ledger",
                "backend": "local",
                "model": "fake-model",
                "results": [
                    {
                        "target": "ledger",
                        "item_count": 12,
                        "dims": 384,
                        "embedded_count": 4,
                        "reused_count": 8,
                        "removed_count": 0,
                    }
                ],
            }

        @staticmethod
        def index_status(**kwargs):
            assert kwargs["target"] == "ledger"
            return {
                "target": "ledger",
                "targets": {
                    "ledger": [
                        {
                            "backend": "local",
                            "model": "fake-model",
                            "item_count": 12,
                            "dims": 384,
                            "built_at": "2026-02-01T00:00:00Z",
                        }
                    ]
                },
            }

        @staticmethod
        def clean_indices(**kwargs):
            assert kwargs["target"] == "ledger"
            return {"target": "ledger", "removed": [str(tmp_path / "idx.json")]}

    load_fn = lambda: FakeEmbeddings()
    resolve_fn = lambda _backend, _model, **_kwargs: "fake-model"

    build_payload = semantic.build_semantic_index(
        target="ledger",
        backend="local",
        model=None,
        source_root=tmp_path,
        load_embeddings_module_fn=load_fn,
        resolve_embed_model_fn=resolve_fn,
    )
    assert "items=12" in semantic.format_embed_build_human(build_payload)

    status_payload = semantic.semantic_index_status(
        target="ledger",
        load_embeddings_module_fn=load_fn,
    )
    assert "ledger:" in semantic.format_embed_status_human(status_payload)

    clean_payload = semantic.clean_semantic_indices(
        target="ledger",
        load_embeddings_module_fn=load_fn,
    )
    assert "removed" in semantic.format_embed_clean_human(clean_payload)


def test_semantic_search_target_projects_contract_fields():
    """semantic_search_target drops internal fields, keeps contract fields."""

    class FakeEmbeddings:
        @staticmethod
        def configured_model_for_backend(backend, model=None):
            return "fake-model"

        @staticmethod
        def semantic_search(**kwargs):
            return {
                "target": "ledger",
                "backend": "local",
                "model": "fake-model",
                "available": True,
                "reason": "",
                "index_built_at": "2026-06-09T10:00:00Z",
                "index_item_count": 2,
                "results": [
                    {
                        "rel_path": "02_facts/fact__alpha.md",
                        "abs_path": "/some/abs/path.md",
                        "type": "fact",
                        "scope": "work",
                        "status": "active",
                        "lang": "en",
                        "updated": "2026-05-12T00:00:00Z",
                        "cosine_similarity": 0.94,
                        "id": "abc123",
                        "content_hash": "deadbeef",
                        "row": 0,
                        "embedding_text": "some text",
                        "score_by_id": {},
                        "score_by_rel_path": {},
                    }
                ],
                "score_by_id": {"abc123": 0.94},
                "score_by_rel_path": {"02_facts/fact__alpha.md": 0.94},
            }

    payload = semantic.semantic_search_target(
        "alpha test",
        target="ledger",
        limit=3,
        embed_backend="local",
        embed_model=None,
        load_embeddings_module_fn=lambda: FakeEmbeddings(),
        resolve_embed_model_fn=lambda _backend, _model, **_kwargs: "fake-model",
    )

    assert payload["available"] is True
    assert payload["target"] == "ledger"
    assert payload["backend"] == "local"
    assert payload["model"] == "fake-model"
    assert payload["index_built_at"] == "2026-06-09T10:00:00Z"
    assert payload["index_item_count"] == 2
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["rel_path"] == "02_facts/fact__alpha.md"
    assert result["cosine_similarity"] == 0.94
    # Internal fields must be stripped
    assert "abs_path" not in result
    assert "id" not in result
    assert "content_hash" not in result
    assert "row" not in result
    assert "embedding_text" not in result
    assert "score_by_id" not in result
    # No title field
    assert "title" not in result
    # Top-level internal fields stripped
    assert "score_by_id" not in payload
    assert "score_by_rel_path" not in payload


def test_semantic_search_target_unavailable_passthrough():
    """semantic_search_target surfaces available:false with reason and empty results."""

    class FakeEmbeddings:
        @staticmethod
        def configured_model_for_backend(backend, model=None):
            return "fake-model"

        @staticmethod
        def semantic_search(**kwargs):
            return {
                "target": "ledger",
                "backend": "local",
                "model": "fake-model",
                "available": False,
                "reason": "missing_index",
                "index_built_at": "",
                "index_item_count": 0,
                "results": [],
                "score_by_id": {},
                "score_by_rel_path": {},
            }

    payload = semantic.semantic_search_target(
        "alpha test",
        target="ledger",
        limit=3,
        embed_backend="local",
        embed_model=None,
        load_embeddings_module_fn=lambda: FakeEmbeddings(),
        resolve_embed_model_fn=lambda _backend, _model, **_kwargs: "fake-model",
    )

    assert payload["available"] is False
    assert payload["reason"] == "missing_index"
    assert payload["results"] == []
    assert payload["index_built_at"] == ""


def test_format_embed_search_human():
    """format_embed_search_human formats both available and unavailable payloads."""
    available_payload = {
        "target": "ledger",
        "backend": "local",
        "model": "BAAI/bge-m3",
        "available": True,
        "reason": "",
        "index_built_at": "2026-06-09T10:00:00Z",
        "index_item_count": 5,
        "results": [
            {
                "rel_path": "02_facts/fact__alpha.md",
                "scope": "work",
                "cosine_similarity": 0.942,
            }
        ],
    }
    output = semantic.format_embed_search_human(available_payload)
    assert "target: ledger" in output
    assert "backend: local" in output
    assert "results: 1" in output
    assert "0.942" in output
    assert "02_facts/fact__alpha.md" in output

    unavailable_payload = {
        "target": "ledger",
        "backend": "local",
        "model": "BAAI/bge-m3",
        "available": False,
        "reason": "missing_index",
        "index_built_at": "",
        "index_item_count": 0,
        "results": [],
    }
    output_unavail = semantic.format_embed_search_human(unavailable_payload)
    assert "available: no" in output_unavail
    assert "missing_index" in output_unavail

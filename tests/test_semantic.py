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

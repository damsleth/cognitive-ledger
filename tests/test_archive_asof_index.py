"""Superseded notes are embedded for --as-of reads only (plan 06b)."""

from __future__ import annotations

import numpy as np

from ledger import embeddings
from ledger.layout import typed_archive_notes


def test_typed_archive_notes_skip_retired_inbox_capture(tmp_path):
    archive = tmp_path / "09_archive"
    archive.mkdir()
    for name in ("fact__home_city_2023.md", "pref__old.md", "note__ingest_summary_2026.md"):
        (archive / name).write_text("x", encoding="utf-8")

    names = [p.name for p in typed_archive_notes(tmp_path)]

    assert names == ["fact__home_city_2023.md", "pref__old.md"]


def test_archive_vectors_hidden_unless_requested(monkeypatch):
    """embed search feeds YAAMS promotion dedup: an updated fact must not
    dedup against its own archived predecessor."""
    index = {
        "items": [
            {"id": "a", "rel_path": "notes/02_facts/fact__home_city.md"},
            {"id": "b", "rel_path": "notes/09_archive/fact__home_city_2023.md"},
        ],
        "text_template": "none",
    }
    monkeypatch.setattr(embeddings, "load_semantic_index", lambda *a: (index, np.eye(2, dtype=np.float32)))
    monkeypatch.setattr(embeddings, "embed_query_text", lambda *a, **k: np.ones((1, 2), dtype=np.float32))
    monkeypatch.setattr(embeddings, "configured_model_for_backend", lambda backend, model=None: "m")

    default = embeddings.semantic_score_map("q", target="ledger", backend="local")
    as_of = embeddings.semantic_score_map("q", target="ledger", backend="local", include_archive=True)

    assert list(default["score_by_rel_path"]) == ["notes/02_facts/fact__home_city.md"]
    assert "notes/09_archive/fact__home_city_2023.md" in as_of["score_by_rel_path"]

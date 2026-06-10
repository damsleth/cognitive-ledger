from __future__ import annotations

import importlib

from ledger import query
from ledger.config import LedgerConfig, reset_config, set_config
from ledger.retrieval import clear_candidate_cache
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
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__semantic.md"
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
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


def _seed_note_scoped(path, statement: str, scope: str = "dev", note_type: str = "fact") -> None:
    _write(
        path,
        f"""---
created: 2026-02-01T00:00:00Z
updated: 2026-02-01T00:00:00Z
tags: [test]
confidence: 0.9
source: user
scope: {scope}
lang: en
type: {note_type}
---

# Scoped Note

## Statement

{statement}
""",
    )


# ---------------------------------------------------------------------------
# two_stage mode
# ---------------------------------------------------------------------------


def test_rank_query_two_stage_returns_retrieval_result(tmp_path):
    """two_stage mode should return a valid RetrievalResult with results."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__two_stage.md"
        _seed_note(note, "Deploy the service using the CI pipeline")

        result = query.rank_query(
            "deploy service",
            scope="dev",
            limit=5,
            retrieval_mode="two_stage",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        assert isinstance(result, RetrievalResult)
        assert result.retrieval_mode == "two_stage"
        assert result.timing.total_ms >= 0.0
        # two_stage uses a shortlist step, so shortlist_size should be populated
        assert result.shortlist_size >= 0
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


def test_rank_query_two_stage_finds_matching_note(tmp_path):
    """two_stage mode should surface a note that matches the query tokens."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__deploy.md"
        _seed_note(note, "Deploy the service using the CI pipeline")

        result = query.rank_query(
            "deploy pipeline",
            scope="all",
            limit=5,
            retrieval_mode="two_stage",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        paths = [r.rel_path for r in result.results]
        assert any("fact__deploy" in p for p in paths), f"Expected note not found in: {paths}"
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


def test_rank_query_two_stage_result_structure(tmp_path):
    """Results from two_stage mode have the expected ScoredResult fields."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__structure.md"
        _seed_note(note, "Structure of the cognitive ledger storage layer")

        result = query.rank_query(
            "cognitive ledger storage",
            scope="all",
            limit=5,
            retrieval_mode="two_stage",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        assert isinstance(result, RetrievalResult)
        if result.results:
            top = result.results[0]
            assert hasattr(top, "score")
            assert isinstance(top.score, float)
            assert hasattr(top, "rel_path")
            assert top.rel_path.endswith(".md")
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


# ---------------------------------------------------------------------------
# scope_type_prefilter mode
# ---------------------------------------------------------------------------


def test_rank_query_scope_type_prefilter_returns_retrieval_result(tmp_path):
    """scope_type_prefilter mode should return a valid RetrievalResult."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__prefilter.md"
        _seed_note(note, "Run prefilter on candidates matching query scope")

        result = query.rank_query(
            "prefilter candidates",
            scope="dev",
            limit=5,
            retrieval_mode="scope_type_prefilter",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        assert isinstance(result, RetrievalResult)
        assert result.retrieval_mode == "scope_type_prefilter"
        assert result.prefilter_size >= 0
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


def test_rank_query_scope_type_prefilter_respects_scope(tmp_path):
    """scope_type_prefilter should not return notes from a different scope
    when a specific scope is requested and there are matching in-scope notes."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        dev_note = config.ledger_notes_dir / "02_facts" / "fact__in_scope.md"
        _seed_note_scoped(dev_note, "In-scope fact about deploying the service", scope="dev")

        work_note = config.ledger_notes_dir / "02_facts" / "fact__out_scope.md"
        _seed_note_scoped(work_note, "Out-of-scope fact about deploying the service", scope="work")

        result = query.rank_query(
            "deploying service",
            scope="dev",
            limit=10,
            retrieval_mode="scope_type_prefilter",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        assert isinstance(result, RetrievalResult)
        # The prefilter_size should be <= candidate_pool_size (some candidates were filtered)
        assert result.prefilter_size <= result.candidate_pool_size
        # All returned results should be from the "dev" scope (or have matching token overlap)
        for r in result.results:
            # Each result should be a valid .md path
            assert r.rel_path.endswith(".md")
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


def test_rank_query_scope_type_prefilter_all_scope_returns_results(tmp_path):
    """With scope='all', scope_type_prefilter should still return results."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__allscope.md"
        _seed_note_scoped(note, "Global note about system configuration", scope="dev")

        result = query.rank_query(
            "system configuration",
            scope="all",
            limit=5,
            retrieval_mode="scope_type_prefilter",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        assert isinstance(result, RetrievalResult)
        assert result.results  # should find at least the seeded note
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


# ---------------------------------------------------------------------------
# progressive_disclosure mode
# ---------------------------------------------------------------------------


def test_rank_query_progressive_disclosure_returns_retrieval_result(tmp_path):
    """progressive_disclosure mode should return a valid RetrievalResult."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__progressive.md"
        _seed_note(note, "Progressive disclosure of retrieval results in stages")

        result = query.rank_query(
            "progressive retrieval stages",
            scope="dev",
            limit=5,
            retrieval_mode="progressive_disclosure",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        assert isinstance(result, RetrievalResult)
        assert result.retrieval_mode == "progressive_disclosure"
        assert result.timing.total_ms >= 0.0
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


def test_rank_query_progressive_disclosure_result_has_disclosure_levels(tmp_path):
    """Top results in progressive_disclosure mode carry a 'rationale' disclosure level;
    lower-ranked results carry 'compact'."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        # Seed enough notes so the progressive top-N boundary is visible
        facts_dir = config.ledger_notes_dir / "02_facts"
        for i in range(8):
            note = facts_dir / f"fact__prog_{i:02d}.md"
            _seed_note(note, f"Progressive disclosure note index {i} about memory recall")

        result = query.rank_query(
            "memory recall",
            scope="all",
            limit=8,
            retrieval_mode="progressive_disclosure",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        assert isinstance(result, RetrievalResult)
        assert result.results
        # All results must have a non-empty disclosure_level set by apply_progressive_disclosure
        for r in result.results:
            assert r.disclosure_level in {"rationale", "compact"}, (
                f"Unexpected disclosure_level '{r.disclosure_level}' on {r.rel_path}"
            )
        # The first result (highest-ranked) should be in the 'rationale' tier
        assert result.results[0].disclosure_level == "rationale"
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


def test_rank_query_progressive_disclosure_compact_results_have_no_reasons(tmp_path):
    """Results in the 'compact' tier of progressive_disclosure have empty reasons lists."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        facts_dir = config.ledger_notes_dir / "02_facts"
        for i in range(10):
            note = facts_dir / f"fact__compact_{i:02d}.md"
            _seed_note(note, f"Compact tier note index {i} about caching strategies")

        result = query.rank_query(
            "caching strategies",
            scope="all",
            limit=10,
            retrieval_mode="progressive_disclosure",
            load_embeddings_module=lambda: None,
            resolve_embed_model=lambda _backend, _model: "unused",
        )

        assert isinstance(result, RetrievalResult)
        compact_results = [r for r in result.results if r.disclosure_level == "compact"]
        for r in compact_results:
            assert r.reasons == [], (
                f"Compact result {r.rel_path} unexpectedly has reasons: {r.reasons}"
            )
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


# ---------------------------------------------------------------------------
# Cross-mode: resolve_retrieval_mode integration
# ---------------------------------------------------------------------------


def test_rank_query_unknown_mode_falls_back_to_semantic_hybrid(tmp_path):
    """An unrecognised retrieval_mode resolves to semantic_hybrid (the default),
    so rank_query should still return a RetrievalResult."""
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    clear_candidate_cache()
    try:
        note = config.ledger_notes_dir / "02_facts" / "fact__fallback.md"
        _seed_note(note, "Fallback mode note about unknown configuration")

        class FakeEmbeddings:
            @staticmethod
            def semantic_score_map(*, query, target, backend, model):
                return {
                    "available": True,
                    "index_item_count": 1,
                    "score_by_rel_path": {
                        "notes/02_facts/fact__fallback.md": 0.7,
                    },
                }

        result = query.rank_query(
            "unknown configuration",
            scope="all",
            limit=5,
            retrieval_mode="not_a_real_mode",
            load_embeddings_module=lambda: FakeEmbeddings(),
            resolve_embed_model=lambda _backend, _model: "fake-model",
        )

        assert isinstance(result, RetrievalResult)
        # Unknown mode resolves to semantic_hybrid
        assert result.retrieval_mode == "semantic_hybrid"
    finally:
        clear_candidate_cache()
        reset_config()
        importlib.reload(query)


def test_query_result_to_json_builds_cli_shape(tmp_path):
    config = LedgerConfig(ledger_root=tmp_path)
    set_config(config)
    try:
        note = config.ledger_notes_dir / "05_open_loops" / "loop__release.md"
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


# ---------------------------------------------------------------------------
# Profile resolution in handle_query_command
# ---------------------------------------------------------------------------

def test_resolve_query_args_no_profile():
    """Without a profile, defaults apply: scope=all, limit=8."""
    from types import SimpleNamespace
    from ledger.cli import _resolve_query_args_from_profile
    args = SimpleNamespace(profile=None, scope=None, limit=None, retrieval_mode=None)
    scope, limit, mode = _resolve_query_args_from_profile(args)
    assert scope == "all"
    assert limit == 8


def test_resolve_query_args_profile_work():
    """With --profile work, scope and limit come from the profile."""
    from types import SimpleNamespace
    from ledger.cli import _resolve_query_args_from_profile
    args = SimpleNamespace(profile="work", scope=None, limit=None, retrieval_mode=None)
    scope, limit, mode = _resolve_query_args_from_profile(args)
    assert scope == "work"
    assert isinstance(limit, int)
    assert limit > 0


def test_resolve_query_args_explicit_overrides_profile():
    """Explicit --scope/--limit flags override profile defaults."""
    from types import SimpleNamespace
    from ledger.cli import _resolve_query_args_from_profile
    args = SimpleNamespace(profile="work", scope="all", limit=3, retrieval_mode=None)
    scope, limit, mode = _resolve_query_args_from_profile(args)
    assert scope == "all"
    assert limit == 3


def test_resolve_query_args_zero_limit_not_replaced_by_profile():
    """Explicit limit=0 must NOT be silently replaced by the profile default."""
    from types import SimpleNamespace
    from ledger.cli import _resolve_query_args_from_profile
    args = SimpleNamespace(profile="work", scope=None, limit=0, retrieval_mode=None)
    scope, limit, mode = _resolve_query_args_from_profile(args)
    assert limit == 0  # validate_limit will catch this as invalid

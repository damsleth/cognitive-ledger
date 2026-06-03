"""Tests for ledger.nli — NLI wrapper for contradiction detection.

All tests use a FakeNliPipeline injected via _pipeline_fn so that no model
download or transformers import is required.  The injection seam mirrors the
FakeSentenceTransformer / FakeEmbeddings pattern used in test_semantic.py and
test_ledger_embeddings.py.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any
from unittest.mock import patch

import pytest

from ledger.nli import (
    contradiction_score,
    get_nli_pipeline,
    reset_nli_pipeline_cache,
    score_pair,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pipeline(
    contradiction: float = 0.1,
    neutral: float = 0.8,
    entailment: float = 0.1,
):
    """Return a fake pipeline callable that always yields fixed scores."""

    def _fake_pipeline(premise: str, hypothesis: str) -> list[dict[str, Any]]:
        return [
            {"label": "CONTRADICTION", "score": contradiction},
            {"label": "NEUTRAL", "score": neutral},
            {"label": "ENTAILMENT", "score": entailment},
        ]

    return _fake_pipeline


# ---------------------------------------------------------------------------
# score_pair
# ---------------------------------------------------------------------------

class TestScorePair:
    def test_returns_three_keys(self):
        pipe = _make_pipeline(contradiction=0.7, neutral=0.2, entailment=0.1)
        result = score_pair("A is true", "A is false", _pipeline_fn=pipe)
        assert set(result.keys()) == {"contradiction", "neutral", "entailment"}

    def test_scores_match_fake_output(self):
        pipe = _make_pipeline(contradiction=0.85, neutral=0.10, entailment=0.05)
        result = score_pair("X", "not X", _pipeline_fn=pipe)
        assert result["contradiction"] == pytest.approx(0.85)
        assert result["neutral"] == pytest.approx(0.10)
        assert result["entailment"] == pytest.approx(0.05)

    def test_labels_normalised_to_lowercase(self):
        # Pipeline may return mixed-case labels; score_pair must normalise.
        def _mixed_case_pipe(p: str, h: str) -> list[dict[str, Any]]:
            return [
                {"label": "Contradiction", "score": 0.6},
                {"label": "NEUTRAL",       "score": 0.3},
                {"label": "entailment",    "score": 0.1},
            ]

        result = score_pair("A", "B", _pipeline_fn=_mixed_case_pipe)
        assert "contradiction" in result
        assert result["contradiction"] == pytest.approx(0.6)

    def test_missing_label_defaults_to_zero(self):
        # If the pipeline omits a label, score_pair fills it with 0.0.
        def _partial_pipe(p: str, h: str) -> list[dict[str, Any]]:
            return [{"label": "CONTRADICTION", "score": 0.9}]

        result = score_pair("A", "B", _pipeline_fn=_partial_pipe)
        assert result["entailment"] == pytest.approx(0.0)
        assert result["neutral"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# contradiction_score (bidirectional)
# ---------------------------------------------------------------------------

class TestContradictionScore:
    def test_returns_max_of_both_directions(self):
        """The bidirectional score is max(AB, BA)."""
        call_log: list[tuple[str, str]] = []

        # Direction A→B: contradiction=0.5; direction B→A: contradiction=0.9
        def _asymmetric_pipe(premise: str, hypothesis: str) -> list[dict[str, Any]]:
            call_log.append((premise, hypothesis))
            if premise == "alpha" and hypothesis == "beta":
                return [
                    {"label": "CONTRADICTION", "score": 0.5},
                    {"label": "NEUTRAL",       "score": 0.4},
                    {"label": "ENTAILMENT",    "score": 0.1},
                ]
            # direction B→A
            return [
                {"label": "CONTRADICTION", "score": 0.9},
                {"label": "NEUTRAL",       "score": 0.07},
                {"label": "ENTAILMENT",    "score": 0.03},
            ]

        score = contradiction_score("alpha", "beta", _pipeline_fn=_asymmetric_pipe)
        assert score == pytest.approx(0.9)
        # Both directions must have been evaluated.
        assert len(call_log) == 2

    def test_symmetric_pair_returns_that_score(self):
        pipe = _make_pipeline(contradiction=0.75)
        score = contradiction_score("A", "B", _pipeline_fn=pipe)
        assert score == pytest.approx(0.75)

    def test_low_contradiction_stays_low(self):
        pipe = _make_pipeline(contradiction=0.05, neutral=0.90, entailment=0.05)
        score = contradiction_score("sky is blue", "sky is blue", _pipeline_fn=pipe)
        assert score < 0.1

    def test_both_directions_called(self):
        """Confirm the pipeline is invoked exactly twice (once per direction)."""
        calls: list[tuple[str, str]] = []

        def _counting_pipe(p: str, h: str) -> list[dict[str, Any]]:
            calls.append((p, h))
            return [
                {"label": "CONTRADICTION", "score": 0.2},
                {"label": "NEUTRAL",       "score": 0.7},
                {"label": "ENTAILMENT",    "score": 0.1},
            ]

        contradiction_score("text_a", "text_b", _pipeline_fn=_counting_pipe)
        assert len(calls) == 2
        # First call: A→B
        assert calls[0] == ("text_a", "text_b")
        # Second call: B→A
        assert calls[1] == ("text_b", "text_a")


# ---------------------------------------------------------------------------
# Lazy singleton loader
# ---------------------------------------------------------------------------

class TestGetNliPipelineSingleton:
    def setup_method(self):
        reset_nli_pipeline_cache()

    def teardown_method(self):
        reset_nli_pipeline_cache()

    def test_singleton_returns_same_object(self, monkeypatch):
        """get_nli_pipeline called twice with same args returns the same object."""
        fake_pipe = object()

        def _fake_pipeline_factory(*args, **kwargs):
            return fake_pipe

        monkeypatch.setattr("ledger.nli.get_nli_pipeline.__module__", "ledger.nli")

        with patch("ledger.nli.get_nli_pipeline", wraps=get_nli_pipeline):
            # Inject a fake 'transformers.pipeline' so real model is not loaded.
            import types

            fake_transformers = types.ModuleType("transformers")
            fake_transformers.pipeline = lambda *a, **kw: fake_pipe  # type: ignore[attr-defined]

            fake_torch = types.ModuleType("torch")

            with patch.dict(sys.modules, {"transformers": fake_transformers, "torch": fake_torch}):
                obj1 = get_nli_pipeline("fake-model", device="cpu")
                obj2 = get_nli_pipeline("fake-model", device="cpu")

        assert obj1 is obj2

    def test_different_cache_keys_load_separately(self, monkeypatch):
        """Different model/device combinations are cached independently."""
        import types

        call_count = {"n": 0}

        def _counting_factory(*args, **kwargs):
            call_count["n"] += 1
            return object()

        fake_transformers = types.ModuleType("transformers")
        fake_transformers.pipeline = _counting_factory  # type: ignore[attr-defined]
        fake_torch = types.ModuleType("torch")

        with patch.dict(sys.modules, {"transformers": fake_transformers, "torch": fake_torch}):
            get_nli_pipeline("model-a", device="cpu")
            get_nli_pipeline("model-b", device="cpu")  # different model
            get_nli_pipeline("model-a", device="cpu")  # cache hit — should NOT call factory again

        assert call_count["n"] == 2  # two distinct keys, one cache hit


# ---------------------------------------------------------------------------
# Clean "feature unavailable" when deps missing
# ---------------------------------------------------------------------------

class TestFeatureUnavailableWithoutDeps:
    def setup_method(self):
        reset_nli_pipeline_cache()

    def teardown_method(self):
        reset_nli_pipeline_cache()

    def test_missing_transformers_raises_runtime_error(self):
        """When transformers is not importable, RuntimeError with helpful message."""
        with patch.dict(sys.modules, {"transformers": None}):  # type: ignore[dict-item]
            with pytest.raises(RuntimeError, match="transformers"):
                get_nli_pipeline("some/model")

    def test_missing_torch_raises_runtime_error(self):
        """When torch is not importable, RuntimeError with helpful message."""
        import types

        fake_transformers = types.ModuleType("transformers")
        fake_transformers.pipeline = lambda *a, **kw: None  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"transformers": fake_transformers, "torch": None}):  # type: ignore[dict-item]
            with pytest.raises(RuntimeError, match="torch"):
                get_nli_pipeline("some/model")


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

class TestContradictionConfigDefaults:
    def test_defaults_are_off_by_default(self):
        from ledger.config import LedgerConfig

        cfg = LedgerConfig()
        assert cfg.contradiction_enabled is False

    def test_default_model_name(self):
        from ledger.config import LedgerConfig

        cfg = LedgerConfig()
        assert "mDeBERTa" in cfg.contradiction_model or "mnli" in cfg.contradiction_model.lower()

    def test_default_neighbors_k(self):
        from ledger.config import LedgerConfig

        cfg = LedgerConfig()
        assert cfg.contradiction_neighbors_k == 8

    def test_default_auto_threshold(self):
        from ledger.config import LedgerConfig

        cfg = LedgerConfig()
        assert cfg.contradiction_auto_threshold == pytest.approx(0.85)

    def test_default_review_threshold(self):
        from ledger.config import LedgerConfig

        cfg = LedgerConfig()
        assert cfg.contradiction_review_threshold == pytest.approx(0.60)

    def test_default_lang_no_threshold_stricter(self):
        from ledger.config import LedgerConfig

        cfg = LedgerConfig()
        # Norwegian threshold must be strictly higher than English auto threshold.
        assert cfg.contradiction_auto_threshold_lang_no > cfg.contradiction_auto_threshold

    def test_default_protect_higher_confidence(self):
        from ledger.config import LedgerConfig

        cfg = LedgerConfig()
        assert cfg.contradiction_protect_higher_confidence is True

    def test_env_override_contradiction_enabled(self, monkeypatch):
        import os
        from ledger.config import LedgerConfig, reset_config
        from ledger.config import _apply_env_overrides

        monkeypatch.setenv("LEDGER_CONTRADICTION_ENABLED", "true")
        cfg = LedgerConfig()
        _apply_env_overrides(cfg)
        assert cfg.contradiction_enabled is True
        reset_config()

    def test_env_override_auto_threshold(self, monkeypatch):
        from ledger.config import LedgerConfig, reset_config
        from ledger.config import _apply_env_overrides

        monkeypatch.setenv("LEDGER_CONTRADICTION_AUTO_THRESHOLD", "0.92")
        cfg = LedgerConfig()
        _apply_env_overrides(cfg)
        assert cfg.contradiction_auto_threshold == pytest.approx(0.92)
        reset_config()

    def test_env_override_neighbors_k(self, monkeypatch):
        from ledger.config import LedgerConfig, reset_config
        from ledger.config import _apply_env_overrides

        monkeypatch.setenv("LEDGER_CONTRADICTION_NEIGHBORS_K", "12")
        cfg = LedgerConfig()
        _apply_env_overrides(cfg)
        assert cfg.contradiction_neighbors_k == 12
        reset_config()

    def test_env_override_model(self, monkeypatch):
        from ledger.config import LedgerConfig, reset_config
        from ledger.config import _apply_env_overrides

        monkeypatch.setenv("LEDGER_CONTRADICTION_MODEL", "some/other-model")
        cfg = LedgerConfig()
        _apply_env_overrides(cfg)
        assert cfg.contradiction_model == "some/other-model"
        reset_config()

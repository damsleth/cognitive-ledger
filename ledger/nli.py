"""Natural Language Inference (NLI) wrapper for contradiction detection.

Uses a 3-way (entailment / neutral / contradiction) transformer classifier to
score hypothesis pairs extracted from ledger notes.  The model is lazy-loaded
once per process and cached globally — the same singleton pattern used by
ledger/rerank.py for the cross-encoder.

NORWEGIAN CAVEAT
----------------
The default model (MoritzLaurer/mDeBERTa-v3-base-mnli-xnli) is trained on
MNLI + XNLI data covering 15 languages.  Norwegian (Bokmål or Nynorsk) is
*not* one of the XNLI languages.  Empirical accuracy on Norwegian-language
notes is therefore unvalidated.  The config key
``contradiction_auto_threshold_lang_no`` (default 0.95) exists to apply a
stricter gate before any auto-supersession fires on Norwegian / mixed-language
content.  If your corpus is predominantly Norwegian, treat NLI scores as noisy
signals and rely on the review path rather than auto-supersession.

INJECTION SEAM
--------------
All entry points accept an optional ``_pipeline_fn`` keyword argument.  In
production this defaults to ``_load_nli_pipeline`` (the real model loader).
Tests inject a ``FakeNliPipeline`` callable that returns canned scores without
touching the filesystem or requiring a model download::

    from ledger.nli import contradiction_score

    def fake_pipeline(premise, hypothesis):
        return [{"label": "CONTRADICTION", "score": 0.9},
                {"label": "NEUTRAL",       "score": 0.07},
                {"label": "ENTAILMENT",    "score": 0.03}]

    score = contradiction_score("A is true", "A is false", _pipeline_fn=fake_pipeline)
    assert score == 0.9
"""

from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# Module-level singleton cache (mirrors rerank.py _RERANKER_CACHE pattern)
# ---------------------------------------------------------------------------

_NLI_PIPELINE_CACHE: dict[str, Any] = {}


def get_nli_pipeline(model_name: str, device: str = "auto") -> Any:
    """Load and cache a transformers zero-shot / NLI pipeline.

    First call pays the model-load latency (~2-5 s on CPU for mDeBERTa-v3).
    Subsequent calls return the cached object immediately.

    Parameters
    ----------
    model_name:
        HuggingFace Hub model ID.  The default configured model is
        ``MoritzLaurer/mDeBERTa-v3-base-mnli-xnli``.
    device:
        ``"auto"`` selects MPS (Apple Silicon) > CUDA > CPU, matching the
        embed_device convention.  Pass ``"cpu"`` to avoid MPS phantom-OOM
        (same guard that affects ledger embed build on Apple Silicon).

    Raises
    ------
    RuntimeError
        If ``transformers`` or ``torch`` is not installed, surfaces a clean
        "feature unavailable" message pointing to the setup script.
    """
    cache_key = f"{model_name}::{device}"
    cached = _NLI_PIPELINE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        from transformers import pipeline  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "contradiction_enabled=true requires the transformers library. "
            "Install with: ./scripts/setup-venv.sh --embeddings"
        ) from exc

    try:
        import torch  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "contradiction_enabled=true requires torch. "
            "Install with: ./scripts/setup-venv.sh --embeddings"
        ) from exc

    # Resolve the device argument.
    # "auto" -> let transformers / accelerate pick (mps > cuda > cpu).
    # Explicit "cpu" / "mps" / "cuda" pass through.
    pipeline_kwargs: dict[str, Any] = {}
    if device != "auto":
        pipeline_kwargs["device"] = device

    nli_pipe = pipeline(
        "text-classification",
        model=model_name,
        return_all_scores=True,
        **pipeline_kwargs,
    )
    _NLI_PIPELINE_CACHE[cache_key] = nli_pipe
    return nli_pipe


def reset_nli_pipeline_cache() -> None:
    """Clear the singleton cache.  Used in tests and after config changes."""
    _NLI_PIPELINE_CACHE.clear()


# ---------------------------------------------------------------------------
# Low-level scoring
# ---------------------------------------------------------------------------

def score_pair(
    premise: str,
    hypothesis: str,
    *,
    model_name: str = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
    device: str = "auto",
    _pipeline_fn: Callable[[str, str], list[dict[str, Any]]] | None = None,
) -> dict[str, float]:
    """Score a (premise, hypothesis) pair with the NLI classifier.

    Returns a dict with keys ``entailment``, ``neutral``, ``contradiction``
    whose values are probabilities summing to ~1.0.

    Parameters
    ----------
    premise:
        The reference statement (existing note body, frontmatter stripped).
    hypothesis:
        The candidate statement to evaluate against the premise.
    model_name:
        HuggingFace model ID.  Only used when ``_pipeline_fn`` is None.
    device:
        Compute device passed to :func:`get_nli_pipeline`.
    _pipeline_fn:
        Optional injection seam for tests.  When provided, ``model_name`` and
        ``device`` are ignored and ``_pipeline_fn(premise, hypothesis)`` is
        called directly.  It must return a list of dicts, each with ``"label"``
        and ``"score"`` keys (labels may be upper- or lower-case).
    """
    if _pipeline_fn is not None:
        raw = _pipeline_fn(premise, hypothesis)
    else:
        pipe = get_nli_pipeline(model_name, device)
        # NLI classifiers are trained on sentence pairs. Passing a manually
        # concatenated string turns the task into single-sequence classification
        # and can produce noisy contradiction scores.
        result = pipe({"text": premise, "text_pair": hypothesis})
        raw = result[0] if isinstance(result[0], list) else result

    # Normalise label names to lowercase without hyphens.
    scores: dict[str, float] = {}
    for item in raw:
        label = item["label"].lower().replace("-", "_")
        scores[label] = float(item["score"])

    # Ensure all three keys are present; default to 0.0 if the model omits one.
    for key in ("entailment", "neutral", "contradiction"):
        scores.setdefault(key, 0.0)

    return scores


# ---------------------------------------------------------------------------
# Bidirectional contradiction score
# ---------------------------------------------------------------------------

def contradiction_score(
    text_a: str,
    text_b: str,
    *,
    model_name: str = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
    device: str = "auto",
    _pipeline_fn: Callable[[str, str], list[dict[str, Any]]] | None = None,
) -> float:
    """Compute a bidirectional contradiction score for two note bodies.

    NLI is asymmetric: (A → B) and (B → A) can yield different contradiction
    probabilities.  For contradiction detection we want to catch both
    directions, so we score both orderings and return the maximum.

    Per-spec formula::

        score_ab = score_pair(a, b)["contradiction"]
        score_ba = score_pair(b, a)["contradiction"]
        return max(score_ab, score_ba)

    Parameters
    ----------
    text_a, text_b:
        Plain-text note bodies (frontmatter already stripped by the caller).
    model_name, device, _pipeline_fn:
        Forwarded to :func:`score_pair`.

    Returns
    -------
    float
        A value in [0, 1].  Higher means more likely a contradiction.
    """
    kwargs: dict[str, Any] = {
        "model_name": model_name,
        "device": device,
    }
    if _pipeline_fn is not None:
        kwargs["_pipeline_fn"] = _pipeline_fn

    score_ab = score_pair(text_a, text_b, **kwargs)["contradiction"]
    score_ba = score_pair(text_b, text_a, **kwargs)["contradiction"]
    return max(score_ab, score_ba)

"""Central configuration for Cognitive Ledger.

This module provides a centralized configuration system with documented
defaults and environment variable overrides. All magic numbers and paths
are defined here with their rationale.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ledger.layout import (
    CORE_NOTE_TYPES,
    indices_dir,
    note_type_metadata,
    note_type_dir as note_type_path,
    timeline_jsonl_path as layout_timeline_jsonl_path,
    timeline_path as layout_timeline_path,
)


REMOVED_CONFIG_KEYS = {
    "root_dir": "ledger_root",
    "notes_dir": "ledger_notes_dir",
    "source_root": "source_notes_dir",
}

REMOVED_ENV_VARS = {
    "LEDGER_ROOT_DIR": "LEDGER_ROOT",
    "LEDGER_SOURCE_ROOT": "LEDGER_SOURCE_NOTES_DIR",
}

PATH_FIELDS = frozenset({"ledger_root", "ledger_notes_dir", "source_notes_dir"})


_INSTALL_LOCATION_MARKERS = (
    "site-packages",
    "dist-packages",
    "Cellar/",
    "/libexec/",
    "pipx/venvs/",
    ".venv/",
)


def _looks_like_install_location(path: Path) -> bool:
    s = path.as_posix()
    return any(marker in s for marker in _INSTALL_LOCATION_MARKERS)


def _default_ledger_root() -> Path:
    """Determine the ledger root directory."""
    if env_root := os.getenv("LEDGER_ROOT"):
        return Path(env_root).expanduser().resolve()
    fallback = Path(__file__).resolve().parents[1]
    if _looks_like_install_location(fallback):
        import warnings

        warnings.warn(
            f"LEDGER_ROOT is not set and the package appears to be installed "
            f"(resolved fallback: {fallback}). This is almost certainly wrong - "
            f"set LEDGER_ROOT to your cognitive-ledger source clone.",
            RuntimeWarning,
            stacklevel=2,
        )
    return fallback


def _default_source_notes_dir() -> Path:
    """Determine the default source notes directory."""
    return Path.home() / "notes"


def _xdg_config_path() -> Path:
    """Canonical user-level config location (XDG Base Directory spec).

    Config lives with the *installation* (`$XDG_CONFIG_HOME/ledger/config.yaml`),
    not inside the source checkout — the package, the config, and the ledger
    folder are independent.
    """
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "ledger" / "config.yaml"


def _legacy_xdg_config_path() -> Path:
    """Deprecated pre-rename config location (``ledger`` was ``cognitive-ledger``).

    Read as a low-priority fallback so existing installs keep working; the
    canonical :func:`_xdg_config_path` overrides it.
    """
    base = os.getenv("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "cognitive-ledger" / "config.yaml"


def _raise_migration_error(items: list[str]) -> None:
    raise RuntimeError("\n".join(items))


def _fail_on_removed_config_keys(data: dict[str, Any], path: Path) -> None:
    removed = [
        f"config key '{old}' in {path} has been removed; use '{new}' instead"
        for old, new in REMOVED_CONFIG_KEYS.items()
        if old in data
    ]
    if removed:
        _raise_migration_error(removed)


def _fail_on_removed_env_vars() -> None:
    removed = [
        f"environment variable '{old}' has been removed; use '{new}' instead"
        for old, new in REMOVED_ENV_VARS.items()
        if os.getenv(old) is not None
    ]
    if removed:
        _raise_migration_error(removed)


def _coerce_value(key: str, value: Any, current: Any) -> Any:
    if key in PATH_FIELDS:
        return Path(str(value)).expanduser()
    if isinstance(current, Path):
        return Path(str(value)).expanduser()
    if isinstance(current, (tuple, frozenset)):
        return current
    if isinstance(current, dict):
        # Pass dict values (e.g. things3_scope_routing) through unchanged
        return value if isinstance(value, dict) else current
    return value


def _apply_dict(config: "LedgerConfig", data: dict) -> None:
    """Apply a dict of key-value pairs to a LedgerConfig, coercing types."""
    for key, value in data.items():
        if not hasattr(config, key):
            continue
        if key in {"_ledger_notes_dir_explicit", "_source_notes_dir_explicit"}:
            continue
        if isinstance(getattr(type(config), key, None), property):
            continue
        current = getattr(config, key)
        value = _coerce_value(key, value, current)
        setattr(config, key, value)
        if key == "ledger_notes_dir":
            config._ledger_notes_dir_explicit = True
        elif key == "source_notes_dir":
            config._source_notes_dir_explicit = True
    config._finalize_paths()


def _load_yaml_data(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on env
        raise RuntimeError(
            f"YAML support is required to read {path}. "
            "Install PyYAML or run ./scripts/setup-venv.sh."
        ) from exc

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Expected {path} to contain a mapping at the top level.")
    _fail_on_removed_config_keys(data, path)
    return data


def _apply_yaml_config(config: "LedgerConfig", path: Path) -> "LedgerConfig":
    """Apply config.yaml overrides if the file exists."""
    data = _load_yaml_data(path)
    if data is not None:
        _apply_dict(config, data)
    return config


def _apply_env_overrides(config: "LedgerConfig") -> "LedgerConfig":
    """Apply environment variable overrides to an existing config instance."""
    _fail_on_removed_env_vars()

    path_mappings = {
        "LEDGER_ROOT": "ledger_root",
        "LEDGER_NOTES_DIR": "ledger_notes_dir",
        "LEDGER_SOURCE_NOTES_DIR": "source_notes_dir",
    }
    for env_var, attr in path_mappings.items():
        if (value := os.getenv(env_var)) is None:
            continue
        setattr(config, attr, Path(value).expanduser())
        if attr == "ledger_notes_dir":
            config._ledger_notes_dir_explicit = True
        elif attr == "source_notes_dir":
            config._source_notes_dir_explicit = True

    # Integer overrides
    int_mappings = {
        "LEDGER_SHORTLIST_MIN": "shortlist_min_candidates",
        "LEDGER_SHORTLIST_MAX": "shortlist_max_candidates",
        "LEDGER_ATTENTION_MIN": "attention_shortlist_min",
        "LEDGER_ATTENTION_MAX": "attention_shortlist_max",
        "LEDGER_REASONS_LIMIT": "detailed_reasons_limit",
        "LEDGER_EMBED_BATCH_SIZE": "embed_batch_size",
        "LEDGER_CONTRADICTION_NEIGHBORS_K": "contradiction_neighbors_k",
        "LEDGER_JUDGE_SEED_TOP_K": "judge_seed_top_k",
    }
    for env_var, attr in int_mappings.items():
        if (value := os.getenv(env_var)) is None:
            continue
        try:
            setattr(config, attr, int(value))
        except ValueError:
            import warnings
            warnings.warn(
                f"Ignoring invalid integer value {value!r} for {env_var}",
                stacklevel=3,
            )

    # Float overrides
    float_mappings = {
        "LEDGER_WEIGHT_BM25": "score_weight_bm25",
        "LEDGER_WEIGHT_LEXICAL": "score_weight_lexical",
        "LEDGER_WEIGHT_TAG": "score_weight_tag",
        "LEDGER_WEIGHT_SCOPE": "score_weight_scope",
        "LEDGER_WEIGHT_RECENCY": "score_weight_recency",
        "LEDGER_WEIGHT_CONFIDENCE": "score_weight_confidence",
        "LEDGER_WEIGHT_SIGNAL": "score_weight_signal",
        "LEDGER_CONTRADICTION_AUTO_THRESHOLD": "contradiction_auto_threshold",
        "LEDGER_CONTRADICTION_REVIEW_THRESHOLD": "contradiction_review_threshold",
        "LEDGER_CONTRADICTION_AUTO_THRESHOLD_LANG_NO": "contradiction_auto_threshold_lang_no",
        "LEDGER_PRIOR_WEIGHT": "prior_weight",
        "LEDGER_PRIOR_W_IMPORTANCE": "prior_w_importance",
        "LEDGER_PRIOR_W_RECENCY": "prior_w_recency",
        "LEDGER_PRIOR_W_RELEVANCE": "prior_w_relevance",
        "LEDGER_PRIOR_HALF_LIFE": "prior_recency_half_life_days",
        "LEDGER_PRIOR_TIE_BAND": "prior_tie_band",
        "LEDGER_PRF_ALPHA": "prf_alpha",
        "LEDGER_PRF_BETA": "prf_beta",
        "LEDGER_PRF_GAMMA": "prf_gamma",
        "LEDGER_SYNTHETIC_WEIGHT": "synthetic_weight",
        "LEDGER_VALIDATION_BOOST_PER": "validation_boost_per_signal",
        "LEDGER_VALIDATION_BOOST_CAP": "validation_boost_cap",
    }
    for env_var, attr in float_mappings.items():
        if (value := os.getenv(env_var)) is None:
            continue
        try:
            setattr(config, attr, float(value))
        except ValueError:
            import warnings
            warnings.warn(
                f"Ignoring invalid float value {value!r} for {env_var}",
                stacklevel=3,
            )

    # Boolean overrides ("1", "true", "yes", "on" -> True; else False)
    bool_mappings = {
        "LEDGER_SIGNALS_AUTO_CAPTURE": "signals_auto_capture",
        "LEDGER_CONTRADICTION_ENABLED": "contradiction_enabled",
        "LEDGER_CONTRADICTION_PROTECT_HIGHER_CONFIDENCE": "contradiction_protect_higher_confidence",
        "LEDGER_PRIOR_ENABLED": "prior_enabled",
        "LEDGER_PROVENANCE_WEIGHTING": "provenance_weighting_enabled",
        "LEDGER_SHOW_TRUST_VERDICT": "show_trust_verdict",
        "LEDGER_PRF_ENABLED": "prf_enabled",
        "LEDGER_THINGS3_SYNC_ENABLED": "things3_sync_enabled",
    }
    for env_var, attr in bool_mappings.items():
        if (value := os.getenv(env_var)) is None:
            continue
        setattr(config, attr, value.strip().lower() in ("1", "true", "yes", "on"))

    # Dict override (plan 43): LEDGER_HALF_LIFE_BY_TYPE="preferences:90,facts:365"
    if (value := os.getenv("LEDGER_HALF_LIFE_BY_TYPE")) is not None:
        parsed: dict[str, float] = {}
        for pair in value.split(","):
            pair = pair.strip()
            if not pair or ":" not in pair:
                continue
            key, _, raw = pair.partition(":")
            try:
                parsed[key.strip()] = float(raw.strip())
            except ValueError:
                import warnings
                warnings.warn(
                    f"Ignoring invalid half-life pair {pair!r} in LEDGER_HALF_LIFE_BY_TYPE",
                    stacklevel=3,
                )
        if parsed:
            config.recency_half_life_by_type = parsed

    string_mappings = {
        "LEDGER_RETRIEVAL_MODE": "retrieval_mode",
        "LEDGER_EMBED_BACKEND": "embed_backend",
        "LEDGER_EMBED_MODEL": "embed_model",
        "LEDGER_EMBED_DEVICE": "embed_device",
        "LEDGER_CONTRADICTION_MODEL": "contradiction_model",
        "LEDGER_FUSION": "fusion",
        "LEDGER_JUDGE_BACKEND": "judge_backend",
        "LEDGER_JUDGE_COMMAND": "judge_subprocess_command",
        "LEDGER_SYNTH_BACKEND": "synth_backend",
        "LEDGER_SYNTH_COMMAND": "synth_command",
        "LEDGER_SYNTH_MODEL": "synth_model",
        "LEDGER_SYNTH_HOST": "synth_host",
        "LEDGER_THINGS3_DB_PATH": "things3_db_path",
        "LEDGER_THINGS3_DEFAULT_PROJECT": "things3_default_project",
        "LEDGER_THINGS3_BLOCKED_PROJECT": "things3_blocked_project",
        "LEDGER_THINGS3_MARKER_PREFIX": "things3_marker_prefix",
        "LEDGER_THINGS3_COMPLETED_MAPS_TO": "things3_completed_maps_to",
        "LEDGER_THINGS3_CANCELED_MAPS_TO": "things3_canceled_maps_to",
        "LEDGER_THINGS3_ORPHAN_ACTION": "things3_orphan_action",
    }
    for env_var, attr in string_mappings.items():
        if (value := os.getenv(env_var)) is None:
            continue
        cleaned = str(value).strip()
        if cleaned:
            setattr(config, attr, cleaned)

    config._finalize_paths()
    return config


@dataclass
class LedgerConfig:
    """Central configuration with documented defaults.

    All constants that were previously magic numbers are defined here
    with documentation explaining their rationale and how they were tuned.
    """

    # =========================================================================
    # Paths
    # =========================================================================

    ledger_root: Path = field(default_factory=_default_ledger_root)
    """Root directory of the cognitive ledger repository."""

    ledger_notes_dir: Path | None = None
    """Physical directory containing the ledger note corpus."""

    source_notes_dir: Path | None = None
    """Root directory for source notes (used in discovery mode)."""

    _ledger_notes_dir_explicit: bool = field(default=False, init=False, repr=False)
    _source_notes_dir_explicit: bool = field(default=False, init=False, repr=False)

    # =========================================================================
    # Note Type Configuration
    # =========================================================================

    # =========================================================================
    # Identity Scoring
    # =========================================================================

    identity_score_boost: float = 0.15
    """Additive boost for identity notes in retrieval scoring.

    Rationale: Identity notes (mission, beliefs, models, strategies,
    narratives) are high-signal, almost-always-relevant context that
    should surface above generic notes of similar lexical relevance.
    """

    boot_min_confidence: float = 0.8
    """Minimum confidence for notes included in boot context.

    Rationale: Prevents low-confidence hypotheses from polluting
    the session-start context. Notes below this threshold are still
    findable via search but excluded from automatic loading.
    """

    # =========================================================================
    # Retrieval Tuning
    # =========================================================================

    # These values were tuned empirically via the eval framework (2026-02)
    # to balance precision vs recall at different query complexities.

    shortlist_min_candidates: int = 24
    """Minimum candidate pool for two-stage shortlisting.

    Rationale: Below this, statistical relevance suffers. Empirically
    determined via eval framework to maintain hit@k >= 0.80.
    """

    shortlist_max_candidates: int = 36
    """Maximum candidate pool before diminishing returns.

    Rationale: Above this, scoring overhead increases without
    improving retrieval quality. Tuned via A/B experiments.
    """

    shortlist_limit_multiplier: int = 3
    """Multiplier for target shortlist size: limit * multiplier.

    Rationale: For limit=8, we want ~24 candidates to ensure
    the true top-8 are likely included after re-ranking.
    """

    attention_shortlist_min: int = 32
    """Minimum candidates for attention-based scoring.

    Rationale: Higher than standard shortlist because attention
    scoring has more discriminative power and benefits from
    a larger candidate pool.
    """

    attention_shortlist_max: int = 72
    """Maximum candidates for attention-based scoring.

    Rationale: Attention scoring is more expensive, so we cap
    higher to allow broader semantic coverage.
    """

    attention_shortlist_limit_multiplier: int = 2
    """Multiplier for attention shortlist."""

    detailed_reasons_limit: int = 20
    """Above this limit, skip reason generation for performance.

    Rationale: Generating reason strings is expensive. For
    large result sets (eval mode), skip to improve throughput.
    """

    progressive_rationale_top: int = 3
    """Number of top results that get full rationale in progressive mode.

    Rationale: Users typically care about why top results ranked
    high. Beyond top-3, compact representation suffices.
    """

    # =========================================================================
    # Scoring Weights (Lexical Mode)
    # =========================================================================

    # These weights were tuned via the eval framework to maximize MRR
    # on the retrieval_eval_cases.yaml benchmark.

    score_weight_bm25: float = 0.30
    """Weight for BM25 keyword ranking.

    Rationale: BM25 captures term frequency and document length
    better than plain overlap, so it gets the largest single share.
    """

    score_weight_lexical: float = 0.15
    """Weight for lexical (token overlap) match.

    Rationale: Secondary lexical signal once BM25 is active.
    """

    score_weight_tag: float = 0.15
    """Weight for tag overlap.

    Rationale: Tags are curated metadata, so matches are
    meaningful but less discriminative than body text.
    """

    score_weight_scope: float = 0.15
    """Weight for scope match.

    Rationale: Scope matching filters by context (work/home)
    but shouldn't dominate over content relevance.
    """

    score_weight_recency: float = 0.15
    """Weight for recency (decay over 90 days).

    Rationale: Recent notes are often more relevant, but
    old notes shouldn't be completely buried.
    """

    score_weight_confidence: float = 0.10
    """Weight for note confidence score.

    Rationale: Higher-confidence notes are more reliable
    but low-confidence notes shouldn't be excluded.
    """

    score_weight_signal: float = 0.0
    """Weight for signal feedback score (0.0 until sufficient data).

    Rationale: Disabled by default until signals.jsonl has enough
    entries to be meaningful. Enable via config.yaml once the
    feedback loop has accumulated data.
    """

    negative_eval_max_score: float = 0.5
    """Score threshold for negative eval cases (expected_none: true).

    A negative eval case passes if the top-1 retrieval result has a score
    at or below this threshold (indicating the retrieval correctly abstained).
    A result above this threshold is counted as a false positive.

    Rationale: 0.5 is a reasonable mid-point for the lexical retrieval
    score range; tune if using semantic or hybrid retrieval modes.
    """

    # =========================================================================
    # Provenance-weighted confidence (plan 42)
    # =========================================================================

    provenance_weighting_enabled: bool = False
    """Whether to derive effective confidence from provenance + validation count.

    When False (default), ranking uses the raw ``confidence`` frontmatter value
    exactly as before — this plan is behaviour-neutral until enabled. When True,
    confidence is replaced by ``scoring.effective_confidence`` everywhere it feeds
    ranking (the weighted-sum confidence term and the prior's importance term).

    Flip to True only after ``ledger ab run`` proves a recall@k improvement.
    """

    validation_boost_per_signal: float = 0.03
    """Effective-confidence boost added per affirmation signal on a note."""

    validation_boost_cap: float = 0.15
    """Maximum total validation boost (caps ``per_signal × count``)."""

    show_trust_verdict: bool = True
    """Whether to attach a high/medium/low trust verdict to results (plan 46).

    Display-only: the verdict appears in ``--view detail`` and the JSON envelope
    but never feeds ranking (result order is identical with it on or off). When
    provenance weighting is off, the verdict is computed from raw confidence.
    """

    # =========================================================================
    # Prior Score (Mechanism 1 — cold-start ranking)
    # =========================================================================

    prior_enabled: bool = True
    """Whether to apply the prior score as an additive ranking term.

    When True (default), a prior score is computed from note importance
    (confidence), recency (half-life decay), and query relevance, and
    blended into the final score via prior_weight. This makes ranking
    non-flat before any signal feedback has accrued.

    Set to False to reproduce pre-prior scores exactly (useful for A/B).
    """

    prior_weight: float = 0.10
    """Additive weight applied to the prior score component.

    The prior is applied as a TIE-BREAKER, not a flat additive bonus: its
    contribution is scaled toward zero for any candidate whose base
    (pre-prior) score trails the local leader by more than ``prior_tie_band``
    (see ``apply_prior_tiebreak``). This keeps prior_weight=0.10 safe — a
    clear semantic winner is never displaced by the prior, while genuinely
    near-tied candidates are still ordered by note quality / recency.

    Rationale: Small enough that the prior nudges ordering within a tie band
    without overriding lexical/semantic relevance. Tunable via A/B eval.
    """

    prior_tie_band: float = 0.02
    """Relative base-score gap (as a fraction of the local leader's base
    score) within which the prior acts as a tie-breaker.

    The prior contribution for a candidate is multiplied by a factor that
    decays continuously from 1.0 (gap == 0, candidate IS the local leader)
    to 0.0 (gap >= prior_tie_band). A candidate whose base score trails the
    leader by more than this band receives NO prior contribution and thus
    keeps its base-score rank regardless of its confidence/recency prior.

    The scaling is continuous (not a hard cutoff) to avoid rank instability
    at the band boundary. Default 0.02 (2% relative gap) was tuned on the
    real semantic_hybrid eval corpus: it restores the pre-prior baseline
    exactly (hit@1 0.733, hit@3 0.889, mrr 0.804) while still letting the
    prior reorder genuine ties. With semantic_hybrid the relevance signal is
    already strong, so the tie band must stay narrow. Override via
    LEDGER_PRIOR_TIE_BAND.
    """

    prior_w_importance: float = 0.30
    """Weight of confidence (importance) inside the prior score.

    Rationale: High-confidence notes are more reliable anchors.
    """

    prior_w_recency: float = 0.30
    """Weight of recency inside the prior score (half-life decay).

    Rationale: More recent notes tend to be more relevant, but
    old notes should not be completely buried. The half-life
    parameter (prior_recency_half_life_days) controls the decay rate.
    """

    prior_w_relevance: float = 0.40
    """Weight of query-relevance inside the prior score.

    In lexical mode: fraction of query tokens found in the note.
    In semantic_hybrid mode: cosine similarity to the query vector.
    Rationale: Relevance is the primary discriminator; the prior
    amplifies the relevance signal for high-quality notes.
    """

    prior_recency_half_life_days: float = 180.0
    """Half-life (in days) for the prior recency decay.

    At this age a note gets 0.5 recency score; at 2x this age, 0.25.
    Rationale: 180 days (6 months) is a reasonable balance between
    favouring fresh content and preserving durable long-term knowledge.
    Use a longer half-life than the existing 90-day linear decay to
    keep older but high-quality notes competitive.
    """

    recency_half_life_by_type: dict[str, float] = field(default_factory=dict)
    """Per-note-type override for the prior recency half-life (plan 43).

    Maps a note type label (``fact``, ``pref``, ``loop``, ``id``, ``goal``,
    ``concept``) to its half-life in days. Types absent from the map fall back
    to ``prior_recency_half_life_days``. Empty by default → every type uses the
    global half-life, i.e. behaviour-neutral until configured.

    Rationale (Memanto): freshness matters more for some types than others — a
    two-year-old preference is likely stale, a fact is not. Suggested tuning:
    ``{preferences: 90, loops: 60, goals: 180, facts: 365, concepts: 365,
    identity: 540}``. A/B-validate before flipping defaults. Env override:
    ``LEDGER_HALF_LIFE_BY_TYPE="preferences:90,facts:365"``.
    """

    auto_file_synthesis: bool = False
    """Whether to automatically file synthesized answers as notes.

    When True, query answers that synthesize from 2+ notes and produce
    new insight will be automatically filed as concept/fact notes.
    When False (default), the agent asks the user before filing.
    """

    signal_min_entries: int = 20
    """Minimum signal entries before signal scoring activates.

    Rationale: With too few signals, the signal score is noisy
    and can distort retrieval. This gate prevents premature activation.
    """

    signals_auto_capture: bool = False
    """Whether queries auto-emit use-time signals (retrieval_miss/hit).

    Off by default to honor the no-noise principle: signals are only
    written when you opt in via config.yaml or LEDGER_SIGNALS_AUTO_CAPTURE.
    Even when on, signal feedback stays inert for ranking until
    ``signal_min_entries`` (20) accrue and ``score_weight_signal`` is
    raised above 0 — so turning this on is safe and reversible.
    """

    signals_miss_score_floor: float = 0.15
    """Top-result score below which a query counts as a retrieval_miss.

    A query that returns nothing, or whose best hit scores under this
    floor, is treated as a coverage gap when ``signals_auto_capture`` is
    on. Tuned conservatively: only genuinely weak matches log a miss.
    """

    synthetic_weight: float = 0.5
    """Down-weight factor applied to synthetic (LLM-seeded) signal events.

    Synthetic events (``synthetic: true``) count as this fraction of a real
    signal when computing ``signal_score`` and per-note stats.  Default 0.5
    means a seeded retrieval_hit contributes half as much as a real user hit.

    Set to 0.0 to ignore synthetic events entirely; 1.0 to treat them as
    equal to real signals (not recommended until judge quality is validated).
    """

    judge_backend: str = "dummy"
    """Backend used by ``ledger signal seed`` to judge (query, note) pairs.

    ``"dummy"`` — deterministic lexical-overlap heuristic (no network, no
    LLM call; stable for tests and offline use).

    ``"subprocess"`` — shells out to ``judge_subprocess_command``.  The
    command receives a JSON object on stdin and must return a JSON verdict.
    Example: ``claude -p`` or an Ollama call.
    """

    judge_subprocess_command: str = ""
    """Shell command template for the subprocess judge backend.

    Used only when ``judge_backend = "subprocess"``.
    The command receives the prompt payload as JSON on stdin and must
    write a JSON verdict to stdout with keys ``relevant`` (bool),
    ``rating`` (int 1–10, optional), ``reason`` (str, optional).

    Example values:
      ``"claude -p"``
      ``"/usr/local/bin/ollama run llama3 --format json"``
    """

    judge_seed_top_k: int = 5
    """Number of notes retrieved per query during ``ledger signal seed``.

    Higher values produce more seeded events but increase judge calls.
    Keep low (3–5) for the dummy backend; can raise to 10+ with a fast
    subprocess backend once quality is validated.
    """

    # =========================================================================
    # Answer synthesis (plan 45 — `ledger answer`)
    # =========================================================================

    synth_backend: str = "dummy"
    """LLM backend for `ledger answer` grounded synthesis.

    ``dummy`` (offline/test-safe, default), ``claude`` (claude CLI),
    ``ollama`` (local server), or ``subprocess`` (pipe prompt to ``synth_command``).
    Distinct from ``judge_backend`` because synthesis is plaintext-in/plaintext-out
    while the judge uses a JSON protocol.
    """

    synth_command: str = ""
    """Shell command for ``synth_backend = subprocess`` (shlex-split; prompt on stdin)."""

    synth_model: str = ""
    """Optional model name passed to the synth backend (e.g. a claude/ollama model)."""

    synth_host: str = "http://localhost:11434"
    """Ollama host for ``synth_backend = ollama``."""

    synth_timeout: float = 120.0
    """Timeout (seconds) for synth backend calls."""

    # =========================================================================
    # Semantic Scoring Weights (Hybrid Mode)
    # =========================================================================

    semantic_weight_vector: float = 0.55
    """Weight for semantic (embedding) similarity.

    Rationale: Primary signal in hybrid mode. Embedding
    similarity captures semantic meaning beyond keywords.
    """

    semantic_weight_lexical: float = 0.30
    """Weight for lexical component in hybrid scoring.

    Rationale: Lexical still matters for exact matches
    that embeddings might miss.
    """

    semantic_weight_scope: float = 0.10
    """Weight for scope in hybrid scoring."""

    semantic_weight_recency: float = 0.05
    """Weight for recency in hybrid scoring.

    Rationale: Lower than lexical mode because semantic
    similarity already captures relevance well.
    """

    # =========================================================================
    # Loop and Query Settings
    # =========================================================================

    loop_statuses: tuple[str, ...] = ("open", "closed", "blocked", "snoozed")
    """Valid loop status values."""

    query_scopes: tuple[str, ...] = ("home", "work", "dev", "personal", "life", "meta", "all")
    """Valid query scope values (life is an alias for personal)."""

    retrieval_modes: tuple[str, ...] = (
        "legacy",
        "two_stage",
        "scope_type_prefilter",
        "precomputed_index",
        "progressive_disclosure",
        "semantic_hybrid",
        "semantic_rerank",
    )
    """Available retrieval modes. Default: precomputed_index (best MRR + fast).
    compressed_attention was removed after A/B testing showed hit@k regression.
    semantic_rerank runs semantic_hybrid then re-orders the top-N with a
    cross-encoder; trades 50-200ms latency for top-1 precision."""

    embed_backends: tuple[str, ...] = ("local", "openai")
    """Available embedding backends."""

    retrieval_mode: str = "semantic_hybrid"
    """Default retrieval mode when no CLI flag or environment override is set."""

    embed_backend: str = "local"
    """Default embedding backend for semantic_hybrid mode."""

    embed_model: str | None = None
    """Optional default embedding model override for semantic_hybrid mode."""

    ab_objective_weights: dict[str, float] = field(
        default_factory=lambda: {"mrr": 0.6, "hitk": 0.4}
    )
    """Weights for the scalar A/B objective (``ledger ab loop``). Keys are
    quality metric names (hit1/hitk/mrr); the objective is their weighted sum."""

    embed_devices: tuple[str, ...] = ("auto", "cpu", "mps", "cuda")
    """Valid values for embed_device / `embed build --device`."""

    embed_device: str = "auto"
    """Device for the local embedder (sentence-transformers).

    "auto" keeps sentence-transformers' own selection (cuda → mps → cpu).
    Set "cpu" to dodge the spurious MPS allocator OOM on Apple Silicon
    (the MPS guard misfires on a phantom working-set figure even when RAM
    is free); "mps"/"cuda" force the GPU. Override per-build with
    `embed build --device`, or globally via LEDGER_EMBED_DEVICE.
    """

    embed_batch_size: int = 32
    """Encode batch size for the local embedder.

    Lower it (e.g. 8) to bound peak GPU memory so large models like bge-m3
    don't trip the MPS allocator guard on long-document corpora. Override
    per-build with `embed build --batch-size`, or via LEDGER_EMBED_BATCH_SIZE.
    """

    embed_text_template: str = "none"
    """Text template applied to passages at index time and queries at search time.

    Values:
      - "none": no transformation (default; correct for bge / sentence-transformers).
      - "e5_prefix": prepend "passage: " to documents and "query: " to queries.
        Required by the intfloat/e5-* family for correct retrieval.

    The template used at build time is stored in the semantic index payload and
    re-applied automatically on the query side, so a build-time setting is
    sufficient (the query side reads it from the index, not from this config).
    Stored here for build-time defaults and documentation.
    """

    embed_text_templates: tuple[str, ...] = ("none", "e5_prefix")
    """Allowed values for embed_text_template."""

    # =========================================================================
    # Reranker (semantic_rerank mode)
    # =========================================================================

    rerank_model: str = "BAAI/bge-reranker-base"
    """Cross-encoder model used by semantic_rerank mode.

    Options tested:
      - BAAI/bge-reranker-base: ~278M params, English-leaning, ~50ms/pair on CPU.
      - BAAI/bge-reranker-v2-m3: ~568M params, multilingual incl. Norwegian.
    """

    rerank_input_k: int = 20
    """Number of candidates fetched from semantic_hybrid for reranking.

    Larger pools give the cross-encoder more candidates but cost more latency.
    Each candidate pair adds ~50ms for bge-reranker-base on CPU.
    """

    rerank_output_k: int = 8
    """Final number of results returned after reranking. Truncates the
    top-N reranked candidates."""

    rerank_batch_size: int = 32
    """Cross-encoder batch size. Higher = better throughput, more peak memory."""

    rerank_max_length: int = 512
    """Cross-encoder max token length per (query, document) pair.

    Long documents are truncated by the tokenizer; we also pre-truncate text
    by characters before tokenization to bound memory.
    """

    # =========================================================================
    # Contradiction Detection (Gap B — NLI-based sleep scan)
    # =========================================================================

    contradiction_enabled: bool = False  # off until NLI model downloaded + validated on corpus
    """Master switch for the NLI-based contradiction scan.

    Off by default: a machine without the model/deps behaves exactly as today.
    Enable in config.yaml once the NLI model is downloaded and the feature is
    validated on your corpus.
    """

    contradiction_model: str = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
    """Transformers model used for NLI-based contradiction detection.

    MoritzLaurer/mDeBERTa-v3-base-mnli-xnli is a multilingual 3-way
    (entailment / neutral / contradiction) classifier trained on MNLI + XNLI.
    It covers 15 languages. Norwegian is NOT one of XNLI's languages — see the
    Norwegian-accuracy caveat in ledger/nli.py.
    """

    contradiction_neighbors_k: int = 8
    """Number of nearest semantic neighbours to check per candidate note.

    Retrieval uses the existing semantic index (same scope, compatible type).
    Increasing k improves recall at the cost of NLI inference time.
    """

    contradiction_auto_threshold: float = 0.85
    """Contradiction probability above which auto-supersession is attempted.

    Both temporal ordering and confidence rules are applied before any
    auto-supersession fires — see spec for the full decision tree.
    """

    contradiction_review_threshold: float = 0.60
    """Contradiction probability above which a conflict note is routed to inbox.

    Pairs scoring in [review_threshold, auto_threshold) are flagged for human
    review rather than auto-resolved. Below review_threshold the pair is ignored.
    """

    contradiction_auto_threshold_lang_no: float = 0.95
    """Stricter auto-supersession threshold for lang:no and mixed-language notes.

    XNLI has no Norwegian training data; mDeBERTa-v3 accuracy on Norwegian
    text is unvalidated. A stricter threshold reduces false auto-supersessions
    on Norwegian-language content. See ledger/nli.py for the full caveat.
    """

    contradiction_protect_higher_confidence: bool = True
    """When the older note has strictly higher confidence, downgrade auto to review.

    Prevents a high-confidence established fact from being silently superseded
    by a newer, lower-confidence note even when contradiction score is above the
    auto threshold.
    """

    # =========================================================================
    # Fusion Mode (semantic_hybrid)
    # =========================================================================

    fusion: str = "weighted_sum"  # keep default until A/B run shows an alternative beats this
    """Fusion strategy for combining lexical and semantic candidate lists.

    Allowed values:
      - "weighted_sum": existing behaviour — single pass weighted-sum formula
        using semantic_weight_* constants. Default; byte-identical to previous
        behaviour when selected.
      - "rrf": Reciprocal Rank Fusion — generates a lexical ranking and a
        semantic ranking independently, then merges with RRF(k=rrf_k).
        Opt-in pending A/B eval; does not affect weighted_sum path.

    Override via LEDGER_FUSION env var.
    """

    rrf_k: int = 60
    """RRF smoothing constant (k in 1/(k+rank)).

    Higher k reduces the score difference between adjacent ranks.
    Standard value used in literature is 60.
    """

    # =========================================================================
    # Pseudo-Relevance Feedback (PRF — Mechanism 2, dense-vector only)
    # =========================================================================

    prf_enabled: bool = False  # enable only after A/B shows recall improvement on real corpus
    """Whether to apply pseudo-relevance feedback to expand the query vector.

    Default off. Only active on the semantic_hybrid / semantic_rerank dense
    path. Has no effect on lexical modes.

    When on, the top ``prf_top_m`` results are used as pseudo-positives and
    the bottom ``prf_bottom_n`` of an expanded pool as pseudo-negatives.
    The query vector is then updated via the Rocchio formula:
      q2 = alpha*q + beta*mean(pos) - gamma*mean(neg)
    and re-ranking is performed with q2.

    Set to true only after A/B validation shows improvement. Toggle via
    config.yaml: ``prf_enabled: true`` or CLI flag ``--prf``.
    Override via LEDGER_PRF_ENABLED env var.
    """

    prf_top_m: int = 3
    """Number of top results used as pseudo-positive vectors in PRF."""

    prf_bottom_n: int = 5
    """Number of bottom results used as pseudo-negative vectors in PRF."""

    prf_alpha: float = 1.0
    """Rocchio alpha: weight of original query vector."""

    prf_beta: float = 0.75
    """Rocchio beta: weight of pseudo-positive centroid."""

    prf_gamma: float = 0.15
    """Rocchio gamma: weight of pseudo-negative centroid."""

    # =========================================================================
    # Text Processing
    # =========================================================================

    stopwords: frozenset[str] = frozenset({
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "how", "i", "in", "is", "it", "my", "of", "on", "or", "that",
        "the", "to", "we", "what", "when", "where", "which", "who",
        "with", "you",
    })
    """Stopwords to exclude from tokenization."""

    # =========================================================================
    # Embedding Configuration
    # =========================================================================

    default_local_model: str = "TaylorAI/bge-micro-v2"
    """Default model for local embedding backend."""

    default_openai_model: str = "text-embedding-3-small"
    """Default model for OpenAI embedding backend."""

    # =========================================================================
    # Things3 Sync Integration
    # =========================================================================

    things3_sync_enabled: bool = False
    """Master switch for the Things3 ⇄ open-loops sync.

    Off by default: no subprocess calls are made when this is False.
    Enable in config.yaml once Things3 is installed and the Things CLI
    (https://culturedcode.com/things/support/articles/2803573/) is on PATH.
    """

    things3_db_path: str = ""
    """Path to Things3 SQLite database (leave empty to use CLI reads only).

    Usually ~/Library/Group Containers/JLMPQHK86H.com.culturedcode.ThingsMac/
    ThingsData-xxx/Things Database.thingsdatabase/main.sqlite
    Leave empty — the Things CLI handles DB access.
    """

    things3_default_project: str = ""
    """Things project name (or UUID) for new loop tasks (scope: any not mapped).

    Leave empty to create tasks in the Things Inbox.
    """

    things3_blocked_project: str = ""
    """Things project name (or UUID) for loops with status:blocked.

    Leave empty to use things3_default_project for blocked loops too.
    """

    things3_scope_routing: dict = field(default_factory=dict)
    """Map of ledger scope → Things project name for new task creation.

    Example YAML::

        things3_scope_routing:
          work: "Work Tasks"
          dev:  "Dev"
          home: "Home"

    Scopes not listed fall back to things3_default_project.
    """

    things3_marker_prefix: str = "ledger:"
    """Prefix embedded in Things task Notes field to link back to a loop.

    Format: ``ledger:<slug> status:<status>``
    Do not change after first sync — existing tasks use this prefix.
    """

    things3_completed_maps_to: str = "closed"
    """Ledger loop status to set when a Things task is marked completed.

    Allowed values: closed | snoozed (or any value in loop_statuses).
    """

    things3_canceled_maps_to: str = "snoozed"
    """Ledger loop status to set when a Things task is marked cancelled.

    Allowed values: closed | snoozed (or any value in loop_statuses).
    """

    things3_orphan_action: str = "flag"
    """What to do with Things tasks whose ledger loop has been deleted.

    Allowed values:
      - ``flag``: add "[orphan]" tag to the Things task (default, safe)
      - ``cancel``: cancel the orphaned task in Things
      - ``ignore``: leave orphans untouched
    """

    # =========================================================================
    # Profiles (named scope presets)
    # =========================================================================

    profiles: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "work": {
            "scope": "work",
            "retrieval_mode": "semantic_hybrid",
            "limit": 8,
        },
        "personal": {
            "scope": "personal",
            "retrieval_mode": "semantic_hybrid",
            "limit": 6,
        },
        "dev": {
            "scope": "dev",
            "retrieval_mode": "semantic_hybrid",
            "limit": 8,
        },
    })
    """Named query / context presets.

    Each profile is a dict with optional keys:
      - ``scope``: note scope filter (e.g. "work", "personal", "all")
      - ``retrieval_mode``: override the retrieval mode for this profile
      - ``limit``: default result limit for this profile

    Users may define additional profiles in ``~/.config/ledger/config.yaml``.
    Built-in profiles are ``work``, ``personal``, and ``dev``.
    """

    # =========================================================================
    # Write Policy
    # =========================================================================

    write_policy: dict[str, Any] = field(default_factory=lambda: {
        "mode": "auto-write",
        "report_level": "summary",
    })
    """Agent write policy injected into context payloads.

    Keys:
      - ``mode``: one of ``auto-write``, ``silent-write``, ``ask-to-write``
        (see AGENTS.md for semantics).
      - ``report_level``: one of ``summary`` (one-line per change),
        ``verbose`` (full diff), ``silent`` (no report).

    This dict is appended verbatim to the ``build_context()`` boot payload
    so agents have the policy available without reading AGENTS.md.
    """

    # =========================================================================
    # Methods
    # =========================================================================

    def resolve_profile(self, name: str) -> dict[str, Any]:
        """Return the merged profile dict for *name*, or an empty dict if not found.

        Always returns a plain dict — safe to call with arbitrary user input.

        Example::

            cfg.resolve_profile("work")
            # -> {"scope": "work", "retrieval_mode": "semantic_hybrid", "limit": 8}

            cfg.resolve_profile("nonexistent")
            # -> {}
        """
        return dict(self.profiles.get(name, {}))

    def __post_init__(self) -> None:
        self._ledger_notes_dir_explicit = self.ledger_notes_dir is not None
        self._source_notes_dir_explicit = self.source_notes_dir is not None
        self._finalize_paths()

    def _finalize_paths(self) -> None:
        self.ledger_root = Path(self.ledger_root).expanduser().resolve()
        if self._ledger_notes_dir_explicit and self.ledger_notes_dir is not None:
            self.ledger_notes_dir = Path(self.ledger_notes_dir).expanduser().resolve()
        else:
            self.ledger_notes_dir = self.ledger_root / "notes"
        if self._source_notes_dir_explicit and self.source_notes_dir is not None:
            self.source_notes_dir = Path(self.source_notes_dir).expanduser().resolve()
        else:
            self.source_notes_dir = _default_source_notes_dir().expanduser().resolve()

    @property
    def note_types(self) -> dict[str, dict[str, Any]]:
        """Mapping of note type names to their directories and labels."""
        return note_type_metadata()

    @property
    def core_note_types(self) -> tuple[str, ...]:
        """Core note types included in retrieval."""
        return CORE_NOTE_TYPES

    @property
    def signals_path(self) -> Path:
        """Path to signals JSONL."""
        return indices_dir(self.ledger_notes_dir) / "signals.jsonl"

    @property
    def signal_summary_path(self) -> Path:
        """Path to precomputed signal summary JSON."""
        return indices_dir(self.ledger_notes_dir) / "signal_summary.json"

    @property
    def aliases_path(self) -> Path:
        """Path to query aliases JSON."""
        return indices_dir(self.ledger_notes_dir) / "aliases.json"

    @property
    def timeline_path(self) -> Path:
        """Path to timeline markdown."""
        return layout_timeline_path(self.ledger_notes_dir)

    @property
    def timeline_jsonl_path(self) -> Path:
        """Path to machine-readable timeline JSONL."""
        return layout_timeline_jsonl_path(self.ledger_notes_dir)

    @property
    def semantic_root(self) -> Path:
        """Path to semantic index root."""
        return self.ledger_root / ".smart-env" / "semantic"

    @property
    def semantic_manifest_path(self) -> Path:
        """Path to semantic manifest JSON."""
        return indices_dir(self.ledger_notes_dir) / "semantic_manifest.json"

    def note_type_dir(self, note_type: str) -> Path:
        """Get the directory for a note type."""
        return note_type_path(self.ledger_notes_dir, note_type)

    @classmethod
    def from_env(cls) -> "LedgerConfig":
        """Load the user config file (if present) with env var overrides.

        Lookup order (later sources override earlier ones):
        1. ``$XDG_CONFIG_HOME/cognitive-ledger/config.yaml`` (deprecated; pre-rename)
        2. ``$XDG_CONFIG_HOME/ledger/config.yaml`` (canonical user config)
        3. Environment variables (LEDGER_ROOT, LEDGER_NOTES_DIR, etc.)

        A ``config.yaml`` inside the source checkout is intentionally NOT read:
        config belongs to the installation, not the codebase.
        """
        config = cls()
        config = _apply_yaml_config(config, _legacy_xdg_config_path())
        config = _apply_yaml_config(config, _xdg_config_path())
        return _apply_env_overrides(config)

    @classmethod
    def from_file(cls, path: Path) -> "LedgerConfig":
        """Load config from a YAML or JSON file with env var overrides on top."""
        config = cls()
        if path.suffix in (".yaml", ".yml"):
            config = _apply_yaml_config(config, path)
        elif path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    _fail_on_removed_config_keys(data, path)
                    _apply_dict(config, data)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                raise
        return _apply_env_overrides(config)


# =============================================================================
# Module-level singleton
# =============================================================================

import threading

_config: LedgerConfig | None = None
_config_lock = threading.Lock()


def get_config() -> LedgerConfig:
    """Get the global configuration singleton (thread-safe).

    Returns:
        The LedgerConfig instance, creating it if needed.
    """
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = LedgerConfig.from_env()
    return _config


def reset_config() -> None:
    """Reset the configuration singleton.

    Use this in tests to ensure clean state between test cases.
    """
    global _config
    _config = None


def set_config(config: LedgerConfig) -> None:
    """Set a custom configuration.

    Use this in tests to inject test-specific configuration.

    Args:
        config: The configuration to use.
    """
    global _config
    _config = config

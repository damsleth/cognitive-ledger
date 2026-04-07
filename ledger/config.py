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


def _get_root_dir() -> Path:
    """Determine the ledger root directory."""
    # Check environment variable first
    if env_root := os.getenv("LEDGER_ROOT_DIR"):
        return Path(env_root).resolve()
    # Default: parent of this package (cog-led repo root)
    return Path(__file__).resolve().parents[1]


def _get_source_root() -> Path:
    """Determine the source notes root for discovery."""
    if env_root := os.getenv("LEDGER_SOURCE_ROOT"):
        return Path(env_root).expanduser().resolve()
    return Path.home() / "notes"


def _apply_dict(config: "LedgerConfig", data: dict) -> None:
    """Apply a dict of key-value pairs to a LedgerConfig, coercing types."""
    for key, value in data.items():
        if not hasattr(config, key):
            continue
        # Skip read-only properties (e.g. notes_dir)
        if isinstance(getattr(type(config), key, None), property):
            continue
        current = getattr(config, key)
        if isinstance(current, Path):
            value = Path(str(value)).expanduser()
        elif isinstance(current, (tuple, frozenset)):
            continue  # skip complex types
        setattr(config, key, value)


def _apply_yaml_config(config: "LedgerConfig", path: Path) -> "LedgerConfig":
    """Apply config.yaml overrides if the file exists."""
    if not path.is_file():
        return config
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _apply_dict(config, data)
    except (ImportError, OSError, TypeError, ValueError):
        pass
    return config


def _apply_env_overrides(config: "LedgerConfig") -> "LedgerConfig":
    """Apply environment variable overrides to an existing config instance."""
    # Integer overrides
    int_mappings = {
        "LEDGER_SHORTLIST_MIN": "shortlist_min_candidates",
        "LEDGER_SHORTLIST_MAX": "shortlist_max_candidates",
        "LEDGER_ATTENTION_MIN": "attention_shortlist_min",
        "LEDGER_ATTENTION_MAX": "attention_shortlist_max",
        "LEDGER_REASONS_LIMIT": "detailed_reasons_limit",
    }
    for env_var, attr in int_mappings.items():
        if (value := os.getenv(env_var)) is None:
            continue
        try:
            setattr(config, attr, int(value))
        except ValueError:
            pass

    # Float overrides
    float_mappings = {
        "LEDGER_WEIGHT_BM25": "score_weight_bm25",
        "LEDGER_WEIGHT_LEXICAL": "score_weight_lexical",
        "LEDGER_WEIGHT_TAG": "score_weight_tag",
        "LEDGER_WEIGHT_SCOPE": "score_weight_scope",
        "LEDGER_WEIGHT_RECENCY": "score_weight_recency",
        "LEDGER_WEIGHT_CONFIDENCE": "score_weight_confidence",
    }
    for env_var, attr in float_mappings.items():
        if (value := os.getenv(env_var)) is None:
            continue
        try:
            setattr(config, attr, float(value))
        except ValueError:
            pass

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

    root_dir: Path = field(default_factory=_get_root_dir)
    """Root directory of the cognitive ledger repository."""

    source_root: Path = field(default_factory=_get_source_root)
    """Root directory for source notes (used in discovery mode)."""

    # =========================================================================
    # Note Type Configuration
    # =========================================================================

    note_types: dict[str, dict[str, Any]] = field(default_factory=lambda: {
        "identity": {"dir": "notes/01_identity", "label": "id"},
        "facts": {"dir": "notes/02_facts", "label": "fact"},
        "preferences": {"dir": "notes/03_preferences", "label": "pref"},
        "goals": {"dir": "notes/04_goals", "label": "goal"},
        "loops": {"dir": "notes/05_open_loops", "label": "loop"},
        "concepts": {"dir": "notes/06_concepts", "label": "concept"},
    })
    """Mapping of note type names to their directories and labels."""

    core_note_types: tuple[str, ...] = ("identity", "facts", "preferences", "goals", "loops", "concepts")
    """Core note types included in retrieval."""

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
        "compressed_attention",
        "scope_type_prefilter",
        "precomputed_index",
        "progressive_disclosure",
        "semantic_hybrid",
    )
    """Available retrieval modes."""

    embed_backends: tuple[str, ...] = ("local", "openai")
    """Available embedding backends."""

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
    # Methods
    # =========================================================================

    @property
    def notes_dir(self) -> Path:
        """Path to the notes directory.

        Overridable via LEDGER_NOTES_DIR env var to decouple note corpus
        from code root (e.g. for A/B testing against an external ledger).
        """
        if env_notes := os.getenv("LEDGER_NOTES_DIR"):
            return Path(env_notes).expanduser().resolve()
        return self.root_dir / "notes"

    @property
    def signals_path(self) -> Path:
        """Path to signals JSONL."""
        return self.notes_dir / "08_indices" / "signals.jsonl"

    @property
    def signal_summary_path(self) -> Path:
        """Path to precomputed signal summary JSON."""
        return self.notes_dir / "08_indices" / "signal_summary.json"

    @property
    def aliases_path(self) -> Path:
        """Path to query aliases JSON."""
        return self.notes_dir / "08_indices" / "aliases.json"

    @property
    def timeline_path(self) -> Path:
        """Path to timeline markdown."""
        return self.notes_dir / "08_indices" / "timeline.md"

    @property
    def timeline_jsonl_path(self) -> Path:
        """Path to machine-readable timeline JSONL."""
        return self.notes_dir / "08_indices" / "timeline.jsonl"

    @property
    def semantic_root(self) -> Path:
        """Path to semantic index root."""
        return self.root_dir / ".smart-env" / "semantic"

    @property
    def semantic_manifest_path(self) -> Path:
        """Path to semantic manifest JSON."""
        return self.notes_dir / "08_indices" / "semantic_manifest.json"

    def note_type_dir(self, note_type: str) -> Path:
        """Get the directory for a note type."""
        if note_type not in self.note_types:
            raise ValueError(f"Unknown note type: {note_type}")
        return self.root_dir / self.note_types[note_type]["dir"]

    @classmethod
    def from_env(cls) -> "LedgerConfig":
        """Load config from config.yaml (if present) with env var overrides."""
        config = cls()
        config_path = config.root_dir / "config.yaml"
        config = _apply_yaml_config(config, config_path)
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
                    _apply_dict(config, data)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                pass
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

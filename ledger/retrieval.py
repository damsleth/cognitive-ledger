"""Compatibility facade for the retrieval pipeline."""

from ledger.ranking.settings import (
    _cfg,
    resolve_retrieval_mode,
    resolve_embed_backend,
    now_utc,
)
from ledger.ranking.adapters import (
    _candidate_fields,
    _copy_candidate,
    _scored_result,
)
from ledger.ranking.prior import (
    resolve_confidence,
    compute_recency_component,
    compute_prior_score,
    prior_tiebreak_factor,
    apply_prior_tiebreak,
    attach_trust_verdicts,
)
from ledger.ranking.expansion import (
    load_aliases,
    expand_query_tokens,
)
from ledger.ranking.candidates import (
    _note_types,
    _candidate_from_parts,
    _candidate_from_json,
)
from ledger.ranking.index import (
    NOTE_INDEX_VERSION,
    load_note_index,
    rebuild_note_index,
    build_candidates,
    clear_candidate_cache,
    build_candidates_with_archive,
    build_candidate_index,
    retrieve_candidates_from_index,
)
from ledger.ranking.filters import (
    apply_temporal_filter,
    apply_changed_since_filter,
    prefilter_candidates_by_scope_and_type,
)
from ledger.ranking.lexical import (
    coarse_candidate_score,
    shortlist_candidates,
    score_candidate,
    apply_progressive_disclosure,
    rank_lexical,
    related_to_text,
)
from ledger.ranking.telemetry import (
    _maybe_log_query,
)
from ledger.scoring import canonical_scope, scope_matches

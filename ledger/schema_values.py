"""Canonical enum values from schema.yaml.

Single source of truth for the controlled vocabularies that lint, validation,
and query filters all need to share. If schema.yaml changes, this module is the
one place to update; downstream callers import from here.
"""

from __future__ import annotations

SOURCE_VALUES: frozenset[str] = frozenset({"user", "tool", "assistant", "inferred"})
SCOPE_VALUES: frozenset[str] = frozenset({"home", "work", "dev", "personal", "life", "meta"})
LANG_VALUES: frozenset[str] = frozenset({"en", "no", "mixed"})
STATUS_VALUES: frozenset[str] = frozenset({"open", "closed", "blocked", "snoozed"})

# Provenance triad: optional ingest-channel metadata, distinct from the
# epistemic `source`. `source` is who/what the fact came from; `via` is
# which pipeline carried it into the ledger. `origin` locates the upstream
# artifact and `external_id` is a stable upstream key enabling idempotent
# re-import. See schema.yaml (frontmatter.optional) and
# docs/claude-memory-import.md.
VIA_VALUES: frozenset[str] = frozenset({"claude-memory", "obsidian", "yaams", "manual", "mcp"})
PROVENANCE_FIELDS: frozenset[str] = frozenset({"via", "origin", "external_id"})

# `provenance:` frontmatter field: origin-act trust class (plan 42). Not to be
# confused with PROVENANCE_FIELDS above (the ingest-channel triad).
PROVENANCE_VALUES: frozenset[str] = frozenset(
    {"explicit_statement", "validated", "corrected", "observed", "imported", "inferred"}
)
IDENTITY_TYPE_VALUES: frozenset[str] = frozenset(
    {"mission", "beliefs", "models", "strategies", "narratives", "voice"}
)

# schema.yaml `limits`. Identity: a cap, not one-per-type (decided 2026-10-14).
MAX_TAGS = 10
MAX_IDENTITY_NOTES = 6

# Query-time scope filter accepts "all" as a wildcard in addition to SCOPE_VALUES.
QUERY_SCOPE_VALUES: frozenset[str] = SCOPE_VALUES | {"all"}

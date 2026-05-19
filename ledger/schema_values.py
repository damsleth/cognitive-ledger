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

# Query-time scope filter accepts "all" as a wildcard in addition to SCOPE_VALUES.
QUERY_SCOPE_VALUES: frozenset[str] = SCOPE_VALUES | {"all"}

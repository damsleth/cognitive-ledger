---
# Template: Generic (atomic) note
#
# Use this template for facts, preferences, goals, concepts and other
# standalone pieces of information.  Each file should capture a single idea
# or claim.  Do not duplicate content: search the repository (e.g. with
# `rg` or `fd`) before creating a new note.  Always include the frontmatter.
#
# Frontmatter fields:
# - created: ISO 8601 timestamp when the note was first written.
# - updated: ISO 8601 timestamp of the most recent modification.
# - tags:    List of lowercase keywords (no spaces) for grouping and search.
# - confidence: A number in [0.0, 1.0] reflecting certainty; <0.7 indicates
#               a hypothesis rather than a fact.
# - source:  'user', 'assistant', 'tool' or 'inferred'.  Facts should only
#             originate from 'user' or 'tool'.  Hypotheses may originate
#             from 'assistant' or be 'inferred'.
---
created: 2026-01-20T00:00:00Z
updated: 2026-01-20T00:00:00Z
tags: [example]
confidence: 0.9
source: user
scope: meta
lang: mixed
---

# Title

## Statement
One clear, atomic claim or idea. One note = one idea.

## Context
Why this matters, where it came from, or what problem it relates to.

## Implications
- How this should influence future decisions, behavior, or reasoning.

## Links
- Related notes (relative links only). e.g. `../06_concepts/concept__cognitive_lightcone.md`.

---

Notes:
- Write in the language that best preserves meaning.
- Do not translate unless translation reduces ambiguity.
- Search for existing notes before creating a new one.
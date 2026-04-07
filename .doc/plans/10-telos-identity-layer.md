# TELOS-Inspired Identity Layer

## Problem

The ledger captures *what* the user prefers and *what* they're working on, but
lacks a structured representation of *who they are* — their mission, beliefs,
mental models, strategies, and narratives. Agents currently reconstruct this
from scattered facts, preferences, and goals, which is fragile and
context-expensive. PAI's TELOS system (10 identity documents) solves this by
giving agents a compact, high-signal identity scaffold to consult before diving
into granular notes.

We don't need all 10 TELOS docs — some overlap with existing note types. The
goal is to adopt the *useful* parts without duplicating what `04_goals/` and
`03_preferences/` already handle.

## Design

### What to adopt (and what maps to existing types)

| TELOS doc        | Adopt? | Rationale                                                      |
| ---------------- | ------ | -------------------------------------------------------------- |
| MISSION.md       | Yes    | No equivalent. A single north-star statement.                  |
| BELIEFS.md       | Yes    | Core worldview axioms. Distinct from preferences or facts.     |
| MODELS.md        | Yes    | Mental models / frameworks. Maps to `06_concepts/` but these   |
|                  |        | are *personal* models, not general definitions.                |
| STRATEGIES.md    | Yes    | Recurring decision heuristics ("when X, I do Y").              |
| NARRATIVES.md    | Yes    | Self-stories and framing. Useful for tone/voice calibration.   |
| LEARNED.md       | Merge  | Lessons learned → already covered by facts + closed loops.     |
|                  |        | Add a `learned` tag convention instead of a separate doc.      |
| GOALS.md         | No     | Already `notes/04_goals/`                                      |
| PROJECTS.md      | No     | Already `notes/07_projects/`                                   |
| CHALLENGES.md    | No     | Already expressible as open loops with `blocked` status        |
| IDEAS.md         | No     | Already expressible as open loops or inbox notes               |

### New note type: `identity`

```yaml
# schema.yaml addition
identity:
  prefix: "id__"
  folder: "notes/01_identity"
  description: "Core identity documents: mission, beliefs, models, strategies, narratives"
  extra_fields: [identity_type]
```

New frontmatter field:

```yaml
identity_type: mission | beliefs | models | strategies | narratives
```

### Folder structure

```
notes/01_identity/
  id__mission.md            # single file — "why I do what I do"
  id__beliefs.md            # axioms and worldview
  id__mental_models.md      # personal frameworks (not general concepts)
  id__strategies.md         # decision heuristics
  id__narratives.md         # self-stories, framing, voice
```

### File format

Same frontmatter as all other notes (created, updated, tags, confidence,
source, scope, lang) plus `identity_type`. Content is free-form markdown,
but each file should stay under the 400-word limit.

Example `id__mission.md`:

```yaml
---
created: 2026-04-07T12:00:00Z
updated: 2026-04-07T12:00:00Z
tags: [identity, mission]
confidence: 0.9
source: user
scope: personal
lang: en
identity_type: mission
---

## Mission

<user's north-star statement>

## Why this matters

<brief context>
```

### Relationship to existing notes

- Identity notes are **not** atomic in the "one idea per file" sense — they
  are curated summaries. Think of them as *indices over beliefs* rather than
  individual beliefs.
- `06_concepts/` remains for general frameworks (e.g. "concept__cognitive_lightcone.md").
  `01_identity/id__mental_models.md` is for *personal* models the user applies
  to their own decisions.
- Identity notes should cross-link to supporting facts/preferences where
  relevant.

### Retrieval integration

- Identity notes get a **high base score boost** in retrieval — they are
  almost always relevant context.
- `build_context_profiles.py` should include a summary of identity notes at
  the top of every context profile.
- The `/notes` skill boot sequence should load identity notes early
  (they're small and high-signal).

## Plan

### Step 1: Schema and folder

1. Create `notes/01_identity/` directory
2. Add `identity` type to `schema.yaml` with `identity_type` field
3. Add `identity_type` enum to `schema.yaml` frontmatter enums
4. Update `CORE_NOTE_TYPES` in `ledger/notes/__init__.py`

### Step 2: Config and retrieval

1. Add `identity` to `LedgerConfig` note type mappings in `config.py`
2. Add identity score boost constant (e.g. `identity_boost: 0.15`) to config
3. Update `retrieval.py` candidate construction to include identity notes
4. Apply identity boost during scoring (additive to final score)

### Step 3: Context profile integration

1. Update `ledger/context.py` `collect_profile_items()` to include identity notes
2. Update `scripts/build_context_profiles.py` to emit identity summary at top
3. Ensure `context.md` includes identity section before scope-specific content

### Step 4: Validation and linting

1. Update `sheep lint` to validate `identity_type` field on identity notes
2. Update `validation.py` to accept `01_identity` as a valid path
3. Add identity notes to timeline tracking

### Step 5: Skill and agent integration

1. Update `skills/notes/SKILL.md` to document identity note type and when to
   create/update them
2. Update `AGENTS.md` boot sequence to include identity note loading
3. Add identity trigger: "when the user expresses a core belief, mission
   change, or strategic heuristic, persist to identity layer"

### Step 6: Seed initial content

1. Review existing `06_concepts/`, `03_preferences/`, and `04_goals/` for
   content that belongs in identity notes
2. Draft initial identity notes collaboratively with user
3. Cross-link identity notes to supporting artifacts

## Verification

```bash
rg "identity" schema.yaml                          # type registered
fd "id__" notes/01_identity                         # files exist
./scripts/ledger query "mission" --scope personal   # identity notes surface
./.venv/bin/pytest tests/ -q                        # nothing broken
./scripts/sheep lint                                # identity notes pass
```

## Effort

~2 sessions. Schema/config/retrieval changes are mechanical. The real work is
collaboratively drafting the initial identity notes with the user.

## Risks

- **Staleness**: Identity notes are useless if not maintained. Mitigation:
  Electric Sheep should flag identity notes older than 90 days for review.
- **Scope creep**: Keep to 5 files max. If a document grows past 400 words,
  it should be split or items should be promoted to proper atomic notes.
- **Overlap with concepts**: Clear convention needed — identity = personal
  axioms, concepts = general frameworks. Document in AGENTS.md.

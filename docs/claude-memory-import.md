# Importing Claude Code memory into the ledger

Claude Code keeps a per-project memory store under
`~/.claude/projects/<encoded-cwd>/memory/` — one atomic markdown note per
fact, plus a `MEMORY.md` index. Those notes are already Tier-2-shaped
(curated, one-idea-per-file, deliberately distilled), so they belong in
the cognitive ledger rather than the YAAMS firehose. `ledger
import-claude-memory` maps them onto the ledger note schema.

> Once they are in the ledger, YAAMS picks them up for unified search via
> its existing `tier2_ledger` adapter — there is no need (and no benefit)
> to add a separate Claude-memory adapter to the firehose, which would
> double-count the same facts.

## Usage

```bash
# Dry run (default): writes nothing, prints the full mapping table plus
# a few rendered example notes. This is what you review first.
ledger import-claude-memory

# Render more/less example notes in the report
ledger import-claude-memory --preview 8

# Point at a non-default memory root
ledger import-claude-memory --memory-root ~/.claude/projects

# Actually write. Default lands notes in 00_inbox/ for triage.
ledger import-claude-memory --apply

# Write straight to the typed folders (02_facts/, 03_preferences/, ...)
ledger import-claude-memory --apply --direct

# Machine-readable summary
ledger import-claude-memory --json
```

Re-import is idempotent: a JSON state file
(`08_indices/claude_memory_import_state.json`) records each source file's
sha1, so unchanged files are skipped on the next run.

## Mapping

The 4-value Claude taxonomy does not map one-to-one onto the 6 ledger
types, so `project`/`reference` are refined with title/body markers. The
dry-run report prints the chosen type and the reason for every file.

| Claude `metadata.type` | ledger type | ledger `source` | rule |
| --- | --- | --- | --- |
| `feedback` | `preferences` | `user` | guidance on how the agent should work |
| `user` | `facts` | `user` | a stable truth about the user |
| `reference` | `facts` (or `concepts`) | `assistant` | durable pointer/config; `concepts` if the title/body reads like a definition/framework |
| `project` | `loops` / `concepts` / `facts` | `assistant` | open-work markers → `loops`; architecture/philosophy → `concepts`; else `facts` |

Marker precedence (highest first): concept-in-title → project open-work →
concept-in-body → type default.

### Synthesized frontmatter

Claude memory files carry none of the ledger's required fields, so the
importer synthesizes them:

- `created` / `updated` — from the source file's birthtime / mtime.
- `tags` — `[imported, claude-memory, <claude_type>, <project>]`.
- `confidence` — `0.85` (`0.8` for loops); these are curated notes.
- `scope` — inferred from the encoded cwd folder: a path containing
  `code` → `dev`; the bare home root or `.claude` tree → `meta`;
  otherwise `personal`. **Scope is the least certain field — review it.**
- `lang` — detected (`en` / `no` / `mixed`).

### Provenance

Every imported note records where it came from, both in a `## Provenance`
body section and in three frontmatter fields (the *provenance triad*,
below). `[[wikilinks]]` in the source body are preserved verbatim.

## Schema proposal — making the ledger a clean sink for both pipelines

The ledger now receives notes from at least three external channels:
`ledger import obsidian import`, `ledger import-claude-memory`, and (via YAAMS
`promote`) the firehose. Today each invents its own provenance
convention. The following additive, non-breaking schema changes make
external ingestion first-class and serve **both** YAAMS and Claude-memory
equally.

1. **Provenance triad (new optional frontmatter).** Keep `source` as the
   *epistemic* origin (who the fact came from) and add a separate channel
   dimension:
   - `via` — the ingest channel that carried the note in
     (`claude-memory` | `obsidian` | `yaams` | `manual`). Do **not**
     overload `source` for this; "the user told me" and "it arrived via
     the firehose" are orthogonal.
   - `origin` — a locator for the upstream artifact (file path / URI).
   - `external_id` — a stable upstream key (e.g. `<project>/<name>`) so
     re-import can match-and-update instead of duplicating, and so a note
     can be traced back to (or kept in sync with) its source.

   The importer already writes these three fields; this proposal just
   documents them in `schema.yaml` and `schema_values.py` so lint
   recognizes them rather than merely tolerating them.

2. **`external_type_map` in `schema.yaml`.** A single documented table of
   how each external taxonomy maps onto ledger types, so every importer
   agrees instead of hard-coding its own mapping.

3. **Recognized tags.** Add `imported` and `claude-memory` to
   `recommended_tags` (alongside the existing `ingested` /
   `auto-capture` / `synthesized`).

### The strategic point

Native Claude memory is siloed per project, with no embeddings, no
consolidation, and no retrieval ranking — everything the ledger already
does. So the durable plan is for the ledger (and its `/notes` skill) to
**supersede** native memory: treat this importer as a one-time/periodic
bootstrap, and pick a single canonical writer to avoid dual-write drift
between `~/.claude/projects/*/memory/` and the ledger. The `external_id`
field is what makes that migration path idempotent.

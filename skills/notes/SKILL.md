---
name: notes
description: Capture notes and maintain structured long-term memory. Writes human-readable notes to Obsidian and syncs durable facts, preferences, decisions, goals, and open loops to a cognitive ledger. Asks targeted questions before writing. Use when the user wants to take notes, log decisions, capture ideas, plan, journal, or remember something.
license: WTFPL
metadata:
  defaults:
    mode: notes+ledger
    auto_write: true
    write_silently: true
    show_git_diff: false
---

# Notes

Capture notes across two stores:

- **Source notes** (Obsidian): `{source_notes_dir}`
- **Ledger notes** (atomic memory): `{ledger_notes_dir}`
- **Ledger repo** (scripts, config): `{ledger_root}`

## Path Resolution

Use ONLY the Read tool (no Bash) to read `~/.config/cognitive-ledger/config.yaml`.
This is a symlink to the repo's config and contains all three paths:

```yaml
ledger_root: ~/Code/cognitive-ledger
ledger_notes_dir: ~/Code/ledger-notes
source_notes_dir: ~/Code/notes
```

Expand `~` to the user's home directory. Use these resolved paths for ALL operations.

**If the file doesn't exist:** ask the user for their `ledger_root` path, read `{ledger_root}/config.yaml`, and create the symlink:
`ln -sf {ledger_root}/config.yaml ~/.config/cognitive-ledger/config.yaml`

**Critical path rule:** Note subfolders (`01_identity/`, `02_facts/`, etc.) live directly under `{ledger_notes_dir}`. Never prepend `notes/` - e.g., use `{ledger_notes_dir}/02_facts/`, NOT `{ledger_notes_dir}/notes/02_facts/`.

## Boot (Run on Activation)

1. Read `{ledger_notes_dir}/08_indices/context.md` - essential facts, active loops, preferences
2. Read `{ledger_notes_dir}/01_identity/id__voice_dna.md` if it exists - apply voice profile
3. Run `./scripts/sheep status` from `{ledger_root}` - check maintenance status

Use `context.md` for boot context. Use `{ledger_notes_dir}/08_indices/index.md` only for deeper lookups (do NOT load at boot).

Use literal resolved paths in all commands - never `$VAR` syntax, which triggers permission prompts.

## Workflow

1. **Classify**: Detect intent (meeting, decision, idea, plan, journal, preference, etc.) and choose write mode:
   - `notes+ledger` (default): human note + ledger sync
   - `ledger-only`: "just log this", "remember that..." - skip the note
   - `notes-only`: "just draft this" - skip ledger sync
2. **Ask**: Only what's missing, max 5 questions. Use `references/question-playbook.md`. Skip if input is complete.
3. **Route**: Search `{source_notes_dir}` for existing notes first. Prefer update over create. Use playbook for folder routing.
4. **Write**: Concise, scannable. Include decisions, next steps, owners, dates. Read `{source_notes_dir}/AGENTS.md` if exists.
5. **Sync**: Distill durable items to ledger using the routing table below. One idea per file.
6. **Summarize**: List changed files briefly. "Logged." is fine when obvious.

---

## Ledger Operations

**Defaults:** Auto-write ON, silent writes ON. Audit with `git diff` internally; only show if asked.

### Core Rules

- Search before writing (`rg` + `fd`). Update existing notes over creating new ones.
- One idea per file. No chat transcripts. Never invent facts (`source: inferred`, `confidence < 0.7`).
- Always bump `updated` timestamp. Always append to `{ledger_notes_dir}/08_indices/timeline.md`.
- Read `{ledger_root}/AGENTS.md`, `schema.yaml`, or `templates/` when guidance is needed.

### Routing Table

All paths below are relative to `{ledger_notes_dir}`:

| Intent | Example phrases | Path |
|--------|----------------|------|
| Identity / mission / beliefs | "My mission is...", "I believe..." | `01_identity/id__{slug}.md` |
| Stable preference | "I prefer...", "Going forward..." | `03_preferences/pref__{slug}.md` |
| Decision / commitment | "We'll do X", "I decided..." | `02_facts/fact__{slug}.md` |
| Goal / objective | "I want to achieve..." | `04_goals/goal__{slug}.md` |
| New concept / definition | "Let's define...", "The idea is..." | `06_concepts/concept__{slug}.md` |
| Unresolved / revisit later | "We should revisit...", "Not sure yet..." | `05_open_loops/loop__{slug}.md` |

Multiple triggers = multiple small notes. Open loops require `status` + a `Next action` checkbox.

**Timeline entries** use logical paths (e.g., `02_facts/fact__x.md`) as identifiers:
`{ISO timestamp} | created|updated|archived|deleted | {path} | {description}`

### Create or Update

1. Search: `rg "<topic>" {ledger_notes_dir} -l` and `fd "<type>__" {ledger_notes_dir}`
2. Path: `{ledger_notes_dir}/{folder}/{type}__{slug}.md`
3. Write frontmatter (see `schema.yaml`) + content (see `templates/`)
4. Append timeline entry

### Signals

Capture only with clear evidence, not speculatively:
```
./scripts/ledger signal add --type retrieval_hit --query "<q>" --note <path>
./scripts/ledger signal add --type correction --note <path> --detail "<detail>"
./scripts/ledger signal add --type affirmation --note <path>
```

### Query (Three-Layer Retrieval)

Start with the compact index view, then drill into detail only for what you need:

```
# Layer 1 - Index: scan results, see cost per note (~20-30 tokens each)
./scripts/ledger query "<topic>" --scope all --limit 8 --view index

# Layer 2 - Context (default): statements, snippets, tags (~80-120 tokens each)
./scripts/ledger query "<topic>" --scope all --limit 8

# Layer 3 - Detail: full bodies, score components (~200-1000 tokens each)
./scripts/ledger query "<topic>" --scope all --limit 8 --view detail

# Bundle mode (context-window-friendly excerpts within a word budget)
./scripts/ledger query "<topic>" --scope dev --bundle
```

**Workflow:** Start with `--view index` to see what's available and how large each note is. Then fetch `--view detail` for only the items you need. This saves tokens on broad queries.

For targeted follow-up use `rg` in the relevant subfolder of `{ledger_notes_dir}`:
`02_facts/` (history, decisions), `03_preferences/` (style), `05_open_loops/` (threads), `06_concepts/` (frameworks).

**Rule:** If about to guess something about the user, search the ledger first.
Do not create files for pure queries unless explicitly asked.

---

## Auxiliary Operations

**Ingest** ("ingest this", "distill this"): `./scripts/ledger ingest scan`, read source, create 3-8 atomic notes tagged `ingested`, then `./scripts/ledger ingest record <source> <notes...>`.

**Briefing** ("brief me", "what's on my plate", "morning"): `./scripts/ledger briefing` (daily) or `--weekly`.

**Answer Filing**: If a query synthesizes 2+ notes into new insight, ask before filing as `concept__` or `fact__` note (`source: assistant`, `confidence: 0.8`). Never silently write during queries.

**Things3** (planning/triage): `things-cli today`, `things-cli upcoming`, `things-cli deadlines`. Add `-j` for JSON. Filter with `--filter-area`, `--filter-project`, `--filtertag`.

**Maintenance**: Run `./scripts/sheep status` before writes. If sleep is due, run `./scripts/sheep sleep` and `./scripts/sheep index` first. Use `./scripts/sheep sync --check` for drift. Skip for pure queries.

**Working Directory**: `cd` to `{ledger_root}` for ledger operations, then return. Never modify unrelated repos.

## Session Wrap-Up

At session end, prompt the user with these five questions to surface durable artifacts:

1. **What was the task?** - File as `fact__` if it was a decision, or update an existing `loop__` if it was ongoing work.
2. **What was explored?** - Only file if the exploration path itself is reusable knowledge (e.g., "X approach doesn't work because Y").
3. **What was discovered?** - File as `fact__` (stable truth), `concept__` (reusable framework), or `pref__` (validated preference).
4. **What was completed?** - Update existing `loop__` status to `closed`, or append a timeline entry with appropriate `activity_type`.
5. **What's still open?** - File as `loop__` with `status: open` and a concrete next-action checkbox.

Not every question produces a note. Skip any that yield nothing durable. Search the ledger first - prefer updating existing notes over creating new ones.

## Safety

- No invented facts. No external data without `source: inferred`.
- Atomic, auditable changes. Ask when scope is ambiguous.
- Ask before mass-creating notes; prefer one canonical note + atomic ledger updates.

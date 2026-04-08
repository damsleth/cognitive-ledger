---
name: notes
description: Capture notes and maintain structured long-term memory. Writes human-readable notes to Obsidian and syncs durable facts, preferences, decisions, goals, and open loops to a cognitive ledger. Asks targeted questions before writing. Use when the user wants to take notes, log decisions, capture ideas, plan, journal, or remember something.
license: WTFPL
metadata:
  source_notes_dir: "<resolve-from-config>"
  ledger_root: "<resolve-from-config>"
  ledger_notes_dir: "<resolve-from-config>"
  defaults:
    mode: notes+ledger
    auto_write: true
    write_silently: true
    show_git_diff: false
---

# Notes

Capture notes and maintain structured long-term memory across two repositories:

- **Notes** (human-facing, Obsidian): `$LEDGER_SOURCE_NOTES_DIR`
- **Ledger Root** (repo, config, scripts): `$LEDGER_ROOT`
- **Ledger Notes** (atomic, searchable memory): `$LEDGER_NOTES_DIR`

> **Setup:** Set `LEDGER_SOURCE_NOTES_DIR`, `LEDGER_ROOT`, and `LEDGER_NOTES_DIR` environment variables or edit the defaults in the frontmatter above.

### Path Resolution

The only value that must be bootstrapped is `LEDGER_ROOT`. Everything else comes from `config.yaml` inside it.

**To find `LEDGER_ROOT`**, try in order:
1. Current working directory contains `config.yaml` - use CWD as `LEDGER_ROOT`
2. `$LEDGER_ROOT` env var is set - use that
3. Ask the user for the path to the cognitive-ledger repo

**Once `LEDGER_ROOT` is known**, check whether `$LEDGER_ROOT/config.yaml` exists:

- **If it exists** - Read it with the Read tool and extract `ledger_notes_dir` and `source_notes_dir`.
- **If it does not exist** - Onboarding flow:
  1. Tell the user `config.yaml` is missing and that you'll create it from `config.sample.yaml`.
  2. Ask for the three paths in one batch:
     - `ledger_root` - path to this repo
     - `ledger_notes_dir` - path to the atomic ledger notes directory
     - `source_notes_dir` - path to the human-facing Obsidian notes directory
  3. Read `$LEDGER_ROOT/config.sample.yaml`, substitute the provided values, and write the result to `$LEDGER_ROOT/config.yaml`.
  4. Confirm the file was created before continuing.

Expand any `~` to the user's home directory. All subsequent operations use these resolved paths.

## Boot Sequence (Run on Activation)

1. `cd $LEDGER_ROOT`
2. Read `$LEDGER_NOTES_DIR/08_indices/context.md` - essential facts, active loops, key preferences
3. Run `./scripts/sheep status` - check if maintenance needed
4. If `$LEDGER_NOTES_DIR/01_identity/id__voice_dna.md` exists, read it - apply voice profile when writing notes longer than 2 sentences

**Two-tier lookup strategy:**

- `context.md` for boot (compact summary, always loaded)
- `$LEDGER_NOTES_DIR/08_indices/index.md` or `index.json` as a lightweight lookup table for deeper searches (do NOT load at boot)

## Write Modes

- **`notes+ledger`** (default): Write a human-readable note AND sync durable memory to the ledger.
- **`ledger-only`**: Skip the note, write directly to the ledger. Use when the user says "just log this", "remember that…", or the input is a bare preference/fact with no note value.
- **`notes-only`**: Write the note, skip ledger sync. Use when the user says "just draft this" or the content is ephemeral with no durable memory.

Infer the mode from context. When in doubt, default to `notes+ledger`.

## Workflow

### 1. Classify the Request

- Detect intent: meeting, project update, decision, idea, plan, journal, reference, preference, or cleanup.
- Choose write mode based on the signals above.

### 2. Ask Targeted Questions Before Writing

- Ask only what is missing. Use at most 5 questions in one batch.
- Pull prompts from `references/question-playbook.md`.
- Prefer specific prompts over generic "anything else?" prompts.
- Skip questions entirely when the user's input is already complete.

### 3. Choose Note Destination in `$LEDGER_SOURCE_NOTES_DIR`

- Route using `references/question-playbook.md`.
- Search for existing notes first: `rg "<topic>" $LEDGER_SOURCE_NOTES_DIR -l`
- Prefer updating over creating duplicates.
- When uncertain between two folders, propose one default and ask once.

### 4. Write or Update the Note

- Keep content concise and scannable.
- Use practical headings; avoid template bloat.
- Include decisions, next steps, owners, and dates when present.
- Read `$LEDGER_SOURCE_NOTES_DIR/AGENTS.md` before first write if context is unclear (if it exists).

### 5. Sync Durable Items to the Ledger

Follow the ledger operations below for all writes to `$LEDGER_ROOT` and `$LEDGER_NOTES_DIR`.

- Distill only durable memory from the note; do not copy full note text.
- Map extracted items using the Intent → Artifact table below.
- Handle timeline updates and frontmatter per ledger conventions.

### 6. Return a Short Completion Summary

- List files changed in both repositories.
- Highlight unresolved questions that blocked full sync.
- Keep it minimal. "Logged." is fine when context is obvious.

---

## Ledger Operations

### Defaults (Auto-write + Silent)

- **Auto-write: ON** — infer the correct ledger artifact(s) from user intent and write them without requiring jargon like "atomic note" or "open loop".
- **Silent writes: ON** — do not show `git diff` or verbose write logs by default.
- **Audit internally** — run `git status` / `git diff` to sanity-check changes, but only surface diffs if the user asks.

### Source of Truth

Read these before writing (when guidance is needed; keep reads minimal):

- `AGENTS.md` for operating rules, triggers, and quick reference
- `schema.yaml` for frontmatter and enums
- `templates/` for note structure
- `$LEDGER_NOTES_DIR/08_indices/README.md` and `$LEDGER_NOTES_DIR/08_indices/sleep_playbook.md` for indices/sleep

### Core Rules

- Search before writing. Prefer updating an existing note over creating a new one.
- Keep one idea per file; no chat transcripts.
- Never invent facts. Use `source: inferred` + `confidence < 0.7` if unsure.
- Always bump `updated` on edits.
- Always append to `notes/08_indices/timeline.md` after any note operation.
- Persist ledger note identifiers as logical `notes/...` paths even if `$LEDGER_NOTES_DIR` lives outside `$LEDGER_ROOT`.

### Intent → Artifact Mapping (Routing Table)

Infer ledger actions from natural language. Check this table first:

- **Identity / mission / beliefs** ("My mission is…", "I believe…", "My strategy for…", "I see myself as…") → `notes/01_identity/id__*.md`
- **Stable preference** ("I prefer…", "I want you to…", "Going forward…") → `notes/03_preferences/pref__*.md`
- **Decision / commitment** ("We'll do X", "I decided…") → `notes/02_facts/fact__*.md` or `notes/04_goals/goal__*.md`
- **New concept / definition** ("Let's define…", "The idea is…") → `notes/06_concepts/concept__*.md`
- **Unresolved / revisit later** ("We should revisit…", "Not sure yet…") → `notes/05_open_loops/loop__*.md`

If multiple triggers occur, write multiple small notes rather than one large note.

### Signal Capture

After using a ledger note in a response, or when the user gives feedback:

```bash
./scripts/ledger signal add --type retrieval_hit --query "<query>" --note <path>
./scripts/ledger signal add --type correction --note <path> --detail "<what's wrong>"
./scripts/ledger signal add --type affirmation --note <path>
```

Only capture when there is clear evidence. Do not log signals speculatively.

### Create or Update an Atomic Note

1. Search: `rg "<topic>" $LEDGER_NOTES_DIR -l` and `fd "<type>__|<slug>" $LEDGER_NOTES_DIR`
2. Choose type and path: `notes/{folder}/{type}__{slug}.md`
3. Use the appropriate template as reference.
4. Write/update frontmatter and content.
5. Append timeline entry: `{ISO timestamp} | created|updated|archived|deleted | {path} | {description}`
6. Verify internally with git diff (do not show unless asked).

### Create or Update an Open Loop

- File location: `notes/05_open_loops/loop__{slug}.md`
- Require `status` and a concrete `Next action` checkbox.
- Append a timeline entry for create/update/close/snooze.

### Query and Summarize

- Start with ranked retrieval:
  - `./scripts/ledger query "<topic>" --scope all --limit 8`
  - Use `--scope personal|work|dev|home|meta` to constrain.
  - Use `--bundle` for compact citation-backed context packs.
- Then use `rg` and `fd` for targeted follow-up.
- Read only the minimum set needed.
- Provide synthesis with citations to note paths.
- Do not create or update files for pure queries unless the user explicitly asks to log the summary.

**When to search deeper during conversation:**

- Personal details, history, family → `rg "<topic>" $LEDGER_NOTES_DIR/02_facts/`
- Past decisions or commitments → `rg "<topic>" $LEDGER_NOTES_DIR/02_facts/` + check timeline
- User preferences or style → `rg "<topic>" $LEDGER_NOTES_DIR/03_preferences/`
- Ongoing threads or open questions → `rg "<topic>" $LEDGER_NOTES_DIR/05_open_loops/`
- Defined concepts or frameworks → `rg "<topic>" $LEDGER_NOTES_DIR/06_concepts/`

**Rule:** If about to guess or assume something about the user, search the ledger first.

---

## Ingest (Source Distillation)

If the user says "ingest this", "process this article/meeting/doc", or
"distill this source":

1. Run `./scripts/ledger ingest scan` to check source state
2. Read the source content
3. Create 3-8 atomic notes (one idea per file, proper frontmatter)
4. Tag all notes with `ingested`
5. Run `./scripts/ledger ingest record <source> <note1> [note2...]`

## Answer Filing (Knowledge Compounding)

After synthesizing a query answer that drew from 2+ notes AND produced
new insight not in any single source:

- **Ask the user**: "This synthesis connects ideas that weren't linked before.
  Want me to file it as a concept note?"
- If the user confirms (or `auto_file_synthesis: true` in config.yaml):
  create a `concept__` or `fact__` note, tag it `synthesized`, link to all
  source notes, set `source: assistant`, `confidence: 0.8`
- **Do not silently write during pure queries** - the read-only query contract
  is preserved unless the user explicitly opts in.

## Briefing (Proactive Awareness)

If the user says "what's on my plate", "brief me", "what needs doing",
"morning", "status", or "what should I work on":

```bash
./scripts/ledger briefing           # daily (default)
./scripts/ledger briefing --weekly  # extended weekly review
```

The briefing surfaces:

- Open loops sorted by staleness with nudges
- Recent changes from the last 24h
- Inbox items needing triage
- Maintenance status
- Motivational progress tracking

## Things3 Task Intake (When Planning)

If the user asks for help planning their week/day, or to triage what's due:

- Default (human-readable): `things-cli today`, `things-cli upcoming`
- Optional: `things-cli deadlines` (all due dates), `things-cli anytime` (full backlog)
- For structure: `things-cli -j today`, `things-cli -j upcoming`, `things-cli -j deadlines`
- Filters: `things-cli anytime --filter-area "Area"`, `--filter-project "Project"`, `--filtertag "tag"`

Pull tasks early in planning conversations so the plan is grounded in actual due tasks.

---

## Electric Sheep (Maintenance)

- Check status: `./scripts/sheep status`
- Sync drift check: `./scripts/sheep sync --check`
- Refresh sync baseline: `./scripts/sheep sync --apply`
- Checklist only: `./scripts/sheep sleep`
- Validate: `./scripts/sheep lint`
- Regenerate indices: `./scripts/sheep index`

### Automatic Maintenance Policy

Before any operation that writes to the ledger:

1. Run `./scripts/sheep status`.
2. If status indicates sleep is due, run `./scripts/sheep sleep` and `./scripts/sheep index` before proceeding.
3. Do not run sleep for pure query operations.

---

## Indices

- Regenerate with `./scripts/sheep index` when asked.
- Treat regenerated indices as note updates and log them in the timeline.
- Use JSON indices for machine consumption when relevant.

---

## Working Directory

The ledger repo lives at `$LEDGER_ROOT`. The ledger note corpus lives at `$LEDGER_NOTES_DIR`. Human notes live at `$LEDGER_SOURCE_NOTES_DIR`.

- If the user is working in another repository, temporarily `cd` to the target repo for operations, then return to the original working directory.
- Never modify unrelated repositories as part of note/ledger operations.

## Session Wrap-Up (Passive Capture)

At the end of any session that created/modified/deleted files, even when
`/notes` was never explicitly invoked:

1. Scan your actions for durable artifacts:
   - Decisions made (e.g. chose library X over Y)
   - Preferences expressed (e.g. "always use TypeScript")
   - New patterns established (e.g. new architectural convention)
   - Configuration choices (e.g. enabled feature flag)
   - Problems diagnosed and solutions applied
2. For each: search ledger first (`rg`), then create/update the appropriate note type
3. If a multi-step task was completed: file a fact note summarizing what + why
4. If work was left incomplete: create/update an open loop with a concrete next action

**When to capture (heuristics):**

- Commit messages containing decisions or rationale
- New architectural patterns established
- Configuration choices made
- Problems diagnosed and solutions applied

## Safety

- Use only repo data and user input; do not invent or import external facts.
- Keep changes atomic and auditable.
- Ask when scope or intent is ambiguous.
- Ask before creating many new notes from one request; default to one canonical note plus atomic ledger updates.

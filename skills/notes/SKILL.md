---
name: notes
description: Capture notes and maintain structured long-term memory. Writes human-readable notes to Obsidian and syncs durable facts, preferences, decisions, goals, and open loops to a cognitive ledger. Asks targeted questions before writing. Use when the user wants to take notes, log decisions, capture ideas, plan, journal, or remember something.
license: WTFPL
metadata:
  notes_dir: "<notes-dir>"
  ledger_dir: "<ledger-dir>"
  defaults:
    mode: notes+ledger
    auto_write: true
    write_silently: true
    show_git_diff: false
---

# Notes

Capture notes and maintain structured long-term memory across two repositories:

- **Notes** (human-facing, Obsidian): `$NOTES_DIR` (default: `~/Code/notes`)
- **Ledger** (atomic, searchable memory): `$LEDGER_DIR` (default: `~/Code/cog-led`)

> **Setup:** Set `NOTES_DIR` and `LEDGER_DIR` environment variables or edit the defaults in the frontmatter above.

## Boot Sequence (Run on Activation)

1. `cd $LEDGER_DIR`
2. Read `notes/08_indices/context.md` — essential facts, active loops, key preferences
3. Run `./scripts/sheep status` — check if maintenance needed

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

### 3. Choose Note Destination in `$NOTES_DIR`

- Route using `references/question-playbook.md`.
- Search for existing notes first: `rg "<topic>" $NOTES_DIR -l`
- Prefer updating over creating duplicates.
- When uncertain between two folders, propose one default and ask once.

### 4. Write or Update the Note

- Keep content concise and scannable.
- Use practical headings; avoid template bloat.
- Include decisions, next steps, owners, and dates when present.
- Read `$NOTES_DIR/AGENTS.md` before first write if context is unclear (if it exists).

### 5. Sync Durable Items to the Ledger

Follow the ledger operations below for all writes to `$LEDGER_DIR`.

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
- `notes/08_indices/README.md` and `notes/08_indices/sleep_playbook.md` for indices/sleep

### Core Rules

- Search before writing. Prefer updating an existing note over creating a new one.
- Keep one idea per file; no chat transcripts.
- Never invent facts. Use `source: inferred` + `confidence < 0.7` if unsure.
- Always bump `updated` on edits.
- Always append to `notes/08_indices/timeline.md` after any note operation.

### Intent → Artifact Mapping

Infer ledger actions from natural language:

- **Stable preference** ("I prefer…", "I want you to…", "Going forward…") → `notes/03_preferences/pref__*.md`
- **Decision / commitment** ("We'll do X", "I decided…") → `notes/02_facts/fact__*.md` or `notes/04_goals/goal__*.md`
- **New concept / definition** ("Let's define…", "The idea is…") → `notes/06_concepts/concept__*.md`
- **Unresolved / revisit later** ("We should revisit…", "Not sure yet…") → `notes/05_open_loops/loop__*.md`

If multiple triggers occur, write multiple small notes rather than one large note.

### Create or Update an Atomic Note

1. Search: `rg "<topic>" $LEDGER_DIR/notes -l` and `fd "<type>__|<slug>" $LEDGER_DIR/notes`
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
- Personal details, history, family → `rg "<topic>" $LEDGER_DIR/notes/02_facts/`
- Past decisions or commitments → `rg "<topic>" $LEDGER_DIR/notes/02_facts/` + check timeline
- User preferences or style → `rg "<topic>" $LEDGER_DIR/notes/03_preferences/`
- Ongoing threads or open questions → `rg "<topic>" $LEDGER_DIR/notes/05_open_loops/`
- Defined concepts or frameworks → `rg "<topic>" $LEDGER_DIR/notes/06_concepts/`

**Rule:** If about to guess or assume something about the user, search the ledger first.

---

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

The ledger lives at `$LEDGER_DIR`. Notes live at `$NOTES_DIR`.

- If the user is working in another repository, temporarily `cd` to the target repo for operations, then return to the original working directory.
- Never modify unrelated repositories as part of note/ledger operations.

## Safety

- Use only repo data and user input; do not invent or import external facts.
- Keep changes atomic and auditable.
- Ask when scope or intent is ambiguous.
- Ask before creating many new notes from one request; default to one canonical note plus atomic ledger updates.

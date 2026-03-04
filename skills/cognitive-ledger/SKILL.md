---
name: cognitive-ledger
description: Cognitive Ledger is a cross-agent, file-based, self-maintaining memory substrate that lets tools act like they have long-term, structured, searchable memory—without stuffing it into the context window.
license: MIT
metadata:
  ledger_root: "<ledger-root>"
  defaults:
    auto_write: true
    write_silently: true
    show_git_diff: false
---

# Cognitive Ledger Operations

## Boot Sequence (Run on Activation)

1. `cd <ledger-root>`
2. Read `notes/08_indices/context.md` — essential facts, active loops, key preferences
3. Run `./scripts/sheep status` — check if maintenance needed

## Things3 Task Intake (When Planning)

If the user asks for help planning their week/day, or to triage "what's due", pull their live task list from Things:

- Default (human-readable):
  - `things-cli today`
  - `things-cli upcoming`
  - Optional: `things-cli deadlines` (all due dates across areas/projects)
  - Optional: `things-cli anytime` (noisy; use when the user wants a full backlog scan)

- If you need structure for summarization/planning, prefer JSON:
  - `things-cli -j today`
  - `things-cli -j upcoming`
  - `things-cli -j deadlines`

- Use filters to avoid dumping the entire backlog:
  - `things-cli anytime --filter-area "AreaName"`
  - `things-cli anytime --filter-project "ProjectName"`
  - `things-cli anytime --filtertag "priority-tag"`

This should happen early in planning conversations so the plan is grounded in actual due tasks, not memory.

**When to search deeper during conversation:**
- Personal details, history, family → `rg "<topic>" notes/02_facts/`
- Past decisions or commitments → `rg "<topic>" notes/02_facts/` + check timeline
- User preferences or style → `rg "<topic>" notes/03_preferences/`
- Ongoing threads or open questions → `rg "<topic>" notes/05_open_loops/`
- Defined concepts or frameworks → `rg "<topic>" notes/06_concepts/`

**Rule:** If about to guess or assume something about the user, search the ledger first.

## Defaults (Auto-write + Silent)

This skill operates with these defaults unless the user explicitly asks otherwise:

- **Auto-write: ON** — infer the correct ledger artifact(s) from user intent and write them without requiring the user to say “atomic note”, “open loop”, etc.
- **Silent writes: ON** — do not show `git diff` or verbose write logs by default.
- **Audit internally** — you may run `git status` / `git diff` to sanity-check changes, but only surface diffs if the user asks.

Always give a minimal acknowledgement after writing, e.g. “Logged.” If asked, provide paths changed and/or diffs.

## Source of Truth

Read these before writing (when you need guidance; keep reads minimal):
- `AGENTS.md` for operating rules and triggers
- `QUICK_REF.md` for minimal workflow
- `schema.yaml` for frontmatter and enums
- `templates/` for note structure
- `notes/08_indices/README.md` and `notes/08_indices/sleep_playbook.md` for indices/sleep

## Core Rules

- Search before writing. Prefer updating an existing note. If intent implies logging, write automatically.
- Keep one idea per file; no chat transcripts.
- Never invent facts. Use `source: inferred` + `confidence < 0.7` if unsure.
- Always bump `updated` on edits.
- Always append to notes/08_indices/timeline.md after any note operation (including indices and sleep changes).

## Intent → Artifact Mapping (Do not require user jargon)

Infer ledger actions from natural language. Use these mappings by default:

- **Stable preference** (“I prefer…”, “I want you to…”, “Going forward…”) → create/update `notes/03_preferences/pref__*.md`.
- **Decision / commitment** (“We’ll do X”, “I decided…”) → create/update an atomic note in the most suitable folder (often `notes/02_facts/fact__*.md` or `notes/04_goals/goal__*.md`).
- **New concept / definition** (“Let’s define…”, “The idea is…”) → create `notes/06_concepts/concept__*.md`.
- **Unresolved / revisit later** (“We should revisit…”, “Not sure yet…”, “Remind me to decide…”) → create/update `notes/05_open_loops/loop__*.md`.

If multiple triggers occur, write multiple small notes rather than one large note.

## Create or Update an Atomic Note

1. Search: `rg "<topic>" notes -l` and `fd "<type>__|<slug>" notes`
2. Choose type and path: `notes/{folder}/{type}__{slug}.md`
3. Use the appropriate template as reference.
4. Write/update frontmatter and content.
5. Append timeline entry:
  `{ISO timestamp} | created|updated|archived|deleted | {path} | {description}`
6. Verify internally with git diff (do not show unless asked).

## Create or Update an Open Loop

- File location: `notes/05_open_loops/loop__{slug}.md`
- Require `status` and a concrete `Next action` checkbox.
- Append a timeline entry for create/update/close/snooze.

**Timeline vs Status**: When closing a loop, you can use either `| closed |` or `| updated |` as the timeline action. The loop's frontmatter `status: closed` records the state; the timeline action records what happened. Both are valid—`closed` is more semantic, `updated` is more general. Consistency within a session is preferred.

## Query and Summarize

- Start with ranked retrieval:
  - `./scripts/ledger query "<topic>" --scope all --limit 8`
  - Use `--scope personal|work|dev|home|meta` to constrain quickly.
  - Use `--bundle` when you want a compact citation-backed context pack.
- Then use `rg` and `fd` for targeted follow-up or verification.
- Read only the minimum set needed.
- Provide a synthesis with citations to note paths.
- Do not create or update files for pure query requests unless the user explicitly asks to log the summary.

## Indices

- Regenerate with `./scripts/sheep index` when asked.
- Treat regenerated indices as note updates and log them in the timeline, but do not display diffs unless asked.
- Use JSON indices for machine consumption when relevant.

## Electric Sheep (Consolidation)

- Check status: `./scripts/sheep status`
- Sync drift check: `./scripts/sheep sync --check`
- Refresh sync baseline: `./scripts/sheep sync --apply`
- Checklist only: `./scripts/sheep sleep`
- Validate: `./scripts/sheep lint`
- Regenerate indices: `./scripts/sheep index`

If a user requests a dry-run and no explicit dry-run flag exists:
- Run `./scripts/sheep sleep` and report the checklist.
- Explain that the script does not support a true dry-run.
- Offer to run `lint`/`index` if they want concrete changes.

If a user requests apply/consolidation:
- Follow `notes/08_indices/sleep_playbook.md`.
- Run `lint` and `index` as needed.
- Update any notes/indices and append timeline entries.
- Internally verify with git diff; keep output minimal (“Logged.”). Show diffs only if asked.

## Working Directory

The ledger lives at `<ledger-root>`.

- If the user is working in another repository, temporarily `cd <ledger-root>` to perform ledger operations, then return to the original working directory.
- Never modify non-ledger repositories as part of ledger operations.

## Safety

- Use only repo data; do not invent or import external facts.
- Keep changes atomic and auditable.
- Ask when scope or intent is ambiguous.

### Automatic Maintenance Policy

Before any operation that writes to the ledger:

- Run `./scripts/sheep status`.
- If status indicates sleep is due, run `./scripts/sheep sleep`
  and `./scripts/sheep index` before proceeding.
- Do not run sleep for pure query operations.

This is the primary automatic trigger for Electric Sheep.

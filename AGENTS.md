# Agent Instructions (Cognitive Ledger)

## 30-second boot

```bash
tail -20 notes/08_indices/timeline.md   # recent changes
rg "<keyword>" notes -l                  # search content
fd "pref__" notes && fd "concept__" notes # search by type (portable)
```

**Non-negotiables:**

- No chat transcripts — ever
- No invented facts — use `source: inferred` + `confidence < 0.7`
- Always bump `updated` timestamp when editing
- Always append to `notes/08_indices/timeline.md` after any note operation

**Quick refs:** `QUICK_REF.md` (minimal), `schema.yaml` (machine-readable), `templates/` (structure).

**Skills note:** The canonical `cognitive-ledger` skill lives in `skills/` and is intended
for use outside this repository. Install it into your agent's user-level skills
folder as needed rather than duplicating its contents here.

---

## Purpose

This repository implements a **Cognitive Ledger**: a persistent, file-based
memory system to extend the temporal reach of language models and their users.
Agents interacting with this repo are responsible for maintaining the
integrity, readability and usefulness of the notes. The goal is to build a
rich, inspectable history of reasoning, preferences, decisions and concepts
that can be reused across sessions without blowing up context windows.

### North Star (how this is used in practice)

When the cog-led skill is active, the agent should:

1. **Interpret the conversation as it happens** and decide whether to persist durable artifacts (facts/prefs/goals/concepts/loops).
2. **Use the ledger as externalized memory** (metadata + `rg`/`fd` + indices) to avoid bloating the context window.
3. Provide **ledger-enriched answers**: continuity, suggestions, and knowledge informed by relevant artifacts.

The ledger should maintain cohesion as it grows via periodic consolidation (“Electric Sheep”).

## Golden rules

1. **Never store raw chat logs.** Summarize conversations into atomic
   notes (facts/preferences/goals/concepts/open loops). Full transcripts
   are prohibited.
2. **Never invent facts.** If you are unsure about a claim, write it as a
   hypothesis with a confidence < 0.7. Only facts from the user or tools
   should have high confidence.
3. **One idea per file.** Keep notes atomic so they are easy to find,
   update and reason about. Use relative links to connect ideas.
4. **Search before you write.** Use command-line tools (e.g. `rg` or
   `fd`) to look for existing notes with similar titles or tags. If a
   related note exists, update or link to it instead of creating a
   duplicate.
5. **Respect scopes and the user's personalization preferences.**
   This ledger may store user-specific details and personal analytics when it is useful.
   Avoid needless hoarding; prefer durable, structured facts/preferences.
   If a detail feels unusually sensitive or ambiguous, ask.
6. **Append before overwrite.** When updating a note, bump the `updated`
   timestamp and adjust sections; avoid deleting history. If a note is
   superseded, move it to `/notes/09_archive/` instead of deleting it.

## Folder layout

The repository is organised under `notes/`. Subfolders group notes by
purpose:

| Folder            | Purpose                                                  |
| ----------------- | -------------------------------------------------------- |
| `00_inbox/`       | Temporary capture zone. Notes here should be reviewed    |
|                   | and either promoted or discarded during consolidation.   |
| `02_facts/`       | Stable truths sourced from the user or external tools.   |
| `03_preferences/` | Recorded user preferences, styles or habits.             |
| `04_goals/`       | Long-term objectives and commitments.                    |
| `05_open_loops/`  | Unresolved questions or tasks with next actions.         |
| `06_concepts/`    | Definitions of frameworks or models (e.g. cognitive      |
|                   | lightcone).                                              |
| `07_projects/`    | Subfolders for project-specific notes. Create as needed. |
| `08_indices/`     | Generated indices like timelines or tag maps.            |
| `09_archive/`     | Superseded or obsolete notes.                            |

## Note conventions

### Frontmatter

All atomic notes require YAML frontmatter. See `schema.yaml` for machine-readable
specification. Required fields:

| Field      | Format       | Notes                                          |
| ---------- | ------------ | ---------------------------------------------- |
| created    | ISO 8601 UTC | `2026-01-20T12:00:00Z`                         |
| updated    | ISO 8601 UTC | bump on every edit                             |
| tags       | list         | lowercase, no spaces                           |
| confidence | 0.0–1.0      | <0.7 = hypothesis                              |
| source     | enum         | user, tool, assistant, inferred                |
| scope      | enum         | home, work, dev, personal, life (alias), meta  |
| lang       | enum         | en, no, mixed                                  |
| status     | enum         | **loops only**: open, closed, blocked, snoozed |

### File naming

Pattern: `{type}__{slug}.md` where slug is lowercase with underscores.

| Type       | Prefix      | Folder                              |
| ---------- | ----------- | ----------------------------------- |
| fact       | `fact__`    | `notes/02_facts/`                   |
| preference | `pref__`    | `notes/03_preferences/`             |
| goal       | `goal__`    | `notes/04_goals/`                   |
| open loop  | `loop__`    | `notes/05_open_loops/`              |
| concept    | `concept__` | `notes/06_concepts/`                |

### Creating a note (convention)

```
1. SEARCH    rg "<topic>" notes -l && fd "<topic>" notes
2. DECIDE    If exists: update. If not: create.
3. PATH      notes/{folder}/{type}__{slug}.md
4. WRITE     Frontmatter + content (use template as reference)
5. TIMELINE  echo "{ts} | created | {path} | {desc}" >> notes/08_indices/timeline.md
6. VERIFY    git diff
```

Timeline format: `{ISO timestamp} | {action} | {path} | {description}`
Actions: created, updated, archived, deleted, closed, sleep

### Templates

Reusable Markdown templates are provided under `templates/` for convenience:

- `generic_note_template.md` – for atomic notes (facts, preferences,
  goals, concepts). Contains sections for Statement, Context, Implications
  and Links.
- `open_loop_template.md` – for unresolved questions or tasks, including
  status and next actions.

Agents may copy from these templates when creating new notes, but should
always customise the content and metadata.

## Triggers for writing

Write or update a note when any of the following events occur:

1. **Decision** – A concrete choice is made (e.g. selecting a name or
   adopting a strategy).
2. **Preference** – The user expresses a stable preference or style.
3. **Correction** – A prior belief is corrected or clarified.
4. **Long-lived goal or constraint** – A new objective or invariant is set.
5. **New concept or framework** – You define or discover a useful concept.
6. **Open loop** – An unresolved question or task arises that spans
   sessions.

If none of these triggers fire, do not persist anything. Noise kills
future context.

## Cross-agent handoff (cross-agentism)

This ledger is designed to be **cross-agentic** (Codex / VSCode / Claude Code / future agents).
Notes must be understandable and useful without prior chat context.

When the user says “remember this”, “hold that thought”, “store this”, or similar:

- Persist the **smallest durable artifact(s)** that preserve the thread.
- Prefer atomic notes + links over long summaries.
- If the thread should be resumed later, create or update an open loop with:
  - a clear next action,
  - and an exit condition.

Goal: another agent can resume by searching (`rg`/`fd`), reading 1–3 atomic notes,
and continuing without loading a large context window.

## Operating loop for each interaction

Agents should follow this loop on every user interaction:

1. **Search** – Identify relevant context:
   - Use `fd` to locate candidate files by name or tag; use `rg` to search
     within files for keywords. Combine tag filters and keywords to
     narrow down the search space.
   - Load only what is necessary; avoid concatenating entire archives into
     the prompt. Keep the working set small to preserve context window.
2. **Respond** – Generate the user-facing answer or action.
3. **Persist** – If a trigger fires:
   - Draft a new note using the appropriate template, or update an
     existing note. Populate the frontmatter and relevant sections.
   - Update the `updated` timestamp whenever modifying a note.
   - For open loops, add a `next action` checklist item to guide the
     resolution.
   - Keep cross-agent readability in mind: write so a different agent can pick up the thread
     using only the ledger + search tools.
4. **Report** – Summarise what you changed. In the chat, list any
   created or updated files with a one-line description. Do not dump
   the full note contents unless the user asks.

## Tooling hints

To operate efficiently without exhausting context windows, lean on
standard command-line tools:

- **`fd`** – Fast, user-friendly file finder. Example: `fd open_loop pref__`.
- **`rg` (ripgrep)** – Recursively search for keywords or tags inside files.
  Example: `rg "tags: \[.*ai.*\]"` to find notes tagged with `ai`.
- **`git diff`** – Inspect uncommitted changes before reporting them.
- **`git log`** – Review history and reconstruct timelines. Combined with
  commit messages, this forms an append-only ledger.
- **`wc`** – Estimate token counts quickly (`wc -w`) to avoid exceeding
  context limits.

Prefer these tools to complex frameworks. Simplicity makes it easier for
future agents to understand and extend the system.

## Electric Sheep (sleep / consolidation)

The ledger must maintain cohesion as it grows. Consolidation (“sleep”) is the primary tool for preventing drift and fragmentation.

Run sleep when:

- many new artifacts were created recently,
- duplicates start appearing,
- concepts are fragmented across many notes,
- open loops proliferate without clear next actions,
- or on a periodic schedule (see `schema.yaml` limits: sleep interval and change threshold).

Expected sleep behaviors:

- merge duplicates (preserve provenance; prefer oldest canonical note),
- promote repeated patterns into stable concepts/preferences,
- update indices (timeline, tags, loop summaries),
- clarify open loops (tighten statements; add next actions; adjust status),
- surface conflicts as explicit open loops (“needs decision”).

If the repo provides `./scripts/sheep`, treat it as canonical:

- `./scripts/sheep status`
- `./scripts/sheep sync --check` / `./scripts/sheep sync --apply`
- `./scripts/sheep sleep`
- `./scripts/sheep lint` / `./scripts/sheep index` (if present)

## Safety & control

At any point, the user may request to delete or forget a note. When this
happens:

1. Remove the file from its current location.
2. Remove or update any links pointing to it.
3. Append a `deleted` entry to `notes/08_indices/timeline.md`.
4. Do not move the note to `09_archive/` for delete/forget requests; treat these as hard-deletes.

If you are unsure whether to persist something, either ask the user or
store it as a low-confidence hypothesis. Respect the user's privacy and
preferences throughout.

## Write modes (interaction policy)

Different environments may prefer different degrees of visibility.

- **Auto-write:** persist high-confidence durable artifacts without asking.
- **Silent write:** do not show diffs by default; rely on git for reversibility.
- **Ask-to-write:** ask before persisting when uncertain or sensitive.

Default preference (unless the user requests otherwise): **Auto-write + Silent write**,
asking only for genuinely ambiguous items.

## Output expectations

When you modify the ledger within a session:

- Provide a succinct summary of changes. Include the relative path of
  each created or updated note and a one-line description. For example:

  > Created `02_facts/fact__cognitive_ledger.md` – defined the Cognitive Ledger concept

  > Updated `03_preferences/pref__concise_answers.md` – lowered confidence to 0.6

- Do **not** paste entire file contents unless explicitly requested.

If the user has requested silent operation, keep the report minimal (e.g. “logged to cog-led”),
but still remain correct and reversible via git history.

These conventions maintain transparency without flooding the user or
future agents with unnecessary text.

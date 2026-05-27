# Agent Instructions (Cognitive Ledger)

## Quick Reference

Machine-readable spec: `schema.yaml`. Note templates: `templates/`.

Resolve the active physical corpus path with `ledger paths --field ledger_notes_dir`.
Logical note references remain `notes/...` even when the corpus lives outside this repo.

The canonical `/notes` agent skill is maintained in a separate repository:
https://github.com/damsleth/SKILLS. Install it from there rather than
duplicating its contents here.

### Boot

```bash
LEDGER_NOTES_DIR="$(ledger paths --field ledger_notes_dir)"
ledger context --format boot         # full boot payload (identity + loops + status)
tail -20 "$LEDGER_NOTES_DIR/08_indices/timeline.md"          # recent changes
rg "<keyword>" "$LEDGER_NOTES_DIR" -n                        # search content (show matches)
fd "id__" "$LEDGER_NOTES_DIR/01_identity"                    # identity notes
fd "pref__" "$LEDGER_NOTES_DIR/03_preferences" && fd "concept__" "$LEDGER_NOTES_DIR/06_concepts"  # search by type
```

**Two-tier lookup strategy:**
- `context.md` for boot (compact summary, always loaded at session start)
- `notes/08_indices/index.md` / `index.json` as a lookup table for deeper searches
  (do NOT load into context at boot - on a mature ledger it would become the bottleneck)

**Voice DNA:** If `notes/01_identity/id__voice_dna.md` exists, read it at boot.
Apply the voice profile to tone, sentence structure, and vocabulary when writing notes.

**Non-negotiables:**

- No chat transcripts — ever
- No invented facts — use `source: inferred` + `confidence < 0.7`
- Always bump `updated` timestamp when editing
- Always append to `notes/08_indices/timeline.md` after any note operation

### Should I write?

Persist only if it's **durable** and **re-usable** (Decision / Preference / Correction / Goal / Concept / Open loop).
If none apply: don't write. Noise kills retrieval.

### Create or update a note

1. **Search first**: `rg "<topic>" notes -l`
2. **Create/update the right type** (atomic, one idea per file; use the right folder + prefix)
3. **Frontmatter required**: `created`, `updated`, `tags`, `confidence`, `source`, `scope`, `lang` (+ `status` for loops)
4. **No transcripts**: never store raw chat logs; summarize into atomic notes
5. **Append timeline**: `echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | <verb> | <path> | <why>" >> notes/08_indices/timeline.md`

### Python env

```bash
./scripts/setup-venv.sh                                       # default: base + dev + embeddings
./scripts/setup-venv.sh --python python3.12 --recreate        # if torch wheels are missing
./scripts/setup-venv.sh --minimal                             # base-only fallback
```

### Obsidian Drop-In

```bash
pipx install cognitive-ledger
ledger-obsidian init --vault /path/to/obsidian-vault
ledger-obsidian import --vault /path/to/obsidian-vault
ledger-obsidian bootstrap --root ~/Code/notes
ledger-obsidian watch --vault /path/to/obsidian-vault
ledger-obsidian queue sync --vault /path/to/obsidian-vault
ledger-obsidian doctor --vault /path/to/obsidian-vault
ledger-obsidian daemon start|status|stop --vault /path/to/vault   # macOS
```

### Retrieve & Eval

```bash
ledger query "<topic>" --scope all --limit 8
ledger query "<topic>" --scope all --limit 8 --retrieval-mode <mode>
ledger query "<topic>" --scope dev --bundle
ledger discover-source "<topic>" --source-notes-dir <root> --limit 20
ledger embed build --target ledger --backend local --model TaylorAI/bge-micro-v2
ledger embed status --target both
ledger eval --cases "$(ledger paths --field ledger_notes_dir)/08_indices/retrieval_eval_cases.yaml" --k 3 --strict-cases
ledger loops                 # compact list (default)
ledger notes --type <all|identity|facts|preferences|goals|loops|concepts>
ledger context --format boot # session boot payload
ledger context --format identity  # identity notes only
```

### Signals (Feedback Loop)

```bash
ledger signal add --type retrieval_hit --query "deploy" --note notes/02_facts/fact__k8s.md
ledger signal add --type correction --note notes/03_preferences/pref__x.md --detail "outdated"
ledger signal add --type rating --rating 8
ledger signal summarize      # rebuild signal_summary.json
ledger signal stats          # show signal counts, top notes, gaps
```

Signal types: `retrieval_hit`, `retrieval_miss`, `correction`, `affirmation`,
`stale_flag`, `preference_applied`, `rating`.

**Scan-and-judge review (TUI):** `ledger review` walks a *prioritized* queue —
the notes most worth judging surface first (corrections pending →
high-traffic-but-never-affirmed → never reviewed → stale-by-age → low
confidence / inferred). One keystroke per note:

```
k keep (affirmation)   w wrong (correction, asks reason)   s stale (stale_flag)
1-9 rate               space/enter skip                    u undo   q quit & save
```

Signals are buffered and flushed (with a summary rebuild) on exit.

```bash
ledger review                       # full queue, interactive TUI
ledger review --type facts --stale-days 90 --unjudged-only
ledger review --queue               # print the prioritized list, no TUI
ledger review --stats               # signal dashboard: coverage, score dist, gaps
```

**Use-time capture (opt-in):** set `signals_auto_capture: true` in
`~/.config/ledger/config.yaml` (or `LEDGER_SIGNALS_AUTO_CAPTURE=1`) and queries
auto-log `retrieval_miss` when nothing scores above `signals_miss_score_floor`
(both CLI `ledger query` and the web `/search`). `ledger query "<topic>" --pick`
prompts for the result that helped and logs a `retrieval_hit`; opening a note
from a web search result does the same. Off by default to avoid noise; even when
on, signal feedback stays inert for ranking until `signal_min_entries` (20)
accrue and `score_weight_signal` is raised above 0.

`<mode>`: `legacy`, `two_stage`, `scope_type_prefilter`, `precomputed_index`, `progressive_disclosure`, `semantic_hybrid`.

### A/B Testing

```bash
ledger ab run --baseline-ref main --candidate-ref HEAD   # uses ledger_notes_dir from config.yaml
ledger ab run --corpus ~/Code/ledger-notes --baseline-ref main --candidate-ref HEAD
ledger ab run --baseline-ref main --candidate-ref HEAD --eval-runs 7 --query-runs 5
ledger ab run --baseline-ref main --candidate-ref HEAD --query-runs 5 --cold-query
```

Exit codes: `0` beneficial, `2` regression, `3` neutral, `4` invalid setup.

### Electric Sheep (sleep / consolidation)

```bash
ledger sleep status
ledger sleep sync --check && ledger sleep sync --apply
ledger sleep sleep
ledger sleep lint
ledger sleep index
```

### Folder map

```
notes/01_identity/     core identity: mission, beliefs, models, strategies, narratives
notes/02_facts/        stable truths / decisions
notes/03_preferences/  user preferences / policies
notes/04_goals/        long-lived objectives
notes/05_open_loops/   durable unresolved items (status lifecycle)
notes/06_concepts/     definitions / frameworks / mental models
notes/08_indices/      timeline, logs, import state, generated indexes, signals
notes/09_archive/      superseded notes (do not delete)
```

---

### Recommended Setup

**Claude Code hooks** (`.claude/settings.json`):
```json
{
  "hooks": {
    "SessionStart": [
      {"type": "command", "command": "bash scripts/hooks/session_start.sh"}
    ],
    "Notification": [
      {"type": "command", "command": "bash scripts/hooks/session_end_capture.sh"}
    ]
  }
}
```

**Codex** (`AGENTS.md` in repo root already covers this).

**Automated maintenance** (cron or Claude Code `/schedule`):
```
0 6 * * * /path/to/cognitive-ledger/scripts/sheep-auto.sh
```

**Troubleshooting:**
- If hooks don't fire: check that scripts are executable (`chmod +x scripts/hooks/*.sh`)
- If venv is missing: run `./scripts/setup-venv.sh`
- If session_start is slow: ensure `.venv` exists (avoids pip install on each run)
- If capture creates too many inbox items: review and triage with `ledger inbox triage`

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
| `01_identity/`    | Core identity documents (mission, beliefs, mental        |
|                   | models, strategies, narratives). Max 5 files.            |
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
| identity   | `id__`      | `notes/01_identity/`                |
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
7. **Identity change** – The user expresses a core belief, mission shift,
   strategic heuristic, or narrative reframing → persist to identity layer.

If none of these triggers fire, do not persist anything. Noise kills
future context.

### Signal capture (feedback loop)

In addition to note writes, capture feedback signals when:

- A retrieved note was **used** in a response → `retrieval_hit`
- A search found **nothing useful** → `retrieval_miss`
- The user **corrects** the agent's use of a note → `correction`
- The user **confirms** the agent got it right → `affirmation`
- A note is referenced but its content is **outdated** → `stale_flag`

Capture via: `ledger signal add --type <type> [--query <q>] [--note <path>]`

Do NOT capture signals speculatively or for trivial queries. Only log when
there is clear user feedback or deliberate note usage.

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
   - Identity notes (`notes/01_identity/`) are always relevant context;
     load them early in the session if not already loaded.
2. **Respond** – Generate the user-facing answer or action.
3. **Persist** – If a trigger fires:
   - Draft a new note using the appropriate template, or update an
     existing note. Populate the frontmatter and relevant sections.
   - Update the `updated` timestamp whenever modifying a note.
   - For open loops, add a `next action` checklist item to guide the
     resolution.
   - Keep cross-agent readability in mind: write so a different agent can pick up the thread
     using only the ledger + search tools.
4. **Signal** – If user feedback occurred (correction, affirmation, or
   explicit rating), or a retrieved note was used in the response, log a
   signal via `ledger signal add`. See "Signal capture" above.
5. **Report** – Summarise what you changed. In the chat, list any
   created or updated files with a one-line description. Do not dump
   the full note contents unless the user asks.

## Session Lifecycle Hooks

Hook scripts under `scripts/hooks/` automate common session patterns:

| Hook               | Script                          | Purpose                                      |
| ------------------ | ------------------------------- | -------------------------------------------- |
| **session-start**  | `scripts/hooks/session_start.sh` | Load boot context: identity + loops + status |
| **post-write**     | `scripts/hooks/post_write.sh`   | Append timeline entry after note operations  |
| **session-end**    | `scripts/hooks/session_end.sh`  | Flush signals, report session activity       |

Manual invocation: `bash scripts/hooks/session_start.sh`

For Claude Code integration, configure in `.claude/settings.json`:
```jsonc
{
  "hooks": {
    "SessionStart": [{"type": "command", "command": "bash scripts/hooks/session_start.sh"}]
  }
}
```

## Identity Layer

Identity notes in `notes/01_identity/` capture *who the user is* — their
mission, beliefs, mental models, decision strategies, and personal narratives.
These are high-signal, small files that provide rich context for interpreting
requests.

- Max 5 files (one per `identity_type`: mission, beliefs, models, strategies, narratives)
- Always loaded at boot (via context profile or session-start hook)
- Receive a retrieval score boost (identity notes surface above similarly-relevant notes)
- Distinct from `06_concepts/` (general frameworks) — identity is *personal* axioms

When to update: mission shifts, new beliefs, changed decision heuristics, or
narrative reframing. Flag identity notes >90 days old during consolidation.

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

The ledger must maintain cohesion as it grows. Consolidation ("sleep") is the primary tool for preventing drift and fragmentation. Commands are listed in the Quick Reference above.

Run sleep when:

- many new artifacts were created recently,
- duplicates start appearing,
- concepts are fragmented across many notes,
- open loops proliferate without clear next actions,
- or on a periodic schedule (see `schema.yaml` limits: sleep interval and change threshold).

Expected behaviors:

- merge duplicates (preserve provenance; prefer oldest canonical note),
- promote repeated patterns into stable concepts/preferences,
- update indices (timeline, tags, loop summaries),
- clarify open loops (tighten statements; add next actions; adjust status),
- surface conflicts as explicit open loops ("needs decision").

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

## Keeping docs in sync

When you make changes to the ledger's **infrastructure** (new note types,
config parameters, retrieval modes, CLI subcommands, schema changes, hooks,
or other user-facing features):

1. **`CHANGELOG.md`** – Append an entry under the current date. Use
   `### Added`, `### Changed`, `### Fixed`, or `### Removed` headings
   (keep-a-changelog style). One bullet per distinct change.
2. **`README.md`** – Update if the change affects getting-started steps,
   folder layout, CLI examples, or introduces a new top-level feature
   section. Keep the README concise; link to `AGENTS.md` or `schema.yaml`
   for details.

Do **not** update these files for routine note operations (creating,
updating, or archiving individual notes). Only infrastructure and tooling
changes warrant doc updates.

## Releasing

When the user says something like **"cut a major/minor/patch version and
publish"**, it always means this full sequence (semver: major = X, minor =
Y, patch = Z in `vX.Y.Z`):

1. **Bump the version** in BOTH `pyproject.toml` (`version = "..."`) and
   `ledger/__init__.py` (`__version__ = "..."`). Move the `## Unreleased`
   CHANGELOG entries under a new dated heading `## <YYYY-MM-DD> (X.Y.Z)`.
2. **Commit and push** to `main` (`release: bump to X.Y.Z`), then create and
   push an annotated tag: `git tag -a vX.Y.Z -m vX.Y.Z && git push origin main vX.Y.Z`.
   The Homebrew formula sources the GitHub tag tarball, so the tag must exist
   before computing its hash.
3. **Update the homebrew-tap formula** at `~/code/homebrew-tap/Formula/cognitive-ledger.rb`:
   point `url` at `.../archive/refs/tags/vX.Y.Z.tar.gz` and set `sha256` to
   `curl -sL <url> | shasum -a 256`. Commit and push the tap repo
   (`git@github.com:damsleth/homebrew-tap`).
4. **Publish to PyPI** with uv:
   ```bash
   export UV_PUBLISH_TOKEN="$(grep -E '^UV_PUBLISH_TOKEN=' ~/code/owa-piggy/.env | cut -d= -f2- | tr -d '"')"
   rm -rf dist/ && uv build && uv publish
   ```
   (`uv build` fails on tracked absolute/external symlinks — keep dev-only
   symlinks like `scripts/ledger` untracked + gitignored.)

**Brew guard — do NOT run `brew install` / `brew upgrade` / `brew reinstall`**
if `where ledger` resolves to anything other than the Homebrew bin (e.g. the
dev symlink at `~/.local/bin/ledger`). The user runs the CLI from a local
editable install; reinstalling via brew would shadow it and reintroduce
version skew. Validating the formula end-to-end is only safe on a machine
that doesn't use the dev install.

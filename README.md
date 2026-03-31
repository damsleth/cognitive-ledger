# Cognitive Ledger
Deepening your agent's cognitive lightcone with a persistent, hybrid markdown+embeddings based memory system. Includes dreams-based consolidation (`electric sheep`), a drop-in `/notes` skill for agents, and tools for bootstrapping from existing notes trees.  

![Cognitive Lightcone](skills/notes/cognitive_lightcone.png)

## What
A structured, file-based memory system for AI agents. Small atomic notes (facts, preferences, goals, open loops, concepts) stored as markdown with YAML frontmatter. Searchable, versionable, and designed to fit inside context windows.

## Why
Language models forget everything between sessions. The Cognitive Ledger gives them a persistent, inspectable memory - not by stuffing raw chat logs into the context window, but by distilling conversations into atomic, retrievable notes. Each note captures one durable idea (a decision, a preference, a goal, an open question) so that any agent can resume any thread by searching the ledger instead of re-reading the entire conversation history. The result is continuity across sessions, agents, and tools without blowing up context budgets.

## Getting Started

### 1. Clone and set up

```bash
git clone https://github.com/<you>/cognitive-ledger.git
cd cognitive-ledger
./scripts/setup-venv.sh
```

### 2. Install the `/notes` skill

The `/notes` skill lets your agent capture notes and sync durable memory to the ledger. Install it into your agent's skill folder:

```bash
./skills/install-skill.sh
```

This symlinks `skills/notes/` into `~/.claude/skills/`, `~/.codex/skills/`, and `~/.copilot/skills/`.

Before first use, set `NOTES_DIR` and `LEDGER_DIR` in the skill's frontmatter (`skills/notes/SKILL.md`) or as environment variables:

```bash
export NOTES_DIR=~/Code/notes        # your existing notes tree
export LEDGER_DIR=~/Code/cognitive-ledger
```

### 3. Try it

Once the skill is installed, invoke `/notes` in your agent session. The skill will:

1. Read `notes/08_indices/context.md` for existing context
2. Ask targeted questions about what you want to capture
3. Write atomic notes to the ledger (and optionally to your notes tree)

## Plugging Into an Existing Notes Repository

You don't need to move your notes. Bootstrap the ledger inside your existing notes tree:

```bash
# Generic markdown notes
ledger-obsidian bootstrap --root ~/Code/notes
ledger-obsidian import --root ~/Code/notes

# Obsidian vault
ledger-obsidian init --vault /path/to/your/obsidian-vault
ledger-obsidian import --vault /path/to/vault
```

This creates a `cognitive-ledger/` subdirectory inside your notes tree. Source notes are never edited.

### Keeping it in sync

```bash
ledger-obsidian watch --vault /path/to/vault          # live sync
ledger-obsidian daemon start --vault /path/to/vault    # macOS background service
ledger-obsidian queue sync --vault /path/to/vault      # manual sync
ledger-obsidian doctor --vault /path/to/vault          # health check
```

## Indexing and Retrieval

### Build indices

```bash
./scripts/sheep index                    # rebuild metadata index
./scripts/sheep lint                     # validate frontmatter
./scripts/sheep status                   # time since last consolidation
```

### Query your notes

```bash
./scripts/ledger query "calendar constraints" --scope all --limit 8
./scripts/ledger query "calendar constraints" --bundle    # context-window-friendly output
./scripts/ledger loops                                    # list open loops
./scripts/ledger loops --interactive                      # progressive disclosure
```

### Semantic search (optional)

```bash
./scripts/ledger embed build --target ledger --backend local --model TaylorAI/bge-micro-v2
./scripts/ledger query "calendar constraints" --retrieval-mode semantic_hybrid --embed-backend local
```

### Eval and A/B testing

```bash
./scripts/ledger eval --cases notes/08_indices/retrieval_eval_cases.yaml --k 3
./scripts/ledger_ab --baseline-ref main --candidate-ref HEAD --runs 5
```

## Folder Layout

```
notes/
  00_inbox/         temporary capture (cleared on consolidation)
  02_facts/         stable truths (fact__*.md)
  03_preferences/   user preferences (pref__*.md)
  04_goals/         long-term objectives (goal__*.md)
  05_open_loops/    unresolved items (loop__*.md)
  06_concepts/      definitions and frameworks (concept__*.md)
  07_projects/      project-specific subfolders
  08_indices/       derived indices (timeline, tags, eval cases)
  09_archive/       superseded notes
```

Each note has YAML frontmatter with `created`, `updated`, `tags`, `confidence`, `source`, `scope`, and `lang`. See `schema.yaml` for the full spec and `templates/` for starter templates.

## Consolidation ("Electric Sheep")

Periodic maintenance keeps the ledger coherent as it grows:

```bash
./scripts/sheep sync --check && ./scripts/sheep sync --apply
./scripts/sheep sleep
```

Sleep merges duplicates, promotes patterns into stable notes, updates indices, and tightens open loops.

## TUI

A terminal interface for browsing and editing notes:

```bash
./.venv/bin/python -m tui              # run from venv
# or
./tui/build-tui.sh && ./tui/dist/ledger-tui   # standalone binary
```

Key bindings: `j/k` navigate, `Enter` select, `1-5` filter by type, `/` search, `e` edit, `g` graph, `q` quit.

## Agent Integration

Agents should read `AGENTS.md` for the full protocol — golden rules, note conventions, write triggers, and the operating loop. The short version:

- Search before you write (`rg`, `fd`)
- One idea per file
- Never store raw chat logs
- Append to `notes/08_indices/timeline.md` after every note operation

## Python Environment

```bash
./scripts/setup-venv.sh                                  # base + dev + embeddings
./scripts/setup-venv.sh --python python3.12 --recreate   # force interpreter
./scripts/setup-venv.sh --minimal                        # base only
```

All scripts auto-activate `.venv` when present.

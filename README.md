# Cognitive Ledger
Deepening your agent's cognitive lightcone with a persistent, hybrid markdown+embeddings based memory system. Includes dreams-based consolidation (`electric sheep`), a drop-in `/notes` skill for agents, and tools for bootstrapping from existing notes trees.  

![Cognitive Lightcone](cognitive_lightcone.png)

## Suite

cognitive-ledger is Tier 2 in the **[mnem](https://github.com/damsleth/mnem)**
memory suite, alongside YAAMS (Tier 1 raw), owa-piggy (M365 auth),
and owa-tools (M365 read/write). The suite gives you one install
(`brew install damsleth/tap/mnem`), one verb surface, and one CLI
contract (output classes, exit codes, action envelopes - see
[mnem/CONVENTIONS.md](https://github.com/damsleth/mnem/blob/main/CONVENTIONS.md)).
The `ledger`, `ledger-obsidian`, and `sheep` binaries continue to
work standalone.

## What
A structured, file-based memory system for AI agents. Small atomic notes (facts, preferences, goals, open loops, concepts, identity) stored as markdown with YAML frontmatter. Searchable, versionable, and designed to fit inside context windows. Includes a feedback loop that captures retrieval signals to improve ranking over time.

## Why
Language models forget everything between sessions. The Cognitive Ledger gives them a persistent, inspectable memory - not by stuffing raw chat logs into the context window, but by distilling conversations into atomic, retrievable notes. Each note captures one durable idea (a decision, a preference, a goal, an open question) so that any agent can resume any thread by searching the ledger instead of re-reading the entire conversation history. The result is continuity across sessions, agents, and tools without blowing up context budgets.

## Quick Start

```bash
brew tap damsleth/tap
brew install cognitive-ledger
ledger init --root "$HOME/.config/cognitive-ledger" \
  --ledger-notes-dir ~/Code/ledger-notes \
  --source-notes-dir ~/Code/notes
```

`ledger init` scaffolds the notes tree (`01_identity/` … `09_archive/`), writes `config.yaml`, and emits templates + schema into `--root`. `config.yaml` is created with `first_run: true` so the session-start hook can inject setup guidance on the first agent session.

Optional flags:

```bash
ledger init --voice-dna ~/voice-profile.json   # import your writing voice
ledger paths                                    # verify resolved locations
```

Persist `LEDGER_ROOT` in your shell rc so the brew-installed CLIs find your config (otherwise they default to a read-only Cellar path):

```bash
echo 'export LEDGER_ROOT="$HOME/.config/cognitive-ledger"' >> ~/.zshrc
source ~/.zshrc
```

## Agent Skill

The `/notes` agent skill that drives this ledger lives in a separate repo:

> https://github.com/damsleth/SKILLS

Clone it and run the installer to symlink the skill into your agent's user-level skills folder (Claude, Codex, Copilot):

```bash
git clone https://github.com/damsleth/SKILLS.git
cd SKILLS
./install-skill.sh        # interactive checkbox UI
```

The skill's first run will brew-install this package automatically if it's missing - so you can install in either order.

### Configure (optional)

Edit `config.yaml` in the repo root:

```yaml
# config.yaml
ledger_notes_dir: ~/Code/ledger-notes
source_notes_dir: ~/Code/notes
# auto_file_synthesis: false  # set true to auto-file query syntheses
```

Environment variables (`LEDGER_ROOT`, `LEDGER_NOTES_DIR`, etc.) override the
config file.

### Set up hooks (recommended)

Add to `.claude/settings.json`:

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

### Try it

Invoke `/notes` in your agent session:

1. Read `$(ledger paths --field ledger_notes_dir)/08_indices/context.md` for existing context
2. Ask targeted questions about what you want to capture
3. Write atomic notes to the ledger (and optionally to your notes tree)

Or try `ledger briefing` for a daily status overview.

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
ledger sleep index                    # rebuild metadata index
ledger sleep lint                     # validate frontmatter
ledger sleep status                   # time since last consolidation
```

### Query your notes

Three detail levels let agents control the cost/detail tradeoff:

```bash
# Index view - compact scan (~20-30 tokens per result)
ledger query "calendar constraints" --scope all --limit 8 --view index

# Context view (default) - statements, snippets, tags (~80-120 tokens)
ledger query "calendar constraints" --scope all --limit 8

# Detail view - full bodies, score components (~200-1000 tokens)
ledger query "calendar constraints" --scope all --limit 8 --view detail

# Bundle mode - context-window-friendly excerpts within a word budget
ledger query "calendar constraints" --bundle

# Other
ledger loops                                    # list open loops
ledger context --format boot                    # session boot payload
```

### Semantic search (recommended)

Build the embedding index to activate `semantic_hybrid` (the default retrieval mode). Without this, queries fall back to `precomputed_index`.

```bash
ledger embed build --target ledger --backend local --model TaylorAI/bge-micro-v2
ledger embed status --target ledger    # verify index exists
```

### Eval and A/B testing

```bash
ledger eval --cases "$(ledger paths --field ledger_notes_dir)/08_indices/retrieval_eval_cases.yaml" --k 3
ledger ab run --baseline-ref main --candidate-ref HEAD --runs 5     # uses ledger_notes_dir from config.yaml
ledger ab run --corpus ~/Code/ledger-notes --baseline-ref main --candidate-ref HEAD --runs 5
```

### Retrieval Mode A/B Results

All modes were benchmarked against `legacy` on the ledger corpus (5 runs each). `semantic_hybrid` is the default and falls back to `precomputed_index` when embeddings are unavailable.

| Mode | MRR | hit@1 | hit@k | p95 (ms) | Decision | Status |
|---|---|---|---|---|---|---|
| **semantic_hybrid** | **0.830** | **0.733** | **0.933** | 2.4 | beneficial (+0.108 MRR) | **default** (falls back to precomputed_index without embeddings) |
| precomputed_index | 0.726 | 0.578 | 0.867 | 6.1 | beneficial (+0.004 MRR) | fallback default |
| progressive_disclosure | 0.725 | 0.578 | 0.867 | 7.3 | beneficial (+0.004 MRR) | available |
| two_stage | 0.725 | 0.578 | 0.867 | 7.7 | beneficial (+0.004 MRR) | available |
| scope_type_prefilter | 0.726 | 0.578 | 0.867 | 40.5 | beneficial (+0.004 MRR) | available (slow) |
| legacy | 0.722 | 0.578 | 0.867 | 5.0 | baseline | available |
| compressed_attention | 0.720 | 0.578 | 0.844 | 4.8 | **regression** (-0.022 hit@k) | **removed** |

`semantic_hybrid` dominates every quality metric (+15.6% hit@1, +6.7% hit@k, +10.8% MRR vs legacy) and is also the fastest at query time (2.4ms p95) because scoring uses precomputed embeddings. It requires a one-time `ledger embed build` step - without it, queries gracefully fall back to `precomputed_index` (best lexical mode).

#### A/B performance over time

Canonical A/B artifacts were consolidated under `.plans/done/19-*.md` through `.plans/done/32-*.md`; duplicate and superseded raw runs were removed. Source data for these charts lives in `docs/ab/performance_series.json` and they are regenerated with `python scripts/build_ab_charts.py`. Each run shows baseline and candidate as paired bars so absolute values are visible, not just deltas.

![MRR per A/B run (baseline vs candidate)](docs/ab/charts/mrr_over_time.png)

![hit@k per A/B run (baseline vs candidate)](docs/ab/charts/hitk_over_time.png)

![p95 query latency per A/B run (baseline vs candidate)](docs/ab/charts/p95_query_over_time.png)

Override the default with `--retrieval-mode <mode>`, `LEDGER_RETRIEVAL_MODE` env var, or `retrieval_mode` in `config.yaml`.

## Folder Layout

```
notes/
  00_inbox/         temporary capture (cleared on consolidation)
  01_identity/      core identity: mission, beliefs, models, strategies, narratives (id__*.md)
  02_facts/         stable truths (fact__*.md)
  03_preferences/   user preferences (pref__*.md)
  04_goals/         long-term objectives (goal__*.md)
  05_open_loops/    unresolved items (loop__*.md)
  06_concepts/      definitions and frameworks (concept__*.md)
  07_projects/      project-specific subfolders
  08_indices/       derived indices (timeline, tags, eval cases, signals)
  09_archive/       superseded notes
```

Each note has YAML frontmatter with `created`, `updated`, `tags`, `confidence`, `source`, `scope`, and `lang`. Identity notes also have `identity_type`. See `schema.yaml` for the full spec and `templates/` for starter templates.

## Identity Layer

Identity notes in `notes/01_identity/` capture who the user is — mission, beliefs, mental models, decision strategies, and personal narratives. These are high-signal, small files (max 5) that provide rich context for interpreting requests. They receive a retrieval score boost and are loaded automatically at session start.

```bash
ledger context --format identity   # list identity notes
ledger notes --type identity       # browse identity notes
```

## Privacy Fences

Wrap sensitive content in `<private>...</private>` tags to prevent it from entering the retrieval index, search results, or generated artifacts:

```markdown
Public context here.
<private>This will be stripped before indexing.</private>
More public content.
```

Privacy stripping runs on all ingestion paths: retrieval candidate building, Obsidian import, extraction, and session-end capture. Nested tags and unclosed fences are handled safely (unclosed tags strip the remainder to avoid leaking).

## Signal Feedback Loop

The ledger captures feedback signals — retrieval hits/misses, corrections, affirmations, and ratings — to improve retrieval ranking over time. Signals are stored as append-only JSONL and summarized into per-note scores that feed back into retrieval.

```bash
ledger signal add --type retrieval_hit --query "deploy" --note notes/02_facts/fact__k8s.md
ledger signal add --type correction --note notes/03_preferences/pref__x.md --detail "outdated"
ledger signal add --type rating --rating 8
ledger signal summarize            # rebuild signal_summary.json
ledger signal stats                # counts, top notes, coverage gaps
```

Signal scoring is disabled by default (`score_weight_signal: 0.0`) until enough data accumulates. Enable via `config.yaml` once you have 20+ signals.

## Session Lifecycle Hooks

Hook scripts under `scripts/hooks/` automate common session patterns:

- **`session_start.sh`** — loads boot context (identity notes, open loops, maintenance status, signal stats)
- **`post_write.sh`** — appends timeline entries after note operations
- **`session_end.sh`** — flushes signal summary, reports session activity

```bash
bash scripts/hooks/session_start.sh          # manual invocation
```

For Claude Code, configure hooks in `.claude/settings.json`. See `AGENTS.md` for integration details.

## Consolidation ("Electric Sheep")

Periodic maintenance keeps the ledger coherent as it grows:

```bash
ledger sleep sync --check && ledger sleep sync --apply
ledger sleep sleep
```

Sleep merges duplicates, promotes patterns into stable notes, updates indices, and tightens open loops.

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

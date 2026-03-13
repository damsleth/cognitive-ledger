# Cognitive Ledger

This repository is an **opinionated scaffold for a Cognitive Ledger**, a structured
note‑taking system designed to extend the temporal reach of language models and
their users.  It provides a simple, file‑based layout where each entry is
small, atomic and easy to search with standard command‑line tools.  The aim
is to build a durable, inspectable record of decisions, preferences, goals and
concepts without storing raw chat logs.

The high‑level structure is:

```text
.
├── AGENTS.md        – Instructions and quick reference for agents
├── README.md        – This file
├── .gitignore       – Ignore patterns for version control
├── skills/          – Canonical, tool-agnostic agent skills
├── notes/           – Primary storage for atomic notes
│   ├── 00_inbox/    – Temporary capture; cleared on consolidation
│   ├── 02_facts/    – Stable truths sourced from the user or tools
│   ├── 03_preferences/ – Recorded preferences and stylistic choices
│   ├── 04_goals/    – Longer‑term objectives
│   ├── 05_open_loops/ – Unresolved questions and pending actions
│   ├── 06_concepts/ – Definitions, frameworks and models
│   ├── 07_projects/ – Project‑specific subfolders (created on demand)
│   ├── 08_indices/  – Derived indices (timelines, tag maps)
│   └── 09_archive/  – Obsolete or superseded notes
├── templates/       – Reusable note templates
│   ├── generic_note_template.md
│   └── open_loop_template.md
└── scripts/         – Retrieval, eval, and maintenance helpers (`ledger`, `ledger_ab`, `sheep`)
├── ledger/          – Core Python library (see "Library Architecture" below)
```

This layout keeps individual files small and context‑portable.  Agents are
expected to consult **AGENTS.md** for guidance on when and how to write notes.

## Library Architecture

The `ledger/` package provides a modular Python library for working with notes:

```text
ledger/
├── __init__.py        – Package entry point (re-exports main APIs)
├── config.py          – Centralized configuration (LedgerConfig dataclass)
├── errors.py          – Exception hierarchy (LedgerError, ParseError, etc.)
├── validation.py      – Input validators (query, scope, paths)
├── parsing/           – Canonical parsing utilities
│   ├── frontmatter.py – YAML frontmatter extraction
│   ├── sections.py    – Markdown section splitting
│   ├── links.py       – Wiki/markdown link extraction
│   └── tokenizer.py   – Text tokenization for search
├── notes/             – Note models and type definitions
│   └── __init__.py    – BaseNote, LoopNote, GenericNote
└── io/                – File safety utilities
    └── safe_write.py  – Atomic writes, file locking
```

### Usage

```python
from ledger.config import get_config
from ledger.parsing import parse_frontmatter_text, parse_sections, extract_links
from ledger.notes import read_note, get_notes
from ledger.validation import validate_query
from ledger.io import atomic_write
```

## Skills

Skills live in `skills/` as the canonical, tool-agnostic location, and can be symlinked to your user skills folder (e.g. `~/.claude/skills/`) by running `./skills/install-skill.sh`.

## Obsidian Drop-In (MVP)

The repository now ships a Python-first Obsidian integration via `ledger-obsidian`.

### Install

```bash
pipx install cognitive-ledger
```

### Initialize in an existing vault

```bash
ledger-obsidian init --vault /path/to/your/obsidian-vault
```

This creates a dedicated `cognitive-ledger/` directory inside the vault and never edits source notes outside that subtree.

For a generic markdown note base such as `~/Code/notes`, the same drop-in flow works with the root alias or one-shot bootstrap command:

```bash
ledger-obsidian bootstrap --root ~/Code/notes
ledger-obsidian import --root ~/Code/notes
```

The importer treats `.obsidian/` as optional and still writes only under `cognitive-ledger/` inside the source tree.

### Key commands

```bash
ledger-obsidian import --vault /path/to/vault
ledger-obsidian bootstrap --root /path/to/note-base
ledger-obsidian import --root /path/to/note-base
ledger-obsidian watch --vault /path/to/vault
ledger-obsidian daemon start --vault /path/to/vault   # macOS
ledger-obsidian daemon status --vault /path/to/vault  # macOS
ledger-obsidian daemon stop --vault /path/to/vault    # macOS
ledger-obsidian doctor --vault /path/to/vault
ledger-obsidian queue sync --vault /path/to/vault
```

### Quality gates (default)

- `auto_write_confidence_min = 0.90`
- `queue_confidence_min = 0.70`
- `file_signal_min = 1.5`
- `max_files_per_cycle = 50`
- `max_notes_per_cycle = 100`

Candidates below auto-write confidence are queued into `cognitive-ledger/notes/00_inbox/` with `review_status: pending` for Bases triage.

## Python Environment (Single venv)

Use one repository-wide virtual environment for all Python tooling (ledger scripts, TUI, tests, and local embeddings by default):

```bash
./scripts/setup-venv.sh  # default: base + dev + embeddings
./scripts/setup-venv.sh --python python3.12 --recreate  # if torch wheels are missing
./scripts/setup-venv.sh --minimal  # base-only fallback
```

Once `.venv` exists:
- `./scripts/ledger`, `./scripts/ledger_ab`, and context builders auto-reexec into `.venv`
- `./scripts/sheep index` prefers `.venv/bin/python` when available
- TUI and binary build also use the same `.venv`

## Retrieval Evaluation and A/B Testing

Use `scripts/ledger` and `scripts/ledger_ab` to validate retrieval quality and compare branches:

```bash
./scripts/ledger eval --cases notes/08_indices/retrieval_eval_cases.yaml --k 3 --strict-cases
./scripts/ledger_ab --baseline-ref main --candidate-ref HEAD --eval-runs 7 --query-runs 5
```

`ab_eval.json` and `ab_eval.md` now include the original quality/latency gate plus:
- query-stage timings (`query_latency_ms`, `candidate_build_ms`, `prefilter_ms`, `shortlist_ms`, `scoring_ms`, `index_rebuild_ms`)
- context-size metrics (`boot_context_tokens`, `boot_context_bytes`, per-scope profile tokens, bundle token distribution, note-size aggregates)
- maintenance health metrics (`sync_drift_count`, `days_since_sleep`, `changes_since_sleep`, `lint_errors`, `lint_warnings`)
- an informational composite quality score that combines retrieval quality, context size, and query latency

For full A/B workflow, result interpretation, corpus-diff handling, and troubleshooting, see:
- `.doc/ab_testing.md`

## Native Embeddings (Local-First)

Embeddings are now first-class in ledger tooling (independent of Obsidian plugins).

- Default local backend/model: `local` + `TaylorAI/bge-micro-v2`
- OpenAI backend is explicit opt-in only (`--backend openai` / `--embed-backend openai`)
- OpenAI usage requires `OPENAI_API_KEY` and never silently falls back
- Retrieval corpus stays ledger-only (`notes/`)
- Optional source-note corpus (`<source-notes-root>`) is discovery-only via `discover-source`

Install/update dependencies:

```bash
./scripts/setup-venv.sh
# if needed, force a compatible interpreter:
./scripts/setup-venv.sh --python python3.12 --recreate
# base-only fallback if local embedding deps are not wanted:
./scripts/setup-venv.sh --minimal
```

Local embedding deps are pinned in `<ledger-root>/scripts/setup-venv.sh` (`sentence-transformers==2.7.0`, `transformers<5`, `numpy<2`) to avoid current `torch`/`numpy` breakage on some platforms.

Build semantic indices:

```bash
./scripts/ledger embed build --target ledger --backend local --model TaylorAI/bge-micro-v2
./scripts/ledger embed build --target source --backend local --model TaylorAI/bge-micro-v2 --source-root <source-notes-root>
./scripts/ledger embed status --target both
./scripts/ledger embed clean --target source
```

Query with hybrid semantic retrieval:

```bash
./scripts/ledger query "calendar constraints" --retrieval-mode semantic_hybrid --embed-backend local
```

Source discovery (never mixed into normal query ranking):

```bash
./scripts/ledger discover-source "calendar constraints" --source-root <source-notes-root> --limit 20
./scripts/ledger discover-source "calendar constraints" --embed-backend openai --allow-api-on-source
```

Artifacts:
- Local ignored state: `.smart-env/semantic/{ledger,source}/<backend>__<model_key>/{index.json,vectors.npy}`
- Tracked manifest: `notes/08_indices/semantic_manifest.json`

## TUI

A terminal interface for browsing and editing notes, built with Python + Textual.

### Quick Start

**Run with the shared venv (from repo root):**
```bash
./scripts/setup-venv.sh
./.venv/bin/python -m tui
```

**Or build standalone binary:**
```bash
./tui/build-tui.sh
./tui/dist/ledger-tui
```

The binary can run from anywhere — it defaults to `<ledger-root>` or uses the current directory if it contains `notes/`.

### Key Bindings

| Key | Action |
|-----|--------|
| `↑/↓` or `j/k` | Navigate tree |
| `Enter` | Select note / follow link |
| `1-5` | Filter by type (facts/prefs/goals/loops/concepts) |
| `0` | Show all types |
| `/` | Focus filter input |
| `e` | Edit in $EDITOR |
| `f` | Quick fix (confidence, source, scope, status) |
| `t` | Edit tags |
| `g` | Toggle graph panel |
| `Ctrl+L` | Run lint |
| `Ctrl+S` | Show status |
| `q` | Quit |

### Development

TUI source and build artifacts live in `tui/`:
- `.venv/` — Shared Python virtual environment for the whole repo
- `tui/build-tui.sh` — Build script for standalone binary
- `tui/dist/ledger-tui` — Compiled binary (14MB)

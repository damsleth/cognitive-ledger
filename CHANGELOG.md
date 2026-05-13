# Changelog

## Unreleased (0.3.0-dev)

### Added
- **Web UI (Phase 1).** New `ledger web` subcommand launches a local FastAPI server (default `http://127.0.0.1:8765`) for read-only browsing of the ledger. Phase 1 covers: sidebar by note type with counts, recent-activity index, per-type listings (`/browse/{type}`), loop status filtering, and single-note detail pages (`/note/{stem}`) with rendered markdown, frontmatter meta, and clickable wikilinks. Broken `[[...]]` targets are surfaced inline (dashed underline) and in a side panel. Server-rendered Jinja templates, no JS framework, dark-mode via `prefers-color-scheme`. Search, backlinks, and graph view land in later phases (see `.plans/41-web-interface-v1.md`).
- **`[project.optional-dependencies].web` extra** in `pyproject.toml` for `fastapi`, `uvicorn[standard]`, `jinja2`, `markdown-it-py`. The web UI prints an install hint if the extras are missing.

### Removed
- **TUI deleted.** The Textual-based `tui/` package (~1,900 lines) is gone, along with `tests/tui/` and `tests/tui_tests/` and the `textual` runtime dependency. The TUI duplicated retrieval logic from `ledger/` without adding value; the new web UI replaces it without introducing parallel parsing or store code.

## 2026-05-12 (0.2.5)

### Fixed
- **`_version()` in `ledger/conventions.py`** now reads `ledger.__version__` first and only falls back to `importlib.metadata.version("cognitive-ledger")`. The metadata path returns whatever wheel pip installed, which can drift behind the in-tree source (e.g. an editable install whose wheel metadata is stale), causing `--doctor` envelopes to mis-report the running version. Source-of-truth is now the code that's actually executing.

### Changed
- Version bumped to `0.2.5`.

## 2026-05-12 (0.2.4)

### Added
- **`ledger --doctor` + `sheep --doctor` + `ledger-obsidian --doctor`** for the mnem suite parity contract. Each binary emits the suite-standard health-check JSON envelope (`tool`, `version`, `ok`, `checks`, `installed`).
- **`ledger init --json`** action envelope so `mnem ledger init` returns a structured ok/error payload instead of human text.
- **`--json` on `ledger loops`, `ledger notes`, and the `sheep` subcommands** (`status`, `lint`, `index`, `sleep`, `sync`). Data-class commands now emit machine-readable output uniformly across the CLI.
- **Action envelopes on `ledger ingest`, `ledger embed`, and `ledger context build/profiles`** so action commands carry a terminal `{type:"result", ok, ...}` envelope when invoked with `--json`.
- **`ledger/conventions.py`** - shared helpers for the mnem suite CLI contract (envelope shapes, exit codes, stream routing). Lets the rest of the suite import the same contract module instead of re-implementing it.
- **Reserved-key contract test** pinning `ledger query --json` output (no top-level `ok` on success; reserved keys stay reserved).

### Changed
- README now points at the mnem suite as the umbrella project.
- Version bumped to `0.2.4`.

## 2026-05-08

### Changed
- **Tag pattern now allows Norwegian letters `æ`, `ø`, `å`.** `TAG_PATTERN` (`ledger/maintenance.py`) and `frontmatter.rules.tags.item_pattern` in `schema.yaml` updated from `^[a-z][a-z0-9_-]*$` to `^[a-zæøå][a-zæøå0-9_-]*$`. Allows tags like `røde-kors` and `østfold` without ASCII-mangling. Slug pattern unchanged (filenames stay ASCII to keep filesystem/URL portability).
- Version bumped to `0.2.3`.

## 2026-04-30

### Changed
- **Refactor: scripts to package modules.** The monolithic `scripts/ledger` (~1050 lines) and `scripts/ledger_ab` (~570 lines) are now thin entry points exposed by the installed `ledger` CLI (`pyproject.toml` declares `ledger = ledger.cli:main`). Logic lives in `ledger/cli.py`, `ledger/ab.py`, `ledger/ab_charts.py`, `ledger/embeddings.py`, and `ledger/__main__.py`.
- Compatibility shims `scripts/ledger`, `scripts/ledger_ab`, `scripts/sheep`, and the duplicate `scripts/ledger_embeddings.py` removed. Use `ledger ...`, `ledger ab run ...`, and `ledger sleep ...` instead.
- Hooks (`scripts/hooks/*.sh`) and `scripts/sheep-auto.sh` now require `ledger` on `$PATH`. Run `./scripts/add-to-path.sh` (pipx editable install) after cloning.
- `ledger ab run` and `ledger sleep` are pre-routed in `cli.main()` to work around argparse.REMAINDER misbehaving inside nested subparsers (bpo-9334).

### Fixed
- `ledger.__version__` now matches `pyproject.toml` (`0.2.2`).

### Added
- `tests/test_cli.py` covers the CLI dispatch surface: argument validation, subcommand routing, exit codes, JSON-vs-human output, and a regression guard for `__version__` consistency.

## 2026-04-28

### Added
- **`ledger init --root`** flag exposing the `init_ledger(root=...)` parameter on the CLI. Lets a brew-installed `ledger` scaffold a writable ledger root (e.g. `~/.config/cognitive-ledger`) without needing to set `LEDGER_ROOT` first.
- Consolidated A/B test plan archive under `.plans/done/19-*.md` through `.plans/done/32-*.md`, covering retrieval modes, semantic defaulting, external corpus validation, capture/privacy experiments, and future derived-context tests.
- A/B performance chart artifacts under `docs/ab/charts/` plus `docs/ab/performance_series.json` for MRR, hit@k, and p95 query latency per run (paired baseline-vs-candidate bars). Regenerate with `python scripts/build_ab_charts.py`.

### Changed
- README now embeds the three A/B performance-over-time charts and clarifies that `semantic_hybrid` is the default with `precomputed_index` fallback.
- A/B performance charts switched from delta plots to paired baseline-vs-candidate bars per run so the absolute metric reached is visible, not just the change.

## 2026-04-22

### Fixed
- `retrieval_mode`, `embed_backend`, and `embed_model` in `config.yaml` now act as CLI defaults, with environment variables still taking precedence.
- Obsidian import and queue promotion timeline writes now update both `timeline.jsonl` and `timeline.md` through the canonical append path.
- Session-end inbox capture now handles duplicate note titles and ignores `.lock` artifacts when reporting dirty note paths.
- Generated `note_index.json` now serializes logical `notes/...` paths instead of machine-local absolute paths.
- Obsidian daemon tests can redirect LaunchAgents plist writes with `LEDGER_LAUNCH_AGENTS_DIR`.
- `ledger init` now seeds notes-corpus `.gitignore` entries for lock files and ephemeral/generated index artifacts.

## 2026-04-18

### Fixed
- **`related_to_text()` crash** - function called `build_candidate_index()` and `score_candidate()` with wrong signatures, causing TypeError on any call. Source ingest pipeline and Obsidian `related` command were non-functional.
- **Semantic hybrid weights ignored config** - `rank_query_semantic_hybrid()` hardcoded weights (0.55, 0.30, 0.10, 0.05) instead of reading `config.semantic_weight_*` fields. Tuning via config.yaml or A/B harness env overrides now takes effect.
- **`reset_embeddings_cache()`** - added missing cache reset function for `_EMBEDDINGS_MODULE_CACHE` in `semantic.py`, matching the pattern of `clear_candidate_cache()` for test isolation.

### Changed
- **Deduplicated `canonical_scope()`** - removed identical copy in `context.py`, now imports from `retrieval.py` (the canonical location).
- **Consolidated recency score** - three identical 90-day linear decay implementations (in `retrieval.py`, `context.py`, `notes/__init__.py`) consolidated to single source in `retrieval.py`.
- **Removed dead `compressed_attention` code** - ~105 lines of unreachable code removed from `retrieval.py` after mode was already removed from `retrieval_modes`. AGENTS.md modes list updated to match.
- **Removed redundant local `import os`** in `retrieval.py` (already imported at module level).

### Added
- **Tests for `related_to_text()` and ingest pipeline** - 19 new tests covering the previously untested source ingest pipeline and related-text retrieval function.

## 2026-04-17

### Changed
- **Default retrieval mode is now `semantic_hybrid`** - A/B tested all modes. `semantic_hybrid` dominates: MRR 0.830 (+10.8%), hit@1 0.733 (+15.6%), hit@k 0.933 (+6.7%) vs legacy, and fastest at 2.4ms p95 (precomputed embeddings). Falls back gracefully to `precomputed_index` (best lexical mode, MRR 0.726) when no embedding index is built.
- **Removed `compressed_attention` mode** - Only mode that regressed on hit@k (-0.022). Code left in place for research but removed from available modes list.
- README updated with complete A/B results table, three-layer query docs, and privacy fences section

### Fixed
- **Semantic hybrid dependency blocker** - The venv was using x86_64 Python (Rosetta), which only has torch 2.2.x wheels. PyTorch stopped publishing macOS x86_64 wheels after 2.2.x, and sentence-transformers requires torch>=2.4. Fix: recreate venv with arm64 Python (`/opt/homebrew/bin/python3.12`). Installed: torch 2.11.0, numpy 2.4.4, sentence-transformers 5.4.1, transformers 5.5.4.

## 2026-04-16

### Added
- **Privacy fences** - `<private>...</private>` tag stripping in all ingestion paths (retrieval candidates, Obsidian import, extraction, session-end capture). Balanced-tag parser handles nested fences safely by over-redacting when unclosed.
- **Cost hints** - `word_count` field on `RetrievalCandidate` flows through scoring, serialization, and query output. Human-readable results show `~Nw` per result. Note index bumped to v3.
- **Activity type on timeline** - optional `activity_type` field on timeline JSONL entries (decision, bugfix, feature, refactor, discovery, change). Backward compatible - omitted when empty, not in markdown format.
- **Three-layer retrieval UX** - `--view index|context|detail` flag on `ledger query`. Index (~20-30 tokens/result) for scanning, context (default, ~80-120 tokens) for reasoning, detail (~200-1000 tokens) for full bodies. Agents start compact and drill into what they need.
- **Session wrap-up template** - structured 5-question prompt in `/notes` skill for surfacing durable artifacts at session end (task, explored, discovered, completed, still open).
- A/B baseline results for retrieval modes: full pairwise matrix across 10 experiments.

### Changed
- `schema.yaml` - added `activity_types` enum for timeline entries
- `SKILL.md` - query section rewritten with three-layer workflow, session wrap-up expanded
- `NOTE_INDEX_VERSION` bumped from 2 to 3 (forces rebuild to populate `word_count`)

## 2026-04-15

### Changed
- `ledger init` now writes `first_run: true` plus active `ledger_notes_dir` / `source_notes_dir` values into `config.yaml` when provided, and its initial index build now targets the configured external corpus instead of the in-memory default config.
- Session hooks and `sheep-auto.sh` now resolve `ledger_notes_dir` from config via `./scripts/ledger paths`, so config-only split-repo setups work without exporting `LEDGER_NOTES_DIR`.
- `scripts/ledger_ab` now uses the configured `ledger_notes_dir` as its default corpus and accepts direct external corpus roots (for example `~/Code/ledger-notes`) instead of assuming a bundled repo-local `notes/` sample tree.

### Added
- `ledger paths` CLI subcommand for printing resolved `ledger_root`, `ledger_notes_dir`, `source_notes_dir`, and `timeline_path`.

### Removed
- Bundled public `notes/` seed corpus from the repository; retrieval eval fixture comments now live under `tests/fixtures/`.

## 2026-04-08

### Changed
- Canonical ledger path naming is now `ledger_root`, `ledger_notes_dir`, and `source_notes_dir` across config, CLI, TUI, docs, and the `/notes` skill. Removed config/env names now fail fast with explicit migration errors, and ledger note references persist as logical `notes/...` paths even when the corpus lives outside the repo root.
- `/notes` skill now performs an environment preflight for `LEDGER_SOURCE_NOTES_DIR`, `LEDGER_ROOT`, and `LEDGER_NOTES_DIR`: if any are unset, the agent should prompt for the missing path(s) and advise adding the exports to `~/.zshrc` followed by `source ~/.zshrc`.

### Added
- **Voice DNA integration** - new `ledger/voice.py` module for importing, exporting, and retrieving voice-dna-creator profiles as identity notes. New `voice-dna` CLI subcommand (`import`, `show`). Added `voice` to `identity_type` enum, bumped `max_identity_notes` to 6.
- **Content index** - `sheep index` now generates `notes/08_indices/index.md` (human-readable) and `index.json` (machine-consumable) as a browseable catalog grouped by note type.
- **Obsidian retrieval API** - new `related_to_text()` function in `ledger/retrieval.py` for querying the ledger with arbitrary text. Exposed via `ledger-obsidian related --path <note> | --query <text>`.
- **Passive second-brain capture** - session hooks for automatic baseline tracking and end-of-session capture. New `ledger/inbox.py` for inbox triage. SKILL.md updated with passive capture policy.
- **Proactive assistant** - new `ledger/briefing.py` with daily/weekly briefings, loop nudging with staleness tracking. New `ledger briefing` CLI subcommand. New `scripts/sheep-auto.sh` for safe automated maintenance.
- **Ingest pipeline** - new `ledger/ingest.py` for source scanning, manifest diffing, and provenance tracking. New `ledger ingest` CLI subcommand. Cross-reference maintenance with `links.json` generation and orphan/broken link detection.
- **Batteries-included setup** - new `ledger/init.py` for one-command initialization. New `ledger init` CLI subcommand. Safer `install-skill.sh` that respects existing customizations.
- Voice DNA template (`templates/voice_dna_template.md`), inbox template (`templates/inbox_template.md`), ingest prompt template (`templates/ingest_prompt_template.md`)

### Changed
- `schema.yaml` - added `voice` identity type, `synthesized` recommended tag, bumped `max_identity_notes` to 6
- `AGENTS.md` - two-tier lookup strategy (context.md for boot, index.md for lookup), voice DNA, hook config docs, recommended setup section
- `SKILL.md` - boot sequence updated with voice DNA and content index, passive capture policy, ingest section, answer filing policy, session wrap-up section
- `install-skill.sh` - now checks for existing symlinks/directories before overwriting

## 2026-04-07

### Added
- **Identity layer** (PAI/TELOS-inspired) — new `identity` note type with `id__` prefix in `notes/01_identity/`. Captures mission, beliefs, mental models, strategies, and narratives. Identity notes receive a retrieval score boost and are included in boot context profiles. New `identity_type` frontmatter field.
- **Signal feedback loop** — new `ledger/signals.py` module with append-only JSONL storage (`signals.jsonl`) for retrieval hits/misses, corrections, affirmations, and ratings. Signal scores feed back into retrieval ranking when enabled. New `ledger signal` CLI subcommand (`add`, `summarize`, `stats`).
- **Session lifecycle hooks** — three hook scripts in `scripts/hooks/`: `session_start.sh` (boot context loader), `post_write.sh` (auto timeline append), `session_end.sh` (signal flush and session summary).
- `ledger context` CLI subcommand — generates boot payloads in three formats (`boot`, `identity`, `json`) for session-start automation.
- `score_weight_signal`, `signal_min_entries`, `identity_score_boost`, `boot_min_confidence` config parameters.

### Changed
- `AGENTS.md` — updated operating loop to 5 steps (added Signal step), added Identity Layer and Hooks sections, expanded folder map and file naming table, added signal capture guidelines.
- `SKILL.md` — routing table moved to top of intent mapping, added identity and signal capture entries.
- `schema.yaml` — added `identity` type, `identity_type` enum, signal system spec.
- `context.py` — boot context now includes Identity section, identity notes included in context profiles.
- `retrieval.py` — `score_candidate()` applies identity boost and optional signal score.
- Core note types expanded from 5 to 6 (added `identity`).

## 2026-03-31

### Added
- `config.yaml` - user-facing configuration file at repo root. Supports paths, retrieval tuning, scoring weights, and shortlisting params. Env vars override file values.
- `--corpus <path>` flag on `scripts/ledger_ab` for A/B testing against an external ledger instance instead of bundled sample notes
- `LEDGER_NOTES_DIR` env var to decouple note corpus from code root

## 2026-03-30

### Fixed
- Consolidated duplicate `EvalCaseValidationError` — was defined in both `ledger/eval.py` and `ledger/errors.py` with incompatible interfaces; now single definition in `errors.py` supporting both batch and single-case usage
- Semantic retrieval and embedding paths now respect `LEDGER_ROOT_DIR` instead of deriving from script location — fixes silent correctness issues for bootstrapped external ledgers
- TUI auto-discovery checks `LEDGER_ROOT_DIR` env var before falling back to cwd or `~/cognitive-ledger`
- Test suite: removed debug print statements from conftest, fixed ambiguous capsys assertion, cleared stale candidate cache in semantic hybrid test — **all 317 tests now pass** (was 316/317)

### Added
- Opt-in query telemetry log (`LEDGER_QUERY_LOG=1`) — appends JSONL to `notes/08_indices/query_log.jsonl` with query, scope, mode, top results, latency, and candidate count

### Changed
- `scripts/ledger` slimmed from 666 to 595 lines — removed redundant delegation layer and inline constant aliases, telemetry now handled by library
- Updated dependencies: textual 0.89→8.2, watchdog 4→6, sentence-transformers 2.7→5.3, removed numpy/transformers version pins
- Consolidated 7 doc files into single improvement plan, then split remaining TODO items into individual plans in `.doc/plans/`

## 2026-03-27

### Changed
- Reorganized README to focus on getting started, `/notes` skill, and plugging into existing repos
- Consolidated `.doc/` from 8 files into 1 (refactoring_2.md)
- Created `CLAUDE.md` pointing to `AGENTS.md`

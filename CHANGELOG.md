# Changelog

## Unreleased

### Added
- **Prior score (cold-start ranking, Mechanism 1) — applied as a TIE-BREAKER.** A prior term blends note confidence (importance), half-life recency decay, and query relevance, and is folded into the final score *only as a tie-breaker*: its contribution scales continuously to zero as a candidate's base (pre-prior) score gap to the local leader exceeds `prior_tie_band` (default 0.02, a 2% relative gap). A candidate with a clear base-score lead keeps its rank regardless of prior, so a strong semantic/lexical winner is never displaced — fixing the cold-start regression where a flat-additive prior reordered clear winners (real-corpus `semantic_hybrid` eval: restored hit@1 0.733 / hit@3 0.889 / mrr 0.804, up from 0.689 / 0.889 / 0.778 with the flat prior). On by default (`prior_enabled: true`). Controlled by `prior_weight` (0.10), `prior_tie_band` (0.02), `prior_w_importance` (0.30), `prior_w_recency` (0.30), `prior_w_relevance` (0.40), and `prior_recency_half_life_days` (180). Set `prior_enabled: false` to reproduce pre-prior scores exactly. Env overrides: `LEDGER_PRIOR_ENABLED`, `LEDGER_PRIOR_WEIGHT`, `LEDGER_PRIOR_TIE_BAND`, `LEDGER_PRIOR_W_IMPORTANCE`, `LEDGER_PRIOR_W_RECENCY`, `LEDGER_PRIOR_W_RELEVANCE`, `LEDGER_PRIOR_HALF_LIFE`. The blend lives in one shared place (`apply_prior_tiebreak`) used by both the lexical and `semantic_hybrid` paths.
- **Pseudo-Relevance Feedback / PRF (Mechanism 2, dense path).** Rocchio-style query-vector expansion using top-m pseudo-positive and bottom-n pseudo-negative results. Off by default (`prf_enabled: false`). Config keys: `prf_top_m` (3), `prf_bottom_n` (5), `prf_alpha` (1.0), `prf_beta` (0.75), `prf_gamma` (0.15). Per-query override: `ledger query --prf`. Env overrides: `LEDGER_PRF_ENABLED`, `LEDGER_PRF_ALPHA/BETA/GAMMA`. Enable only after `ledger ab run` confirms improvement.
- **RRF fusion mode (Mechanism 3, `semantic_hybrid`).** `fusion: rrf` generates independent lexical and semantic rank lists then merges them with Reciprocal Rank Fusion (smoothing constant `rrf_k`, default 60). Default is `fusion: weighted_sum` (byte-identical to previous behaviour). Env override: `LEDGER_FUSION`. Enable only after A/B eval. **Measured on the real corpus, RRF still underperforms `weighted_sum` (hit@1 0.467 vs 0.733, mrr 0.654 vs 0.804) even after the fix below — the literature `k=60` flattens rank differences on tiny candidate pools — so `weighted_sum` remains the default.**

### Fixed
- **RRF fusion no longer demotes pure-semantic winners.** The lexical rank list previously included only candidates with lexical overlap > 0, so a strong-semantic / zero-lexical note appeared in just one of the two fused lists while a weak-semantic but lexically-matched note appeared in both and accumulated roughly double the RRF score — systematically burying the true semantic winner (real-corpus `semantic_hybrid` hit@1 collapsed to 0.333). Both rank lists now cover the full candidate pool (zero-overlap candidates fall to the bottom of the lexical list deterministically), and the RRF score is normalised against the observed pool maximum instead of the theoretical rank-1-in-both maximum so scores use the full [0, 1] range rather than compressing into a tiny band where minor recency/scope terms dominated ordering. This lifts RRF to hit@1 0.467 / mrr 0.654, but it still trails `weighted_sum`; RRF stays opt-in.
- **`ledger signal seed` — LLM-judged synthetic signal bootstrapping.** Reads queries from `query_log.jsonl` (`--from-history`) or a plain-text file (`--queries-file`), retrieves top-k notes per query via the configured retrieval stack, and has an LLM judge score each (query, note) pair. Writes synthetic `llm_judged` signal events tagged `synthetic: true`. Judge backends: `dummy` (deterministic lexical heuristic, no network) and `subprocess` (configurable shell command, e.g. `claude -p`). Config: `judge_backend`, `judge_subprocess_command`, `judge_seed_top_k` (5). Env: `LEDGER_JUDGE_BACKEND`, `LEDGER_JUDGE_COMMAND`, `LEDGER_JUDGE_SEED_TOP_K`.
- **`ledger signal purge --synthetic` — rollback seeded signals.** Rewrites `signals.jsonl` in place, removing all entries where `synthetic: true`. Run `ledger signal summarize` afterwards to refresh `signal_summary.json`.
- **Synthetic signal down-weighting.** Synthetic events count as `synthetic_weight` (default 0.5) of a real signal in `summarize_signals`. The 20-signal gate (`signal_min_entries`) counts only real (non-synthetic) events so seeded signals cannot artificially activate scoring. Config: `synthetic_weight`. Env: `LEDGER_SYNTHETIC_WEIGHT`. New `real_signals` counter in `_meta` and `real_total` in `signal stats` output.
- **`llm_judged` signal type** added to `SIGNAL_TYPES`.
- **A/B harness applied env overrides to nothing.** `ledger ab run --baseline-env/--candidate-env LEDGER_WEIGHT_SIGNAL=0.1` (and the documented signal-weight recipe) reported metrics identical to the un-overridden side because the isolated probe matched override keys against config *field* names only — the documented `LEDGER_*` env-var form (`LEDGER_WEIGHT_SIGNAL`, `LEDGER_PRIOR_ENABLED`, …) never matched, so it was silently dropped. `LEDGER_*` keys are now pushed into the probe's environment before the config loads (so they reach **both** the eval and query probes), raw config-field keys still work as a fallback, and the values the probe actually applied are surfaced in the JSON report (`applied_env_overrides`) and the markdown "Config Overrides" table (requested vs. applied) so a run can prove the override took effect.
- **Same-ref A/B runs executed the wrong code.** When `--baseline-ref` and `--candidate-ref` resolved to the same commit, the harness probed `repo_root` directly — i.e. whatever branch was checked out — instead of the named ref, so `ab run --baseline-ref feat/X --candidate-ref feat/X` with `main` checked out benchmarked `main`. The harness now builds a worktree at the resolved commit when it differs from the current `HEAD`, and the report records the resolved commit instead of the literal ref string.
- **A/B refused-but-ran on missing refs.** A `--baseline-ref`/`--candidate-ref` that did not resolve fell through to a silent direct-probe of the current working tree. Unresolvable refs now fail loudly with `invalid_setup` (exit 4) naming the offending ref. `_resolve_repo_root` also rejects a `LEDGER_ROOT` that points at the note corpus instead of the cognitive-ledger source clone (it now requires `ledger/ab.py` to be present).
- **A/B defaulted to the `legacy` retrieval mode.** `--baseline-mode`/`--candidate-mode` hard-defaulted to `legacy`, so an A/B without explicit mode flags silently benchmarked a mode the ledger doesn't use. Both now default to the resolved configured `retrieval_mode` (e.g. `semantic_hybrid`), with the explicit flags still overriding; the resolved modes are printed in the run header and shown in the report.
- **PRF now runs against the real semantic index.** The PRF path previously depended on `item_vectors` in the semantic score payload, which the real embedding module did not return, so `prf_enabled`/`--prf` silently fell back to original cosine scores outside tests. It now loads the index vectors directly and re-embeds the query with the index's stored text template (e.g. `e5_prefix`) before Rocchio re-scoring.
- **Seeded judge events now match the `llm_judged` contract.** `ledger signal seed` now writes one synthetic `llm_judged` audit event per judged query/note pair. Relevant judged pairs count as down-weighted retrieval hits; irrelevant pairs do not create per-note stats or global demotions. If no top-k candidate is relevant, seeding writes one query-level synthetic `retrieval_miss` instead of one miss per rejected candidate.
- **Prior audit data is preserved in query output.** `prior_score` is now included in serialized score components, and scored result wrappers preserve `created_ts` so creation-age prior behavior remains inspectable through result transformations.
- **Invalid `fusion` values fall back consistently.** A mistyped `fusion` config/env value now uses and reports `weighted_sum` instead of running weighted-sum logic while surfacing the invalid string in metadata.

### Added
- **NLI-based contradiction scan (`ledger sleep contradictions`).** Detects when a note contradicts an existing note using a local transformer classifier (`MoritzLaurer/mDeBERTa-v3-base-mnli-xnli`, multilingual mDeBERTa). Off by default (`contradiction_enabled: false`); enable once the model is downloaded and the feature is validated on your corpus. Three outcomes per detected pair: auto-supersede (score ≥ `contradiction_auto_threshold`, unambiguous temporal ordering, no confidence inversion), route to `00_inbox` as a conflict note (score ≥ `contradiction_review_threshold`), or ignore. Identity notes (`01_identity/`) are never auto-superseded — always routed to review. State file at `notes/08_indices/contradiction_state.json` makes the scan idempotent.
- **Norwegian NLI caveat.** XNLI has no Norwegian training data; accuracy on `lang:no` notes is unvalidated. A stricter per-language threshold (`contradiction_auto_threshold_lang_no`, default 0.95 vs 0.85) gates auto-supersession on Norwegian-language content. Recommendation: hand-check ~20 real Norwegian pairs before trusting auto-resolution on a `lang:no` corpus; rely on the review path until validated.
- **Conflict inbox record convention.** When a contradiction pair is routed to review, a file named `conflict__{timestamp}__{stem_a}__{stem_b}.md` is written to `notes/00_inbox/` with frontmatter (`tags: [conflict, nli, review]`, `confidence` = NLI score, `source: inferred`) and a checklist for the human resolver (A supersedes B / B supersedes A / different contexts / both outdated). Duplicate records for the same pair are not created.
- **Seven new config keys** for contradiction detection: `contradiction_enabled` (bool, default false), `contradiction_model` (str), `contradiction_neighbors_k` (int, default 8), `contradiction_auto_threshold` (float, default 0.85), `contradiction_review_threshold` (float, default 0.60), `contradiction_auto_threshold_lang_no` (float, default 0.95), `contradiction_protect_higher_confidence` (bool, default true). All keys are documented in `config.sample.yaml` and have `LEDGER_CONTRADICTION_*` env overrides.
- **NLI wrapper (`ledger/nli.py`).** Lazy-loads a transformers zero-shot pipeline once per process (same singleton pattern as the cross-encoder reranker). `contradiction_score(a, b)` returns the bidirectional max of both NLI directions. Test seam via `_pipeline_fn` injection avoids model downloads in tests.
- **`sheep-auto.sh` contradiction probe.** The automated maintenance script runs `ledger sleep contradictions --check` (dry run only) when `contradiction_enabled=true` and appends the report to the run log. `--apply` requires agent judgment and must be run manually.
- **Bitemporal (valid-time) axis on notes.** Four optional frontmatter fields track when a fact was true in the world, independent of transaction time (`created`/`updated`): `valid_from`, `valid_to`, `superseded_by`, and `supersedes`. Applicable to fact-like note types (`01_identity`, `02_facts`, `03_preferences`, `04_goals`, `06_concepts`); `00_inbox` is exempt. All fields are optional — legacy notes without them lint clean and retrieve byte-identically.
- **`ledger query --as-of DATE`** temporal filter. Accepts `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SSZ`. Widens the candidate pool to include `09_archive` notes and returns only notes whose valid-time interval contains the given instant. Without `--as-of`, expired notes (non-null `valid_to` in the past) are hidden from default retrieval; notes with no validity fields are passed through unchanged.
- **`ledger migrate bitemporal --check / --apply`** back-fill command. `--check` (default) reports which notes are candidates for back-fill without modifying anything; `--apply` writes `valid_from` (derived from `created`) and, for archive notes, `valid_to` (derived from `updated`), then appends a timeline entry. Idempotent — already-set fields are not overwritten.
- **Bitemporal lint rules** in `ledger sleep lint`. Validates timestamp format, `valid_from <= valid_to` ordering, `superseded_by` requires a non-null `valid_to`, and dangling `superseded_by` references. Emits a `warn_bitemporal_null_valid_from` warning (not an error) on eligible notes that carry other validity fields but lack `valid_from`, suggesting `ledger migrate bitemporal --apply`.
- **`supersede()` primitive** in `ledger/bitemporal.py`. Sets `valid_to` on the old note, writes `superseded_by` pointing to the replacement, copies `supersedes` onto the new note, and moves the old file to `09_archive/`. Returns a `SupersessionResult` dataclass. Idempotent when called twice on the same pair.
- **Bitemporal fields on `RetrievalCandidate`** (`valid_from`, `valid_to`, `superseded_by`). Persisted in the note index only when set, keeping the index lean for legacy notes.
- **Schema entries** for all four bitemporal frontmatter fields in `schema.yaml` (optional, with type, pattern, and constraint notes).

## 2026-05-27 (0.4.3)

### Added
- **Signal-loop activation scaffolding.** The feedback loop shipped in 0.4.2 produced signals but left them inert for ranking (`score_weight_signal = 0.0`). This adds the means to validate and turn it on:
  - **Activation status** — `signal stats`, `review --stats`, and the web dashboard now report whether signal feedback influences ranking: `accruing` (below `signal_min_entries`), `ready` (enough signals but weight still 0, so ignored), or `active` (weight > 0). The `ready` state nudges you to validate and raise the weight.
  - **Web `/signals` dashboard** — a read-only page (sidebar link + colour-coded activation banner) showing coverage, score distribution, correction backlog, and top retrieval-miss gaps. Mirrors `ledger review --stats`.
  - **`LEDGER_WEIGHT_SIGNAL` env override** — wired into the config env mappings so `ledger ab run --candidate-env LEDGER_WEIGHT_SIGNAL=0.1` can A/B the signal weight against eval cases before you commit to raising `score_weight_signal` in `config.yaml`. Recipe documented in AGENTS.md.

## 2026-05-27 (0.4.2)

### Added
- **`ledger review` — scan-and-judge signal interface.** A keyboard-driven curses TUI that walks a *prioritized* review queue and lets you cast a one-keystroke verdict per note (`k` keep → affirmation, `w` wrong → correction, `s` stale → stale_flag, `1`–`9` rate, space/enter skip, `u` undo, `q` quit & save). The queue ordering is the insight: corrections-pending → high-traffic-but-never-affirmed → never-reviewed → stale-by-age → low-confidence/inferred surface first. Verdicts buffer and flush (rebuilding `signal_summary.json`) on exit. `--queue` prints the prioritized list and `--stats` prints a dashboard (coverage, score distribution, correction backlog, top retrieval-miss gaps) without launching the TUI. Closes the feedback loop that was previously open — nothing produced signals, so `signal stats` always read 0.
- **Use-time signal capture (opt-in).** With `signals_auto_capture: true` (config.yaml or `LEDGER_SIGNALS_AUTO_CAPTURE=1`), queries auto-log `retrieval_miss` when no result scores above `signals_miss_score_floor` (both CLI `ledger query` and the web `/search`). `ledger query "<topic>" --pick` prompts for the result that helped and logs a `retrieval_hit`; opening a note from a web search result does the same. Off by default to avoid noise; signal feedback stays inert for ranking until `signal_min_entries` accrue and `score_weight_signal` is raised above 0.

### Fixed
- **`ledger review` no longer crashes on terminal resize.** Resizing or splitting the pane makes curses `getch()` return `KEY_RESIZE`; that mapped to an empty key that was mistaken for a rating (`"" in "123456789"` is `True`), raising `ValueError` on `int("")`. Resize events are now handled explicitly (silent redraw) and the rating branch is guarded with `len(key) == 1`.

## 2026-05-27 (0.4.1)

### Fixed
- **`ledger init --json` now emits valid JSON.** `init_ledger()` runs `sheep index`, which prints progress to stdout; in `--json` mode that corrupted the envelope. The index output is now captured so only the JSON envelope reaches stdout.
- **`ledger notes --json` / `ledger loops --json` no longer drop fields.** An operator-precedence bug made `type` always `null` for the `BrowseItem` dataclass, and `status` was read as an attribute even though it lives in frontmatter (so loop `status` was always `null`). Both are now resolved correctly.

### Changed
- **Full test suite runs on the default dev setup.** Optional-dependency tests now skip cleanly when their deps are absent: `tests/web` skips without `fastapi`, the chart test skips without `matplotlib`. The context-profiles test invokes the venv interpreter (`sys.executable`) instead of a bare `python3`, and isolates `XDG_CONFIG_HOME`. Added a `test` extra (`pip install -e '.[test]'`) and a `--test` flag to `scripts/setup-venv.sh` for a no-skips run.

## 2026-05-27 (0.4.0)

### Changed
- **Config now lives with the installation, not the codebase.** The canonical user config moved to `$XDG_CONFIG_HOME/ledger/config.yaml` (i.e. `~/.config/ledger/config.yaml`). A `config.yaml` inside the source checkout (`<ledger_root>/config.yaml`) is **no longer read** — this keeps the line clean between the package, the config, and the ledger folder, so the source tree isn't needed at runtime. The pre-rename `$XDG_CONFIG_HOME/cognitive-ledger/config.yaml` is still read as a deprecated low-priority fallback. Resolution order is now: legacy XDG → canonical XDG → environment variables. `ledger init` writes to the XDG location (creating it, never clobbering), and the session-start hook reads the first-run flag from there.
- **`config.embed_model` is authoritative for the embedding model.** A new `configured_model_for_backend()` resolver (explicit arg → `config.embed_model` when the backend matches `config.embed_backend` → static default) is used by `embed build`, the query/semantic path, A/B builds, and `sheep index`. Previously these fell back to a hardcoded `TaylorAI/bge-micro-v2` whenever `--model` was omitted, ignoring config.

### Removed
- **`notes/` is untracked and gitignored.** The repo-local `notes/` folder was scratch space tooling could write into (e.g. a misfiring `sheep` run indexing the wrong corpus). It's removed from version control and `notes/*` is ignored so accidental writes never get committed.

### Fixed
- **`sheep index` no longer resurrects the default embedding model.** `_generate_semantic_index` hardcoded `TaylorAI/bge-micro-v2`, so every index run rebuilt and re-registered it — undoing `embed clean` and ignoring `config.embed_model`. It now builds the configured model.
- **`ledger embed clean` now prunes the manifest.** Previously it only `rmtree`'d the on-disk vectors under `.smart-env/semantic/<target>/`, leaving the `semantic_manifest.json` target entry pointing at directories that no longer existed (so `embed status` kept reporting stale models). Clean now removes the cleaned target's entry from the manifest, bumps `updated`, and appends a timeline entry — keeping the manifest consistent with what's actually on disk. Human output reports pruned entries.

## 2026-05-17 (0.3.1)

### Added
- **Web UI (Phase 3) - backlinks.** Note detail pages now render two new panels: "Linked from" (incoming wikilinks, with target titles) and "Links out" (outgoing wikilinks). Empty incoming state renders a hint. Broken links panel preserved.
- **`Corpus` link maps.** New `outgoing_stems()`, `incoming_stems()`, `broken_outgoing()`, and `link_titles()` methods. Built in a single pass over note bodies using the shared `extract_links` parser; rebuilt on `reload()`.
- **`GET /healthz`** - JSON status probe (`ok`, `notes_loaded`, `embeddings_enabled`, `index_built_at`). Cheap; does not import the embeddings module.
- **`POST /admin/reload`** - rescans the corpus from disk and invalidates the search cache. Use after `ledger sleep index` to pick up new notes without restarting the server.
- **`type_label` Jinja filter** - normalizes plural type keys (`facts`, `loops`) to singular labels (`fact`, `loop`) for the note-type pills in search results, browse, and note detail.
- **Vendored static asset README** (`ledger/web/static/README.md`) documenting htmx version + provenance.

## 2026-05-15 (0.3.0)

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
- **`ledger --doctor` + `sheep --doctor` + `ledger-obsidian --doctor`** for the hugr suite parity contract. Each binary emits the suite-standard health-check JSON envelope (`tool`, `version`, `ok`, `checks`, `installed`).
- **`ledger init --json`** action envelope so `hugr ledger init` returns a structured ok/error payload instead of human text.
- **`--json` on `ledger loops`, `ledger notes`, and the `sheep` subcommands** (`status`, `lint`, `index`, `sleep`, `sync`). Data-class commands now emit machine-readable output uniformly across the CLI.
- **Action envelopes on `ledger ingest`, `ledger embed`, and `ledger context build/profiles`** so action commands carry a terminal `{type:"result", ok, ...}` envelope when invoked with `--json`.
- **`ledger/conventions.py`** - shared helpers for the hugr suite CLI contract (envelope shapes, exit codes, stream routing). Lets the rest of the suite import the same contract module instead of re-implementing it.
- **Reserved-key contract test** pinning `ledger query --json` output (no top-level `ok` on success; reserved keys stay reserved).

### Changed
- README now points at the hugr suite as the umbrella project.
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

# Cognitive Ledger — Technical Improvement Plan

> Generated 2026-02-23 · Consolidated 2026-03-27
> Incorporates: REFACTOR_PLAN, OBSIDIAN_PLAN, REVIEW 2026-03-10, REVIEW 2026-03-12, A/B testing guide

---

## Current State Snapshot

| Metric | Value |
|--------|-------|
| Active notes | 102 |
| Timeline entries | 198 |
| `scripts/ledger` | 2,127 lines (monolith) |
| `scripts/sheep` | 596 lines (bash) |
| `ledger/` library | ~1,650 lines across 9 modules |
| TUI models + services | 588 lines |
| Tests | 317 (316 passing, 1 failing) |
| `ledger/retrieval.py` | 167 lines (utilities only) |

---

## Implementation Status

| Phase | Status |
|-------|--------|
| 1.1 Extract retrieval | DONE |
| 1.2 Rewrite sheep in Python | DONE |
| 2.1 Persistent metadata cache | DONE |
| 2.2 Persistent inverted index | DONE |
| 3.1 BM25 scoring | DONE |
| 3.2 Automatic alias discovery | DONE |
| 4.1 Unify TUI/library note models | TODO — needs design session |
| 5.1 Structured timeline (JSONL) | DONE |
| 5.2 Query telemetry log | DONE |
| 6.1 CLI refactor | DONE — 595 lines |
| 6.2 TUI watch mode | DONE |
| Obsidian boundary (phases 1-2) | DONE |
| Obsidian boundary (phases 3-4) | TODO |
| Context extraction to ledger/ | DONE |
| Structured return types | DONE |
| Pluggability (semantic, CLI, TUI) | DONE |
| Bug: duplicate EvalCaseValidationError (C6) | FIXED |
| Test cleanup (T3, T4, T5, T6) | FIXED |
| Test coverage gaps | TODO |

All bugs from QA reviews verified fixed as of 2026-03-30.

---

## Phase 1: Structural Extraction (Highest Impact)

### 1.1 Extract Retrieval Pipeline from `scripts/ledger` into `ledger/` — DONE

Retrieval logic now lives in `ledger/retrieval.py`. `rank_lexical()`, `build_candidates()`, scoring, shortlisting, and prefiltering are all library functions. Eval framework lives in `ledger/eval.py`.

### 1.2 Rewrite `scripts/sheep` in Python — TODO

**Problem:** `sheep` is a 596-line bash script that:

- Parses YAML frontmatter via `sed`/`grep` (not a real parser)
- Has its own validation logic separate from `ledger/validation.py`
- Depends on `gdate` (GNU coreutils) on macOS
- Cannot share lint rules with the Python library

**Approach:**

```
scripts/sheep (bash, 596 lines)
       ↓ rewrite
scripts/sheep (thin bash wrapper → python)
       ↓ calls
ledger/maintenance.py (new)
  - cmd_status() — time/changes since last sleep
  - cmd_lint() — frontmatter validation using ledger.parsing + ledger.validation
  - cmd_index() — regenerate derived indices
  - cmd_sleep() — consolidation checklist
```

Keep the bash entry point as a two-line shim for backward compatibility:

```bash
#!/usr/bin/env bash
exec "$(dirname "$0")/../.venv/bin/python" -m ledger.maintenance "$@"
```

**Validation:** Capture current `sheep lint` output for all notes, rewrite, verify identical warnings/errors, add unit tests.

---

## Phase 2: Persistent Index (Performance)

### 2.1 Build a Persistent Metadata Cache — DONE

`rebuild_note_index()` checks mtime + SHA-256 before re-parsing. `note_index.json` stores entries with frontmatter, tokens, and snippets.

### 2.2 Persistent Inverted Token Index — TODO

Extend `note_index.json` with an inverted section mapping tokens → note paths for O(1) candidate retrieval:

```json
{
  "inverted": {
    "calendar": ["notes/02_facts/fact__calendar_constraints.md", ...],
    "python": ["notes/06_concepts/concept__python_tooling.md", ...]
  }
}
```

This already partially exists as `build_candidate_index()` in the script but is built from scratch on every cold start.

---

## Phase 3: Retrieval Quality

### 3.1 Add BM25 Scoring — DONE

BM25 integrated into `score_candidate()` with configurable weights. Scoring weights now use `LedgerConfig` directly.

### 3.2 Automatic Alias Discovery — TODO

Auto-generate alias candidates from note content:

1. During `sheep index`, scan all note titles and tags
2. Build co-occurrence pairs (tags that appear together frequently)
3. Extract noun phrases from titles/statements
4. Write suggested aliases to `aliases_suggested.json`
5. Human reviews and promotes to `aliases.json`

---

## Phase 4: Model Unification — TODO

### 4.1 Unify TUI and Library Note Models

Two parallel type hierarchies exist:

```
ledger/notes/:         BaseNote → LoopNote, GenericNote  (326 lines)
tui/models/:           Note, Frontmatter, NoteType, ...  (150 lines)
tui/services/parser:   NoteParser (wraps ledger.parsing)  (129 lines)
```

**Approach:** Extend library models to be the single source; TUI wraps via composition:

```python
# TUI wraps rather than redefines:
@dataclass
class TUINote:
    base: BaseNote                    # composition over inheritance
    incoming_links: list[Path]        # TUI-specific (computed by NoteStore)
    lint_warnings: list[LintWarning]  # TUI-specific (computed by SheepRunner)
```

---

## Phase 5: Timeline & Observability

### 5.1 Structured Timeline (JSONL) — TODO

Replace `timeline.md` (pipe-delimited text) with `timeline.jsonl` as machine source of truth:

```json
{"ts":"2026-02-15T10:30:00Z","action":"created","path":"notes/02_facts/fact__example.md","desc":"New fact","type":"fact"}
```

Keep `.md` as a generated human-readable view via `sheep index`.

### 5.2 Query Telemetry Log — TODO

Optional telemetry file (`08_indices/query_log.jsonl`), disabled by default, enabled via `LEDGER_QUERY_LOG=1`. Captures query, scope, mode, top results, latency.

---

## Phase 6: Developer Experience

### 6.1 CLI Refactor — PARTIAL

Context extraction to `ledger.context` is done. `scripts/build_context.py` and `scripts/build_context_profiles.py` are thin wrappers. `scripts/ledger` still > 500 lines (target: < 500). Follow-on: pull A/B probe/report assembly out of `scripts/ledger_ab` into importable library code.

### 6.2 Watch Mode for TUI — TODO

Use `watchdog` or poll-based refresh so TUI reflects on-disk changes without restart.

---

## Phase 7: Obsidian Boundary

### Goal

Keep `ledger/obsidian/` as an optional adapter. Core ledger modules must not depend on `ledger.obsidian`.

### Current Surface

CLI: `init`, `bootstrap`, `import`, `watch`, `daemon {start,status,stop}`, `doctor`, `queue sync`
Python exports: `main`, `load_config`, `run_import`, `sync_queue`

### Phases 1-2 — DONE

- Import/dependency audit complete
- One-way dependency rule enforced
- `__init__.py` limited to supported exports

### Phase 3: Clarify Runtime Surfaces — TODO

- Separate long-running concerns from import logic (import/bootstrap, queue sync, watch loop, macOS daemon wrapper)
- Ensure `doctor` reports only adapter concerns, not duplicated validation

### Phase 4: Test and Prune — TODO

- Add tests for: root bootstrap/import, vault bootstrap/import, queue sync transitions, doctor output, watch/daemon wiring
- Archive or delete Obsidian helpers not on a supported command path

### Acceptance Criteria

- One canonical parsing/frontmatter path
- Root-mode and vault-mode import share the same pipeline
- Supported CLI commands covered by targeted tests
- Dead/duplicate Obsidian code removed

---

## Phase 8: Structured Return Types — PARTIAL

Dataclasses in `ledger/retrieval_types.py` for candidates, scored results, timings, and retrieval payloads. Some code paths still use `dict[str, Any]`.

---

## Pluggability Issues (from QA Review 2026-03-14)

### P1: Semantic retrieval not fully pluggable

Lexical retrieval honors `LedgerConfig` / `LEDGER_ROOT_DIR`, but embeddings still bind note paths, semantic artifact paths, and manifests to the clone's own filesystem location.

- **Relevant code:** `ledger/config.py`, `ledger/query.py`, `scripts/ledger_embeddings.py`
- **Impact:** Bootstrapped external ledger returns correct lexical results but semantic retrieval reads/writes indices from the repo clone. Silent correctness issue.
- **Priority:** Highest — blocks the public repo's stated drop-in goal.

### P2: CLI side effects pinned to repo root

Some outputs still default to repo-root paths rather than the configured ledger root:
- Query telemetry defaults to `ROOT_DIR / "notes" / "08_indices" / "query_log.jsonl"`
- `eval --write-baseline` requires output path inside the repo clone

### P2: TUI discovery does not match documented workflow

TUI auto-discovers only `cwd` (if it contains `notes/`) or `~/cognitive-ledger`. Does not discover common drop-in locations like `~/Code/notes/cognitive-ledger`.

- **Relevant code:** `tui/__main__.py`, `README.md`
- **Fix:** Either broaden auto-discovery or narrow the docs to match current behavior.

### Pluggability Fix Priority

1. Make semantic indexing and retrieval respect configured ledger root
2. Move CLI side-effect paths (telemetry, eval outputs) onto config-aware defaults
3. Align TUI auto-discovery with documented workflow

---

## Open Bugs (from QA Review 2026-03-12)

### Critical

| # | Location | Issue |
|---|----------|-------|
| C1 | `ledger/obsidian/extraction.py:39` | **Broken regex** — `r"\\b"` produces literal `\b`, not word boundary. `is_meeting_like` never fires. |
| C2 | `ledger/io/safe_write.py:126` | **FD leak** — If `os.fdopen(fd, ...)` raises, raw FD from `mkstemp` is never closed. |
| C3 | `ledger/retrieval.py:1104` | **Mutation of cached objects** — `apply_progressive_disclosure` mutates `ScoredResult` in-place; objects may be shared via `_CANDIDATE_CACHE`. |
| C4 | `tui/screens/main_screen.py:400` | **Prompt injection** — User query text interpolated unsanitized into `codex` subprocess. Needs `shlex.quote()`. |
| C5 | `ledger/obsidian/queue.py:88` | **Path traversal** — `promoted_path` from frontmatter used without vault containment check. |
| C6 | `ledger/eval.py:40` vs `ledger/errors.py:219` | **Duplicate exception class** — `EvalCaseValidationError` in two places with incompatible interfaces. |
| C7 | `ledger/query.py:80` | **JSON serialization crash** — `note_tokens`/`tag_tokens` stored as `set`, which `json.dumps` can't serialize. |

### Important

| # | Location | Issue |
|---|----------|-------|
| I1 | `validation.py:21`, `config.py:248`, `errors.py:160` | **Scope set divergence** — Three hardcoded scope lists; `"life"` valid in validation but absent from config/errors. |
| I2 | `config.py:363`, `retrieval.py:57` | **Thread-safety** — Module-level singletons use unguarded double-check pattern. |
| I3 | `context.py:294`, `maintenance.py:605` | **Non-atomic writes** — Index files use bare `write_text` instead of `safe_write_text`. |
| I4 | `obsidian/utils.py:88` | **Boundary violation** — `frontmatter_to_text` re-implements YAML serializer, duplicating `ledger.parsing`. |
| I5 | `obsidian/watch.py:22` | **Thread race** — `changed_paths` set mutated from watchdog thread without lock. |
| I6 | `obsidian/importer.py:209` | **TOCTOU race** — `exists()` then write for collision-suffix filenames. |
| I7 | `obsidian/daemon.py:58` | **XML injection** — `vault_root` interpolated into plist XML without escaping. |
| I8 | `tui/screens/main_screen.py:551` | **Blocking I/O on event loop** — `_poll_file_changes` runs sync disk I/O on Textual event loop. |
| I9 | `tui/services/note_writer.py:27` | **TOCTOU in update_frontmatter** — File read outside lock, write inside lock. |
| I10 | `scripts/ledger_ab:24` | **Stale constant** — `RETRIEVAL_MODES` hardcoded locally instead of imported. |
| I11 | `scripts/ledger_embeddings.py:34` | **Divergent default** — `DEFAULT_SOURCE_ROOT` is `~/Code/notes` vs `~/notes` everywhere else. |
| I12 | `parsing/frontmatter.py:116` | **YAML null handling** — `parse_scalar` returns `"null"` for YAML `null`/`~` instead of `None`. |
| I13 | `ledger/query.py:428` | **Budget overshoot** — `bundle_results` minimum 40 words means last item can exceed budget. |
| I14 | `obsidian/cli.py:52` | **Exit code mismatch** — Returns exit code 1 for optional daemon auto-start failure. |
| I15 | `obsidian/utils.py:133` | **Non-atomic append** — `append_log` uses read-modify-write; concurrent runs corrupt log. |

### Test Issues

| # | Issue |
|---|-------|
| T1 | `tests/test_ledger.py:577` — `LedgerIntegrationTests` run against real notes directory, no isolation. |
| T2 | `tests/test_context_profiles.py:13` — Subprocess test reads live repo notes. |
| T3 | `tests/tui/conftest.py:12` — Debug `print` statements left in conftest. |
| T4 | Identical fixtures duplicated across `tests/tui/conftest.py` and `tests/tui_tests/conftest.py`. |
| T5 | `NamedTemporaryFile(delete=False)` leaks temp files on failure; should use `TemporaryDirectory`. |
| T6 | `tests/test_maintenance.py:135` — `capsys.readouterr()` called once after two operations. |

### Test Coverage Gaps

No dedicated tests for: `ledger/validation.py`, `ledger/ab_probe.py`, `ledger/venv.py`, `ledger/retrieval_types.py`, 11 `ledger/obsidian/` modules, `tui/services/sheep_runner.py`, most TUI widgets/screens.

**Failing test:** `test_rank_query_semantic_hybrid_returns_typed_payload` — expects `fact__semantic.md` as top result but BM25/lexical scoring outweighs injected semantic score (0.93).

### Recommended Fix Priority

1. **C1, C4, C6, C7** — Incorrect behavior or crashes in normal operation
2. **C2, C3, C5** — Safety/correctness under edge conditions
3. **I1, I4, I11** — Divergent constants and boundary violations
4. **T1, T2** — Flaky tests undermine CI reliability
5. **I2, I5, I6, I8, I9** — Concurrency issues (prioritize if multi-threaded use planned)

---

## Implementation Sequence

```
Phase 1.2  Rewrite sheep ──────────┐
                                    ├──→ Phase 2.2  Inverted index
                                    │
                                    ├──→ Phase 3.2  Alias discovery
                                    │
                                    ├──→ Phase 4.1  Model unification
                                    │
                                    └──→ Phase 5.1  JSONL timeline

Phase 5.2  Query telemetry ────────── (independent, any time)
Phase 6.1  CLI refactor ──────────── (continue simplifying scripts/ledger)
Phase 6.2  TUI watch mode ────────── (independent)
Phase 7.3–7.4  Obsidian boundary ── (independent)
Phase 8  Structured return types ── (ongoing)
Phase 7.*  Future ─────────────────── (as needed)
```

**Critical path:** 1.2 → 2.2 (sheep rewrite unlocks inverted index work)

---

## Future Considerations (Lower Priority)

### 7.1 Confidence Evolution

Add `references` counter tracking when a note is cited in retrieval or linked. Suggest confidence promotions when a hypothesis has been referenced N times without contradiction.

### 7.2 Graph Export

Export link graph as DOT or GraphML. TUI graph panel data could feed this directly.

### 7.3 REST/WebSocket API

Wrap the library in a lightweight API for non-CLI tools, browser extensions, or remote agents.

### 7.4 Multi-User / Team Support

Add `author` frontmatter field, per-user scopes, merge strategy for concurrent edits. Only if concrete multi-user use case emerges.

---

## Appendix: A/B Testing Reference

### Purpose

Use `scripts/ledger_ab` to compare retrieval quality and latency between two git refs.

### What It Measures

- **Quality:** `hit1`, `hitk`, `mrr` from `scripts/ledger eval`
- **Latency:** `eval` probe p95, `query` probe p95 (candidate cache reset between runs; optional `--cold-query` resets cache per case)

**Decision policy:**
- `regression` (exit 2) if any quality metric drops
- `beneficial` (exit 0) if any quality metric improves
- Quality ties → latency thresholds decide: pass → `beneficial`, fail → `neutral` (exit 3)
- `invalid_setup` (exit 4)

### Prerequisites

Validate cases first:

```bash
./scripts/ledger eval --cases notes/08_indices/retrieval_eval_cases.yaml --k 3 --strict-cases
```

### Standard Branch-vs-Branch Run

```bash
./scripts/ledger_ab \
  --baseline-ref main \
  --candidate-ref HEAD \
  --runs 5 \
  --out-dir /tmp/ledger-ab
```

### Mode Comparison (same ref, different retrieval modes)

```bash
./scripts/ledger_ab \
  --baseline-ref HEAD \
  --candidate-ref HEAD \
  --baseline-mode legacy \
  --candidate-mode semantic_hybrid \
  --runs 5 \
  --query-runs 5 \
  --out-dir /tmp/ledger-ab-semantic-hybrid
```

### Cold-Query Variant

```bash
./scripts/ledger_ab \
  --baseline-ref main \
  --candidate-ref HEAD \
  --query-runs 5 \
  --cold-query \
  --out-dir /tmp/ledger-ab-cold-query
```

### `--allow-corpus-diff`

Default requires baseline/candidate corpus fingerprint equality. Use `--allow-corpus-diff` when the benchmark corpus intentionally changed.

### Working Tree Caveat

`ledger_ab` compares git refs, not uncommitted edits. To test uncommitted work:

```bash
CANDIDATE_REF=$(git stash create "ab-temp-candidate")
./scripts/ledger_ab --baseline-ref HEAD --candidate-ref "$CANDIDATE_REF" --allow-corpus-diff
```

### Interpreting Results

- Read `decision.reason` first
- Quality changes → inspect `decision.quality_deltas`
- Tie-break → inspect `decision.latency.eval/query` and thresholds
- `invalid_setup` → check missing refs, missing cases, corpus mismatch

---

## Success Metrics

| Metric | Current | Target (after Phase 3) |
|--------|---------|----------------------|
| Cold query latency (100 notes) | ~200ms | < 50ms |
| Cold query latency (1,000 notes) | ~2–3s (projected) | < 200ms |
| Eval hit@3 | baseline | >= +5% improvement |
| Retrieval testable in isolation | Yes | Yes |
| Lint rule parity (Python vs bash) | Approximate | Exact |
| `scripts/ledger` size | 2,127 lines | < 500 lines |

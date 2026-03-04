# Cognitive Ledger — Technical Improvement Plan

> Generated 2026-02-23 · Ordered by impact · All estimates assume solo developer

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
| Tests | 4,016 lines |
| `ledger/retrieval.py` | 167 lines (utilities only) |

---

## Phase 1: Structural Extraction (Highest Impact)

### 1.1 Extract Retrieval Pipeline from `scripts/ledger` into `ledger/`

**Problem:** The scoring, ranking, shortlisting, and candidate-building logic
(~1,200 lines) lives in `scripts/ledger` — a CLI script — instead of the
library. This means:

- Retrieval logic cannot be unit-tested without invoking the CLI
- The TUI cannot reuse ranked retrieval (it has its own substring search)
- Other consumers (API, Codex, future agents) must shell out to `scripts/ledger`
- Business logic is tangled with presentation (curses UI, output formatting)

**Scope:** Move the following functions from `scripts/ledger` into library modules:

| Function(s) | Current Location | Target Module |
|---|---|---|
| `candidate_from_note()`, `build_candidates()`, `build_candidate_index()`, `retrieve_candidates_from_index()`, `clear_candidate_cache()` | script L428–564 | `ledger/retrieval.py` |
| `score_candidate()`, `coarse_candidate_score()` | script L616–955 | `ledger/retrieval.py` |
| `shortlist_candidates()`, `shortlist_attention_candidates()`, `prefilter_candidates_by_scope_and_type()` | script L652–798 | `ledger/retrieval.py` |
| `_rank_query_lexical()` | script L958+ | `ledger/retrieval.py` (as `rank_lexical()`) |
| `build_attention_tokens()` | script L410–425 | `ledger/retrieval.py` |
| Eval framework (`cmd_eval`, case loading, metrics) | script (bottom) | `ledger/eval.py` (new) |

**After extraction, `scripts/ledger` becomes:**

```python
# ~300 lines: argument parsing, output formatting, curses UI
from ledger.retrieval import rank_lexical, build_candidates
from ledger.eval import run_eval_cases, compute_metrics

def cmd_query(args):
    results = rank_lexical(args.query, scope=args.scope, limit=args.limit)
    for item in results:
        print(format_result(item, args.width))
```

**Validation strategy:**

1. Extract functions with zero behavioral changes (copy-paste, fix imports)
2. Run existing A/B eval suite: `./scripts/ledger eval --cases ... --k 3`
3. Verify identical output for 10 sample queries via diff
4. Run full test suite

**Estimated effort:** 2–3 sessions
**Risk:** Low (pure refactor, eval suite catches regressions)
**Impact:** Enables all subsequent improvements (TUI retrieval, API, testing)

---

### 1.2 Rewrite `scripts/sheep` in Python

**Problem:** `sheep` is a 596-line bash script that:

- Parses YAML frontmatter via `sed`/`grep` (not a real parser)
- Has its own validation logic separate from `ledger/validation.py`
- Depends on `gdate` (GNU coreutils) on macOS
- Cannot share lint rules with the Python library
- Is harder to test than Python code

This means `sheep lint` and Python-side validation can disagree on edge cases
(e.g., inline YAML lists, quoted strings, multiline tags).

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

**Validation strategy:**

1. Capture current `sheep lint` output for all notes
2. Rewrite, verify identical warnings/errors
3. Add unit tests for each lint rule

**Estimated effort:** 2 sessions
**Risk:** Low (lint rules are well-defined in `schema.yaml`)

---

## Phase 2: Persistent Index (Performance)

### 2.1 Build a Persistent Metadata Cache

**Problem:** `build_candidates()` reads and parses every `.md` file on every
cold query. At 102 notes this takes ~200ms. At 1,000 notes it would take 2–3s.
At 5,000 notes it becomes impractical.

**Design:**

```
notes/08_indices/note_index.json
{
  "version": 2,
  "built": "2026-02-23T12:00:00Z",
  "entries": {
    "notes/02_facts/fact__example.md": {
      "mtime": 1708700000.0,
      "content_hash": "sha256:abc123...",
      "title": "Example Fact",
      "frontmatter": { "tags": [...], "scope": "dev", ... },
      "note_tokens": ["example", "fact", ...],
      "tag_tokens": ["example"],
      "snippet": "This is an example fact about..."
    }
  }
}
```

**Incremental rebuild logic:**

```python
def rebuild_index(index_path, notes_dir):
    existing = load_index(index_path)
    updated = {}
    for path in notes_dir.rglob("*.md"):
        rel = str(path.relative_to(root))
        current_mtime = path.stat().st_mtime
        cached = existing.get(rel)
        if cached and cached["mtime"] == current_mtime:
            updated[rel] = cached  # skip re-parse
        else:
            updated[rel] = parse_and_index(path)  # re-parse changed file
    save_index(index_path, updated)
```

**Performance target:**

| Operation | Current (102 notes) | After (1,000 notes) |
|-----------|-------------------|---------------------|
| Cold query | ~200ms | ~50ms (index read) |
| Warm query | ~200ms (no cache) | ~5ms (in-memory) |
| Index rebuild | N/A | ~100ms (incremental) |

**Integration points:**

- `build_candidates()` reads from index instead of scanning files
- `sheep index` rebuilds the metadata index
- `safe_write_text()` could optionally invalidate the relevant index entry
- TUI `NoteStore.load_all()` uses index for initial load

**Estimated effort:** 2–3 sessions
**Risk:** Medium (cache invalidation is inherently tricky — use mtime + content hash as belt-and-suspenders)

---

### 2.2 Persistent Inverted Token Index

**Problem:** Even with a metadata cache, scoring still iterates all candidates
to compute token overlap. An inverted index maps tokens → note paths for O(1)
candidate retrieval.

**Design:** Extend `note_index.json` with an inverted section:

```json
{
  "inverted": {
    "calendar": ["notes/02_facts/fact__calendar_constraints.md", ...],
    "python": ["notes/06_concepts/concept__python_tooling.md", ...],
    ...
  }
}
```

**This already partially exists** as `build_candidate_index()` in the script
(L549–564), but it's built from scratch on every cold start. Making it
persistent is a natural extension of 2.1.

**Estimated effort:** 1 session (after 2.1 is done)
**Risk:** Low

---

## Phase 3: Retrieval Quality

### 3.1 Add BM25 Scoring as Lightweight Semantic Layer

**Problem:** Pure lexical overlap (token intersection / query length) doesn't
account for term frequency or document length. A note that mentions "python"
once scores the same as one that's entirely about Python.

**Solution:** Add `rank_bm25` (pure Python, ~50KB, no native deps):

```bash
pip install rank-bm25  # adds to pyproject.toml
```

```python
from rank_bm25 import BM25Okapi

# Build corpus from note tokens (done at index time)
corpus = [list(note["note_tokens"]) for note in candidates]
bm25 = BM25Okapi(corpus)

# At query time
scores = bm25.get_scores(list(query_tokens))
```

**Integration:** Add BM25 as a scoring component alongside existing weights:

```python
# Current weights (lexical mode)
score = (0.40 * lexical) + (0.20 * tag) + (0.15 * scope) + (0.15 * recency) + (0.10 * confidence)

# Proposed weights (with BM25)
score = (0.30 * bm25_norm) + (0.15 * lexical) + (0.15 * tag) + (0.15 * scope) + (0.15 * recency) + (0.10 * confidence)
```

**Validation:** Run A/B eval to compare before/after on existing eval cases.

**Estimated effort:** 1 session
**Risk:** Low (additive change, A/B eval catches regressions)
**Dependency:** Phase 1.1 (retrieval must be in library to add scoring components cleanly)

---

### 3.2 Automatic Alias Discovery

**Problem:** The alias system (`aliases.json`) is manually maintained. If no
alias maps "commute" → "calendar constraints travel", the query misses relevant
notes.

**Solution:** Auto-generate alias candidates from note content:

1. During `sheep index`, scan all note titles and tags
2. Build co-occurrence pairs (tags that appear together frequently)
3. Extract noun phrases from titles/statements
4. Write suggested aliases to `aliases_suggested.json`
5. Human reviews and promotes to `aliases.json`

**Estimated effort:** 1–2 sessions
**Risk:** Low (suggestions only, no automatic activation)

---

## Phase 4: Model Unification

### 4.1 Unify TUI and Library Note Models

**Problem:** Two parallel type hierarchies exist:

```
ledger/notes/:         BaseNote → LoopNote, GenericNote  (326 lines)
tui/models/:           Note, Frontmatter, NoteType, ...  (150 lines)
tui/services/parser:   NoteParser (wraps ledger.parsing)  (129 lines)
```

The TUI's `Note` dataclass has fields like `sections`, `outgoing_links`,
`incoming_links`, `lint_warnings` that the library models lack. The library's
`LoopNote` has `question`, `why`, `next_action` that the TUI doesn't use
directly (it reads from `sections`).

**Approach:** Extend the library models to be the single source:

```python
# ledger/notes/__init__.py
@dataclass
class BaseNote:
    path: Path
    frontmatter: ParsedFrontmatter  # typed, not raw dict
    body: str
    title: str
    note_type: str
    sections: dict[str, str]         # add: parsed sections
    outgoing_links: list[NoteLink]   # add: from ledger.parsing.links
    tags: list[str]                  # promote from frontmatter

# TUI wraps rather than redefines:
# tui/models/note.py
@dataclass
class TUINote:
    base: BaseNote                    # composition over inheritance
    incoming_links: list[Path]        # TUI-specific (computed by NoteStore)
    lint_warnings: list[LintWarning]  # TUI-specific (computed by SheepRunner)
```

**Estimated effort:** 2 sessions
**Risk:** Medium (TUI widget code depends on current model shape)

---

## Phase 5: Timeline & Observability

### 5.1 Structured Timeline (JSONL)

**Problem:** `timeline.md` (198 entries) is a pipe-delimited text file:

```
2026-02-15T10:30:00Z | created | notes/02_facts/fact__example.md | New fact
```

This is human-readable but machine-hostile:
- No efficient date-range queries
- Git merge conflicts on concurrent appends
- No structured filtering by action/path/note-type

**Design:**

```
notes/08_indices/timeline.jsonl     ← machine source of truth
notes/08_indices/timeline.md        ← human view (generated by sheep index)
```

Each JSONL line:

```json
{"ts":"2026-02-15T10:30:00Z","action":"created","path":"notes/02_facts/fact__example.md","desc":"New fact","type":"fact"}
```

**Migration:**

1. Parse existing `timeline.md` into JSONL (one-time script)
2. Update `append_timeline_entry()` to write JSONL
3. Add `sheep index` step to regenerate `timeline.md` from JSONL
4. Add query functions: `timeline_since(date)`, `timeline_for_note(path)`

**Estimated effort:** 1 session
**Risk:** Low (additive — keep `.md` as generated view)

---

### 5.2 Query Telemetry Log

**Problem:** No visibility into how retrieval performs in real usage. The A/B
eval uses synthetic cases, but real queries may hit different edge cases.

**Design:** Optional telemetry file (`08_indices/query_log.jsonl`):

```json
{"ts":"...","query":"calendar constraints","scope":"work","mode":"legacy","top_3":["fact__calendar_constraints.md","pref__scheduling.md","loop__meeting_setup.md"],"latency_ms":142}
```

Disabled by default. Enabled via `LEDGER_QUERY_LOG=1`.

Uses: identify slow queries, discover missing aliases, find retrieval blind spots.

**Estimated effort:** 0.5 sessions
**Risk:** None (opt-in, local-only)

---

## Phase 6: Developer Experience

### 6.1 `ledger` as a Proper CLI (click or argparse refactor)

**Problem:** The current argparse setup in `scripts/ledger` (~200 lines of
subparser configuration) is verbose and hard to extend.

**Approach:** Either:

- **(a)** Refactor argparse into a clean dispatch pattern (no new deps)
- **(b)** Adopt `click` (well-known, decorator-based, auto-help)

Option (a) is preferred to keep deps minimal:

```python
# scripts/ledger
COMMANDS = {
    "query": cmd_query,
    "list": cmd_list,
    "loops": cmd_loops,
    "eval": cmd_eval,
    "embed": cmd_embed,
    "bundle": cmd_bundle,
}
```

**Estimated effort:** 1 session
**Dependency:** Phase 1.1 (commands should call library, not inline logic)

---

### 6.2 Watch Mode for TUI

**Problem:** The TUI shows a static snapshot. If notes change on disk (via CLI,
another agent, or manual edit), the TUI doesn't reflect changes until restart.

**Solution:** Use `watchdog` (already in many environments) or poll-based
refresh:

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class NoteChangeHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith(".md"):
            self.app.post_message(NoteChanged(path=event.src_path))
```

**Estimated effort:** 1 session
**Risk:** Low (Textual has good async message support)

---

## Phase 7: Future Considerations (Lower Priority)

### 7.1 Confidence Evolution

Add a `references` counter or `corroborated_by` field that tracks when a note
is cited in retrieval results or linked from other notes. Suggest confidence
promotions when a hypothesis note has been referenced N times without
contradiction.

### 7.2 Graph Export

Export the link graph as DOT or GraphML for visualization in Obsidian, Gephi,
or web-based tools. The TUI's graph panel data could feed this directly.

### 7.3 REST/WebSocket API

Wrap the library in a lightweight API (FastAPI or similar) for integration with
non-CLI tools, browser extensions, or remote agents.

### 7.4 Multi-User / Team Support

Add an `author` frontmatter field, per-user scopes, and a merge strategy for
concurrent edits. This is a significant architectural change and should only be
considered if there's a concrete multi-user use case.

---

## Implementation Sequence

```
Phase 1.1  Extract retrieval ──────┐
Phase 1.2  Rewrite sheep ──────────┤
                                    ├──→ Phase 2.1  Persistent index
                                    │         │
                                    │         ├──→ Phase 2.2  Inverted index
                                    │         │
                                    ├──→ Phase 3.1  BM25 scoring
                                    │
                                    ├──→ Phase 4.1  Model unification
                                    │
                                    └──→ Phase 5.1  JSONL timeline

Phase 3.2  Alias discovery ────────── (independent, any time after 1.1)
Phase 5.2  Query telemetry ────────── (independent, any time after 1.1)
Phase 6.1  CLI refactor ──────────── (after 1.1)
Phase 6.2  TUI watch mode ────────── (independent)
Phase 7.*  Future ─────────────────── (as needed)
```

**Critical path:** 1.1 → 2.1 → 3.1 (retrieval extraction → index → BM25)

---

## Estimated Total Effort

| Phase | Sessions | Cumulative |
|-------|----------|------------|
| 1.1 Extract retrieval | 2–3 | 2–3 |
| 1.2 Rewrite sheep | 2 | 4–5 |
| 2.1 Persistent index | 2–3 | 6–8 |
| 2.2 Inverted index | 1 | 7–9 |
| 3.1 BM25 scoring | 1 | 8–10 |
| 3.2 Alias discovery | 1–2 | 9–12 |
| 4.1 Model unification | 2 | 11–14 |
| 5.1 JSONL timeline | 1 | 12–15 |
| 5.2 Query telemetry | 0.5 | 12–15 |
| 6.1 CLI refactor | 1 | 13–16 |
| 6.2 TUI watch mode | 1 | 14–17 |

**Phases 1–3 (highest impact): ~8–10 sessions**

---

## Success Metrics

| Metric | Current | Target (after Phase 3) |
|--------|---------|----------------------|
| Cold query latency (100 notes) | ~200ms | < 50ms |
| Cold query latency (1,000 notes) | ~2–3s (projected) | < 200ms |
| Eval hit@3 | baseline | ≥ +5% improvement |
| Retrieval testable in isolation | No | Yes |
| Lint rule parity (Python vs bash) | Approximate | Exact |
| `scripts/ledger` size | 2,127 lines | < 500 lines |

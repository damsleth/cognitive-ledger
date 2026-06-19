# Plan 40 - YAAMS Tier 2 integration, Phase E: conflict detection

Part of the YAAMS Tier 2 roadmap (`35-yaams-tier2-integration-roadmap.md`).
Builds on Phase C's semantic dedup (`38-yaams-tier2-c-semantic-dedup.md`).

**STATUS as of 2026-06-10: PARTIAL — scaffolding shipped, core feature not started.**

| Piece | Status |
| --- | --- |
| cogled⇄YAAMS contract v1 (`docs/yaams-cogled-interface.md`) | ✅ shipped in `f176825` |
| cogled: `00_inbox/_conflicts/` loaded by `load_candidates_for_triage` (`ledger/inbox.py:357,455-458`) + test `test_conflicts_subfolder_loaded_and_sorted` | ✅ shipped in `96e1e07` |
| cogled: interactive triage UI, `m` merge command, `merge_into()` (Phase B soft dep) | ✅ shipped in `96e1e07` |
| YAAMS: contract-v1 provenance frontmatter in `format_note` (`yaams/promote/review.py`) | ✅ shipped in yaams `70849a2` |
| YAAMS: `yaams/promote/conflict.py` (classify_pair, prompt, JSON parsing) | ❌ not started |
| YAAMS: conflict columns in `promotion_candidates` table + wiring into generate/review | ❌ not started |
| YAAMS: `_conflicts/` routing in `write_to_inbox` | ❌ not started |
| YAAMS: `promote.conflict_detection` config | ❌ not started |
| cogled: `conflict_*` frontmatter parsing into `InboxCandidate` | ❌ not started |
| cogled: conflict column + side-by-side inspect in triage UI | ❌ not started |
| cogled: contradict rows excluded from range-accept | ❌ not started |
| cogled: `ledger inbox conflicts` subcommand | ❌ not started |

**Not this feature**: the NLI contradiction sleep scan
(`ledger/contradiction.py`, `ledger/nli.py`, shipped in `a6642bd`/`eefb7a9`)
retroactively scans *existing tier-2 notes* against each other during
`ledger sleep` and writes `conflict__*.md` notes into `00_inbox/` (root, not
`_conflicts/`). It is config-gated off (`contradiction_enabled: false`) until
the NLI model is validated on real Norwegian pairs — a rollout gate, not
missing code. Phase E is a different seam: classifying an *incoming YAAMS
candidate* against the single tier-2 note Phase C flagged as a near-match, at
promotion time, with a generative LLM (not NLI). Do not merge the two; do
reuse nothing from `contradiction.py` except awareness that both can produce
inbox items.

## Hard dependency: Phase C (plan 38) — NOT YET BUILT

Phase E only classifies candidates that Phase C put in the merge band
(`merge_with` set, similarity 0.80–0.92). Phase C is unimplemented in both
repos (no `yaams/promote/dedup.py`, no `ledger embed search` subcommand —
`ledger/cli.py` only has `embed build|status|clean` at lines 1508–1556).

**Build order: plan 38 first.** Phase E code can still be written and tested
against the pinned Phase C interface below (stub it in tests), but it cannot
be enabled end-to-end until plan 38 ships:

```python
# pinned from plan 38 — yaams/promote/dedup.py
@dataclass
class DedupVerdict:
    decision: Literal["new", "merge", "duplicate"]
    target_path: str | None      # logical path, e.g. "notes/02_facts/fact__x.md"
    similarity: float
    reason: str

def check_candidate(candidate_statement: str, config: DedupConfig,
                    ledger_cli: str = "ledger") -> DedupVerdict: ...
```

## Goal

When a YAAMS-drafted candidate near-matches an existing tier-2 note (Phase C
merge band), classify the relationship as
`duplicate | supplement | contradict | unrelated | unclassified` via one LLM
call. Embed the verdict in the inbox file's frontmatter, route contradictions
to `00_inbox/_conflicts/`, and surface everything in cogled's triage UI as a
column plus a side-by-side inspect view.

Classification semantics:

- **duplicate** — same claim; skip the candidate entirely (don't write it).
- **supplement** — same topic, adds detail; recommended action is merge.
- **contradict** — same topic, incompatible claims; route to `_conflicts/`,
  require an explicit per-row triage decision.
- **unrelated** — similar embedding, different claim; clear `merge_with`,
  treat as a Phase C false positive.
- **unclassified** — classifier confidence below threshold, or LLM failure.
  Keep `merge_with`, flow through as a normal merge-band candidate. Never
  conflate uncertainty with the semantic verdict "unrelated".

## YAAMS changes (repo: `~/code/yaams`)

### E1. New module `yaams/promote/conflict.py`

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from yaams.synthesize.llm import LLMAdapter

CONFLICT_PROMPT_VERSION = 1

Classification = Literal[
    "duplicate", "supplement", "contradict", "unrelated", "unclassified"
]

@dataclass
class ConflictVerdict:
    classification: Classification
    confidence: float
    reason: str
    target_path: str
    model: str | None
    prompt_version: int = CONFLICT_PROMPT_VERSION

@dataclass
class ConflictConfig:
    enabled: bool = False
    only_for_merge_band: bool = True
    confidence_threshold: float = 0.7
```

Functions:

- `strip_private_fences(text: str) -> str` — remove `<private>...</private>`
  spans (case-insensitive, multiline, balanced/nested — mirror the algorithm
  in cogled's `ledger/parsing/privacy.py:strip_private_tags`). Applied to both
  statements before prompt construction. YAAMS has no existing helper; write
  it here.
- `_build_prompt(existing_title, existing_statement, candidate_title,
  candidate_statement) -> str` — instructs the model to compare the two
  *statements* (not whole notes) and respond with bare JSON:
  `{"classification": "duplicate|supplement|contradict|unrelated",
  "confidence": 0.0-1.0, "reason": "<one sentence>"}`. Note: the prompt's
  enum has four values; `unclassified` is assigned by code, never by the model.
- `_parse_json_response(text: str) -> dict` — strip optional ``` fences,
  `json.loads`; on any parse failure, missing key, non-float confidence, or a
  classification outside the four-value enum, return
  `{"classification": "unclassified", "confidence": 0.0,
  "reason": "unparseable classifier output"}`.
- `classify_pair(existing_title: str, existing_statement: str,
  candidate_title: str, candidate_statement: str, target_path: str,
  adapter: LLMAdapter, config: ConflictConfig) -> ConflictVerdict`:
  - strips private fences from both statements;
  - calls `adapter.complete(prompt, max_tokens=200, temperature=0.1)`
    (`LLMAdapter` protocol in `yaams/synthesize/llm.py`: returns
    `LLMResponse(text, backend, model)`; adapter exposes `.model_name`);
  - any adapter exception → verdict `("unclassified", 0.0, "llm call failed:
    <exc>")`;
  - if parsed `confidence < config.confidence_threshold` → demote
    classification to `"unclassified"` (keep confidence and reason as
    returned);
  - returns `ConflictVerdict(..., target_path=target_path,
    model=adapter.model_name)`.

### E2. Wire into `generate_candidates` (`yaams/promote/candidates.py`)

Extend `PromotionCandidate` (additive, defaulted fields so existing call
sites/tests don't change):

```python
merge_with: str | None = None              # from Phase C
dedup_similarity: float | None = None      # from Phase C
conflict_classification: str | None = None
conflict_confidence: float | None = None
conflict_reason: str | None = None
conflict_model: str | None = None
conflict_checked_at: str | None = None     # ISO-8601 Z
conflict_target_statement_hash: str | None = None  # "sha256:<hex>" of existing statement
conflict_prompt_version: int | None = None
```

In the per-entity loop, after Phase C's `check_candidate` sets
`merge_with`/`dedup_similarity` (plan 38), and only when
`conflict_cfg.enabled` and (`not only_for_merge_band` or
`verdict.decision == "merge"`):

1. Resolve the existing note's title + statement from `note_index.json`
   (`config.note_index_path`): the index shape (built by cogled
   `ledger/retrieval.py:rebuild_note_index`) is
   `{"entries": {"<logical rel path>": {"candidate": {"title": ..., "statement": ...}}}}`,
   keyed by the same logical path Phase C returns as `target_path`. If the
   entry or statement is missing, skip classification (leave conflict fields
   None) and `on_progress("  conflict check skipped (no statement for <path>)")`.
2. `cv = classify_pair(existing_title, existing_statement,
   candidate.draft_title, candidate.draft_statement, target_path, adapter,
   conflict_cfg)`.
3. Apply the verdict:
   - `duplicate` → drop the candidate (`on_progress("  skipped (LLM-confirmed
     duplicate of <path>)")`, `continue`);
   - `unrelated` → clear `merge_with` and `dedup_similarity`, store the
     conflict fields anyway (auditable);
   - `supplement` / `contradict` / `unclassified` → keep `merge_with`, store
     all `conflict_*` fields (`conflict_checked_at` = now UTC,
     `conflict_target_statement_hash` = `"sha256:" +
     sha256(existing_statement.encode()).hexdigest()`,
     `conflict_prompt_version` = `CONFLICT_PROMPT_VERSION`).

`generate_candidates` gains a keyword arg
`conflict_cfg: ConflictConfig | None = None` (None → disabled, zero behavior
change).

### E3. Schema migration (`yaams/yaams/schema.py`)

In `_migrate_promotion_candidates`, add additive columns using the existing
`PRAGMA table_info` + `ALTER TABLE ADD COLUMN` pattern (see
`_migrate_query_structured_fields`, line ~255): `merge_with TEXT`,
`dedup_similarity REAL`, `conflict_classification TEXT`,
`conflict_confidence REAL`, `conflict_reason TEXT`, `conflict_model TEXT`,
`conflict_checked_at TEXT`, `conflict_target_statement_hash TEXT`,
`conflict_prompt_version INTEGER`. Do not bump `SCHEMA_VERSION` semantics
beyond what additive migration requires (follow repo convention). Update
`store_candidates` INSERT to persist the new fields; `fetch_pending` uses
`SELECT *` so it needs no change.

### E4. Inbox writing (`yaams/promote/review.py`)

- `format_note(candidate)`: when `conflict_classification` is set, append the
  conflict block to the frontmatter (additive fields — contract stays
  `contract_version: 1` per `docs/yaams-cogled-interface.md`, which bumps
  only on rename/removal):

```yaml
merge_with: notes/02_facts/fact__crayon_softwareone_internal_domains.md
dedup_similarity: 0.84
conflict_classification: supplement
conflict_confidence: 0.91
conflict_reason: candidate adds a specific date to the existing acquisition fact
conflict_model: ollama/llama3.1
conflict_checked_at: 2026-06-10T12:00:00Z
conflict_target_statement_hash: sha256:...
conflict_prompt_version: 1
```

  Emit `merge_with`/`dedup_similarity` whenever set, even without a
  classification (Phase C alone). `conflict_reason` must be private-stripped
  already (it comes from the model over stripped statements; assert no
  `<private>` substring in tests).
- `write_to_inbox(candidate, inbox_path, content=None)`: if
  `candidate.get("conflict_classification") == "contradict"`, write into
  `inbox_path / "_conflicts"` instead (keep the existing mkdir +
  anti-clobber logic).

### E5. Config + CLI wiring (`yaams/yaams/_default_config.yaml`, `config.yaml.example`, `yaams/cli/promote.py`)

```yaml
promote:
  conflict_detection:
    enabled: false            # default off until plan 38 ships + prompt tuned
    only_for_merge_band: true
    confidence_threshold: 0.7
```

In `promote_generate` (`yaams/cli/promote.py`), build a `ConflictConfig` from
`promote_cfg_raw.get("conflict_detection", {})` and pass it to
`generate_candidates`. Add to the `--json` envelope stats:
`conflicts_classified`, `duplicates_skipped_llm`, `merges_cleared_unrelated`.
Non-JSON progress lines should read like
`  classified as supplement (vs notes/02_facts/fact__x.md, 0.91)`.

### E6. YAAMS tests (`tests/test_promote_conflict.py`, new)

Use `DummyAdapter` from `yaams/synthesize/llm.py` (or a local stub returning
canned `LLMResponse` text); never call a real model.

- `test_classify_pair_returns_duplicate_for_identical_claims`
- `test_classify_pair_returns_supplement_for_added_detail`
- `test_classify_pair_returns_contradict_for_opposing_claims`
- `test_classify_pair_low_confidence_becomes_unclassified` (canned
  confidence 0.4 < threshold 0.7)
- `test_classify_pair_adapter_exception_returns_unclassified_zero`
- `test_classify_pair_strips_private_fences_from_prompt` (canary string
  inside `<private>` must not reach `adapter.complete`'s prompt arg)
- `test_parse_json_response_handles_fenced_output`
- `test_parse_json_response_defaults_on_garbage`
- `test_parse_json_response_rejects_unknown_classification`
- `test_generate_candidates_classifies_merge_band_only` (only_for_merge_band)
- `test_generate_candidates_skips_llm_duplicate`
- `test_generate_candidates_clears_merge_on_unrelated`
- `test_generate_candidates_disabled_is_noop` (conflict_cfg None / disabled)
- `test_store_candidates_persists_conflict_columns`
- `test_write_to_inbox_routes_contradicts_to_subfolder`
- `test_format_note_embeds_conflict_metadata`

## Cogled changes (this repo)

### E7. Parse conflict metadata (`ledger/inbox.py`)

Extend `InboxCandidate` (line ~360) with defaulted fields:
`dedup_similarity: float | None = None`,
`conflict_classification: str | None = None`,
`conflict_confidence: float | None = None`,
`conflict_reason: str | None = None`. Populate them in
`_load_one_candidate` from frontmatter (coerce floats defensively like the
existing `confidence` handling; absent → None). `_conflicts/` loading is
already done (✅ `96e1e07`).

Range-accept guard: in `apply_actions` nothing changes, but in
`ledger/inbox_triage.py:_parse_command`, when the `a` command's parsed range
covers **more than one row**, drop rows whose
`conflict_classification == "contradict"` and print
`note: #<n> is a contradiction — accept it explicitly with 'a <n>'`. A
single-index `a <n>` still accepts a contradiction. (Satisfies acceptance
criterion 7 without new action types.)

### E8. Triage UI (`ledger/inbox_triage.py`)

- `_render_table`: add a `conflict?` column (width 12) after `conf`,
  showing `conflict_classification.upper()` or `-`. Adjust the `fixed`
  width calculation accordingly. Keep the existing `[merge?]` title suffix
  for merge-band candidates without a classification.
- `_handle_inspect`: when the row has `merge_with` **and** conflict metadata,
  render side-by-side instead of just the body. New helper:

```python
def _render_side_by_side(candidate: InboxCandidate) -> None:
    """Print existing note vs candidate: title, statement, then the
    classification line 'CONTRADICT (0.88): <reason>'."""
```

  Resolve `candidate.merge_with` via the existing `_merge_target_for`,
  read the target file, extract its `## Statement` section (fallback: first
  body paragraph), and print both sides under `--- existing ---` /
  `--- candidate ---` headers, followed by the verdict line and a hint that
  `m <idx>` merges. Unreadable target → fall back to plain body print with a
  warning (never crash the loop).
- `_HELP_TEXT`: document that `i` shows a side-by-side for conflict rows and
  that range-accept skips contradiction rows.

### E9. `ledger inbox conflicts` subcommand (`ledger/cli.py`)

Parser (inbox block, line ~1664):

```python
conflicts_parser = inbox_subparsers.add_parser(
    "conflicts", help="List contradiction candidates with side-by-side statements"
)
conflicts_parser.add_argument("--json", action="store_true", dest="json")
```

Handler (in `handle_inbox_command`, new `elif sub == "conflicts":`): filter
`load_candidates_for_triage()` to
`c.conflict_classification == "contradict"`. Plain output: per candidate,
filename, both statements (reuse `_render_side_by_side` logic from
inbox_triage), confidence, reason. `--json`: envelope
`{"tool": "ledger", "command": "inbox conflicts", "ok": true, "conflicts":
[{"filename", "merge_with", "conflict_classification",
"conflict_confidence", "conflict_reason", "statement"}]}`. Empty →
`"No conflict candidates."` / `"conflicts": []`. Update the usage string at
line ~1158 to `{list|triage|cleanup|reject|conflicts}`.

### E10. Cogled tests (`tests/test_inbox_triage.py` + `tests/test_cli_inbox_conflicts.py`)

In `tests/test_inbox_triage.py` (reuse `TriageTestBase`, which already
creates `self.conflicts = self.inbox / "_conflicts"`):

- `test_load_candidates_reads_conflict_metadata` (write a fixture file with
  the full `conflict_*` block; assert fields on `InboxCandidate`)
- `test_render_table_shows_conflict_column`
- `test_inspect_renders_side_by_side_for_conflict` (create the merge target
  note with a `## Statement` section; capture stdout)
- `test_range_accept_skips_contradict_rows`
- `test_single_accept_allows_contradict_row`

New `tests/test_cli_inbox_conflicts.py` (follow the pattern of existing CLI
tests):

- `test_inbox_conflicts_filters_to_contradictions`
- `test_inbox_conflicts_json_envelope_shape`
- `test_inbox_conflicts_empty_inbox`

## Acceptance criteria

1. (YAAMS, after plan 38) `yaams promote generate` with conflict detection
   enabled prints `classified as supplement (vs notes/02_facts/fact__X.md,
   0.91)`-style lines for merge-band candidates; `--json` stats include
   `conflicts_classified`.
2. (YAAMS) A candidate classified `contradict` lands in
   `00_inbox/_conflicts/`, not the inbox root; its frontmatter carries the
   full `conflict_*` block. Verify: `yaams promote review` → accept → check
   file location and `head -20` of the file.
3. (cogled) `ledger inbox triage --interactive` shows the `conflict?` column;
   `i <idx>` on a conflict row prints the side-by-side view.
   `python -m pytest tests/test_inbox_triage.py -q` → all pass.
4. (cogled) `ledger inbox conflicts` lists only `contradict` rows;
   `ledger inbox conflicts --json | jq .ok` → `true`.
   `python -m pytest tests/test_cli_inbox_conflicts.py -q` → all pass.
5. With `promote.conflict_detection.enabled: false` (the default), YAAMS
   behavior is byte-identical to Phase C alone (`merge_with` hints, no
   `conflict_*` fields); cogled renders `-` in the conflict column.
6. LLM failures (timeout, garbage, unknown enum) and low confidence yield
   `conflict_classification: unclassified` with the candidate flowing through
   as a normal merge-band row — never silently dropped, never marked
   `unrelated`.
7. Range-accept (`a 1-`) skips contradiction rows with a printed note;
   `a <idx>` on that row alone accepts it.
8. `<private>`-fenced canary text never appears in classifier prompts
   (asserted via stub adapter), inbox frontmatter, or `ledger inbox
   conflicts` output.
9. `python -m pytest tests/test_promote_conflict.py -q` (yaams repo) → all
   pass with no network/model access.

## Risk and rollback

- **LLM cost**: ~200-token prompt per merge-band candidate, ~10/run →
  negligible locally, bounded hosted. `only_for_merge_band: true` caps it.
- **Hallucinated contradictions**: classification is a hint; the user always
  sees side-by-side + reason. Worst case is one extra triage decision.
- **Malformed JSON from local models**: `_parse_json_response` →
  `unclassified`, candidate flows through.
- **Rollback**: `promote.conflict_detection.enabled: false` (the default).
  Cogled-side code is purely additive and inert without the frontmatter
  fields.

## Build order / effort

1. Plan 38 (Phase C) — prerequisite, separate plan.
2. E1 + E6 classify/parse tests (1 day, stub adapter; no Phase C needed).
3. E3 schema + E2 generate wiring + E5 config (0.75 day; Phase C stub in
   tests).
4. E4 inbox writing/routing (0.25 day).
5. E7–E10 cogled side (1.5 days; independent of YAAMS, drivable entirely
   from fixture files — start here if plan 38 is blocked).
6. Prompt iteration on real Norwegian/English pairs + end-to-end run
   (0.5–1 day; gate flipping `enabled: true` on this, same discipline as the
   NLI `contradiction_enabled` gate).

---

## Workflow decomposition (Sonnet subagents)

**Two repos.** yaams = `/Users/damsleth/code/yaams` (`.venv/bin/python -m pytest`).
cogled = `/Users/damsleth/code/cognitive-ledger` (`.venv/bin/pytest`).
Every subagent: read this plan + the repo's `AGENTS.md` first; edit only its
listed files; never share a file with a parallel sibling.

**Prereq gate:** Phase C (plan 38) ships `yaams/promote/dedup.py` + `merge_with`.
Phase E code is authored against the pinned `DedupVerdict` interface (§"Hard
dependency"); E-tasks stub Phase C in tests, so they need plan 38 *merged* only
for the end-to-end gate (Y-G), not for unit work. The **cogled side (E7–E10) is
fully fixture-driven and independent of both plan 38 and the yaams side** — it
is the safe place to start if anything upstream is blocked.

### Task graph

| ID | Repo | Steps | Files (exclusive) | Depends on | Verify |
|----|------|-------|-------------------|-----------|--------|
| Y1 | yaams | E1 (`conflict.py`: strip/prompt/parse/classify) | `yaams/promote/conflict.py` | — (stub adapter) | `.venv/bin/python -m pytest tests/test_promote_conflict.py -q -k "classify or parse"` |
| Y2 | yaams | E3 (schema migration + store_candidates INSERT) | `yaams/yaams/schema.py` | — | migration unit test |
| Y3 | yaams | E2 (PromotionCandidate fields + generate wiring) | `yaams/promote/candidates.py` | Y1, Y2 | `tests/test_promote_conflict.py -q -k generate` |
| Y4 | yaams | E4 (format_note conflict block + _conflicts routing) | `yaams/promote/review.py` | Y1 | `-k "write_to_inbox or format_note"` |
| Y5 | yaams | E5 (config + CLI + --json stats) | `yaams/cli/promote.py`, `yaams/yaams/_default_config.yaml`, `config.yaml.example` | Y3 | config loads; `--json` envelope has new stats |
| Y6 | yaams | E6 (full test file) | `tests/test_promote_conflict.py` | Y1–Y5 | `.venv/bin/python -m pytest tests/test_promote_conflict.py -q` (no network) |
| C1 | cogled | E7 (InboxCandidate fields + parse + range-accept guard) | `ledger/inbox.py`, `ledger/inbox_triage.py` (parse only) | — | `.venv/bin/pytest tests/test_inbox_triage.py -q -k "metadata or range_accept"` |
| C2 | cogled | E8 (triage UI: column + side-by-side) | `ledger/inbox_triage.py` (render) | C1 | `tests/test_inbox_triage.py -q` |
| C3 | cogled | E9 (`ledger inbox conflicts` subcommand) | `ledger/cli.py` | C1 | `tests/test_cli_inbox_conflicts.py -q` |
| C4 | cogled | E10 (test files) | `tests/test_inbox_triage.py`, `tests/test_cli_inbox_conflicts.py` | C1–C3 | both test files green |
| Y-G | both | acceptance 1–9 | — | Y*, C*, plan 38 merged | full §Acceptance criteria |

> Note: C1 and C2 both touch `ledger/inbox_triage.py` — they are **sequential,
> not parallel** (C1 = parse + range-accept guard, C2 = render table +
> side-by-side). Do not assign them to concurrent agents.

### Parallelism

- **Round 1 (parallel, disjoint files/repos):** Y1 ∥ Y2 ∥ C1.
- **Round 2:** Y3 (after Y1+Y2) ∥ Y4 (after Y1) ∥ C2→C3 chain (after C1; C2 then
  C3, since C3 reuses C2's `_render_side_by_side`).
- **Round 3:** Y5 (after Y3) ∥ continue cogled.
- **Round 4:** Y6 ∥ C4 (test tasks).
- **Round 5 (serial):** Y-G end-to-end after plan 38 + both sides merged.

Parallel agents inside one repo (e.g. Y3 ∥ Y4) must use `isolation: worktree`.
The cogled chain and yaams chain are separate repos → no isolation needed
between them.

### Gate discipline

Default config keeps the feature inert (`conflict_detection.enabled: false`),
so any task can land on `main` without behaviour change. Flipping `enabled:
true` is **out of scope for the workflow** — it's the manual prompt-tuning gate
in build-order step 6, same discipline as the NLI `contradiction_enabled` gate.

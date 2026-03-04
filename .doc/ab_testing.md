# Cognitive Ledger A/B Testing Guide

## Purpose
Use `scripts/ledger_ab` to compare retrieval quality and latency between two git refs in a reproducible way.

## What It Measures
- Quality: `hit1`, `hitk`, `mrr` from `scripts/ledger eval`
- Latency:
  - `eval` probe p95 (repeated full-eval timing)
  - `query` probe p95 (repeated per-case query timing)
  - candidate cache is reset before each probe run to avoid warm-cache carryover across runs
  - optional `--cold-query` resets candidate cache before every individual query case

Decision policy:
- `regression` (exit `2`) if any quality metric drops.
- `beneficial` (exit `0`) if any quality metric improves.
- If quality ties, latency thresholds decide:
  - pass -> `beneficial`
  - fail -> `neutral` (exit `3`)
- `invalid_setup` is exit `4`.

## Prerequisites
- Retrieval cases should be valid and portable:
  - `notes/08_indices/retrieval_eval_cases.yaml`
  - use `id`, `query`, `scope`, and repo-relative `expected_any` (`notes/...`)
- Validate cases first:

```bash
./scripts/ledger eval --cases notes/08_indices/retrieval_eval_cases.yaml --k 3 --strict-cases
```

## Standard Branch-vs-Branch Run

```bash
./scripts/ledger_ab \
  --baseline-ref main \
  --candidate-ref HEAD \
  --runs 5 \
  --out-dir /tmp/ledger-ab
```

Equivalent explicit form:

```bash
./scripts/ledger_ab \
  --baseline-ref main \
  --candidate-ref HEAD \
  --eval-runs 5 \
  --query-runs 5 \
  --out-dir /tmp/ledger-ab
```

Cold-query variant (stricter query latency probe):

```bash
./scripts/ledger_ab \
  --baseline-ref main \
  --candidate-ref HEAD \
  --query-runs 5 \
  --cold-query \
  --out-dir /tmp/ledger-ab-cold-query
```

Outputs:
- `/tmp/ledger-ab/ab_eval.json`
- `/tmp/ledger-ab/ab_eval.md`

Retrieval-mode defaults:
- `legacy` is default.
- As of 2026-02-12, `legacy` includes the stacked winning retrieval behaviors (two-stage shortlist, compressed-attention shortlist path, scope/type prefilter, precomputed index lookup, progressive disclosure).
- Compare experimental retrieval without changing refs by setting modes explicitly:

```bash
./scripts/ledger_ab \
  --baseline-ref HEAD \
  --candidate-ref HEAD \
  --baseline-mode legacy \
  --candidate-mode two_stage \
  --out-dir /tmp/ledger-ab-modes
```

Compressed-attention mode comparison:

```bash
./scripts/ledger_ab \
  --baseline-ref HEAD \
  --candidate-ref HEAD \
  --candidate-mode compressed_attention \
  --cold-query \
  --out-dir /tmp/ledger-ab-compressed-attention
```

Scope/type-prefilter mode comparison:

```bash
./scripts/ledger_ab \
  --baseline-ref HEAD \
  --candidate-ref HEAD \
  --candidate-mode scope_type_prefilter \
  --runs 5 \
  --out-dir /tmp/ledger-ab-scope-prefilter
```

Precomputed-index mode comparison:

```bash
./scripts/ledger_ab \
  --baseline-ref HEAD \
  --candidate-ref HEAD \
  --candidate-mode precomputed_index \
  --runs 5 \
  --out-dir /tmp/ledger-ab-precomputed-index
```

Progressive-disclosure mode comparison:

```bash
./scripts/ledger_ab \
  --baseline-ref HEAD \
  --candidate-ref HEAD \
  --candidate-mode progressive_disclosure \
  --runs 5 \
  --out-dir /tmp/ledger-ab-progressive-disclosure
```

Semantic-hybrid mode comparison (semantic index auto-build is handled by `ledger_ab` in each worktree):

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

Note: `ledger_ab` evaluates git refs/worktrees, so semantic tooling files must exist in the tested refs (commit or stash ref), not only as uncommitted local files.

Optional backend/model controls for semantic runs:

```bash
./scripts/ledger_ab \
  --baseline-ref HEAD \
  --candidate-ref HEAD \
  --baseline-mode semantic_hybrid \
  --candidate-mode semantic_hybrid \
  --baseline-embed-backend local \
  --candidate-embed-backend local \
  --baseline-embed-model TaylorAI/bge-micro-v2 \
  --candidate-embed-model TaylorAI/bge-micro-v2 \
  --runs 3 \
  --out-dir /tmp/ledger-ab-semantic-configured
```

## 2026-02-12 Stacked Default Rollout

Applied process:
1. Start from cleanup baseline commit `4d4e7da` (regressing ideas removed).
2. Integrate one winning idea into `legacy`.
3. Run gate:

```bash
python ./scripts/ledger_ab \
  --baseline-ref <previous_commit> \
  --candidate-ref HEAD \
  --baseline-mode legacy \
  --candidate-mode legacy \
  --runs 5 \
  --query-runs 5 \
  --cold-query \
  --out-dir ./tmp/ledger-ab/<stack-step>
```

Rollout commits:
- `d5ac45f` — two-stage in `legacy` (passed)
- `c2abf26` — compressed-attention in `legacy` (passed)
- `f69afe0` — scope/type prefilter in `legacy` (passed)
- `e536de8` — precomputed index in `legacy` (passed)
- `8b7a2e2` — progressive disclosure in `legacy` (passed)

Abort rule:
- Stop rollout if any step returns `regression` or `neutral`.

## When to Use `--allow-corpus-diff`
Default behavior requires baseline/candidate corpus fingerprint equality.
Use `--allow-corpus-diff` only when the benchmark corpus intentionally changed (for example edits to `retrieval_eval_cases.yaml` or note corpus updates).

```bash
./scripts/ledger_ab \
  --baseline-ref main \
  --candidate-ref HEAD \
  --allow-corpus-diff \
  --out-dir /tmp/ledger-ab-corpus-change
```

## Working Tree Caveat
`ledger_ab` compares git refs/worktrees, not uncommitted local edits.
If you need to evaluate uncommitted work, either commit first (recommended) or generate a temporary ref:

```bash
CANDIDATE_REF=$(git stash create "ab-temp-candidate")
./scripts/ledger_ab --baseline-ref HEAD --candidate-ref "$CANDIDATE_REF" --allow-corpus-diff
```

## Interpreting Results
- Read `decision.reason` first.
- For quality changes, inspect `decision.quality_deltas`.
- For tie-break outcomes, inspect `decision.latency.eval/query` and thresholds.
- If `invalid_setup`, check:
  - missing refs
  - missing cases path
  - corpus mismatch without `--allow-corpus-diff`

## Suggested Team Workflow
1. Run strict eval locally.
2. Commit candidate changes.
3. Run `ledger_ab` against `main` vs candidate commit.
4. Attach `ab_eval.md` to PR/review notes.
5. Treat any quality regression as a hard stop.

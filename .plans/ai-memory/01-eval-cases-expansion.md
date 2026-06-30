# Plan 01 — Expand retrieval eval cases (multi-hop / temporal / negative)

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** Stage 0 + T1/T5 (multi-hop), T-temporal (A1), negatives (B1/D3). **Gate:** none — this *is* the gate substrate. **Blocks:** everything.

## Why first
`tests/fixtures/retrieval_eval_cases.yaml` is **comments-only today** (14 lines, verified). Every other plan's accept/reject runs `ledger ab run --cases tests/fixtures/retrieval_eval_cases.yaml`. With no cases, the gate is vacuous. This plan makes the gate real. **Nothing else in this roadmap may merge until this lands.**

## Schema (validated in `ledger/eval.py:207-286`)
```yaml
- query: "..."             # required, non-empty
  id: unique_id            # optional (auto case_N); must be unique under --strict-cases
  scope: all|work|personal # optional, default "all"
  expected_any: ["notes/02_facts/foo.md", ...]   # positive: ≥1 must rank ≤k
# OR a negative:
- query: "..."
  id: neg_1
  expected_none: true      # top score must be ≤ negative_eval_max_score (0.5)
```
`expected_any` and `expected_none` are mutually exclusive. Metrics: `hit@1`, `hit@k`, `mrr`, and for negatives `false_positive_rate`/`abstain_accuracy` (`ledger/eval.py:326-512`).

## Steps
1. **Use a fixture corpus, never Kim's live `~/brain/ledger`.** Check whether `tests/fixtures/` already ships a corpus the cases resolve against (search for a fixture notes tree under `tests/`). If none, author a small deterministic fixture corpus (~30–50 notes across `02_facts`, `03_preferences`, plus a couple in `09_archive` for temporal cases) under `tests/fixtures/corpus/` and point the cases at it. Document the corpus path in the YAML header comment.
2. **General cases (~25):** straightforward query→note, covering each taxonomy folder. These protect against regressions in the common path.
3. **Multi-hop cases (~8–10, tag `id: hop_*`):** answer requires linking 2–3 notes across *different* taxonomy folders. These are the T5 (associative) subset — Plan 13 measures hit@k on exactly these. Include 1–2 queries about a high-degree "hub" entity (you, a main project) to expose mega-hub dilution later.
4. **Temporal cases (~6, `id: temporal_*`):** facts that changed over time (old value in `09_archive` with `valid_to` set, new value live). Author both an as-of query ("where did I live in 2023") and a now query ("where do I live"). These gate Plan 06.
5. **Negative cases (~8, `id: neg_*`, `expected_none: true`):** queries that *should* match nothing in the corpus. These gate admission control + guard `false_positive_rate` for every later plan.
6. Split for `ab loop`: also create `retrieval_eval_screen.yaml` (fast ~10-case subset) and `retrieval_eval_holdout.yaml` (~10 disjoint cases) alongside, since `ab loop` needs `--screen-cases`/`--holdout-cases`/`--benchmark-cases`. The full file is the benchmark set.

## Acceptance gate
```bash
ledger eval --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 --strict-cases
echo "exit=$?"   # must be 0; every case must resolve (no missing expected_any paths)
```
Baseline `semantic_hybrid` should pass the general+temporal cases; multi-hop/negative are *allowed to fail now* — they exist to be improved by later plans. Record the baseline metric table in the PR; it becomes the reference every later plan diffs against.

## Done when
Cases file is non-trivial, `ledger eval` resolves all paths with exit 0, screen/holdout/benchmark splits exist, and the baseline metrics are recorded in the PR body.

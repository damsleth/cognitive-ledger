# Cognitive Ledger Retrieval A/B Improvement Ideas

Created: 2026-02-10

## Purpose
Capture high-level retrieval improvement concepts to evaluate via `scripts/ledger_ab`.

## Completed

1. Two-Stage Ranking
- status: completed (2026-02-12)
- A/B-gated retrieval mode implemented and benchmarked.
- status: integrated into `legacy` default via `d5ac45f`.

2. Compressed Attention Retrieval
- status: completed (2026-02-12)
- A/B-gated retrieval mode implemented and benchmarked.
- status: integrated into `legacy` default via `c2abf26`.

3. Scope/Type Prefiltering
- status: completed (2026-02-12)
- Retrieval mode adds intent-aware prefiltering by scope/type before shortlist/ranking.
- status: integrated into `legacy` default via `f69afe0`.

4. Precomputed Retrieval Index
- status: completed (2026-02-12)
- Retrieval mode adds cached token index lookup to reduce candidate scanning.
- status: integrated into `legacy` default via `e536de8`.

5. Progressive Disclosure Retrieval UX
- status: completed (2026-02-12)
- Retrieval mode stages result detail (top rationale-rich hits, remaining compact).
- status: integrated into `legacy` default via `8b7a2e2`.

## Dropped

1. Query Normalization Expansion
- status: dropped (2026-02-12)
- A/B run showed quality regression (`hitk`, `mrr`) and slower latency.

2. Corpus Hygiene as Retrieval Optimization
- status: dropped (2026-02-12)
- A/B run showed quality regression (`hitk`, `mrr`) despite latency gains.
- removed from active mode set via `4d4e7da`.

## Stacked Rollout (Legacy Default)

Stacking approach used:
- Start from post-cleanup baseline (`4d4e7da`).
- Integrate one winner into `legacy` at a time.
- Gate each step with `ledger_ab` (quality + latency tie-break).
- Commit only when gate passes.

Stack outcome:
1. `d5ac45f` — integrate Two-Stage into `legacy`: beneficial.
2. `c2abf26` — integrate Compressed Attention into `legacy`: beneficial.
3. `f69afe0` — integrate Scope/Type Prefilter into `legacy`: beneficial.
4. `e536de8` — integrate Precomputed Index into `legacy`: beneficial.
5. `8b7a2e2` — integrate Progressive Disclosure into `legacy`: beneficial.

Current default:
- `legacy` now reflects the stacked winner path from the above sequence.
- dropped ideas remain in git history but are removed from runtime mode choices.

## Idea Backlog

No pending retrieval ideas. Add new ideas here before next implementation wave.

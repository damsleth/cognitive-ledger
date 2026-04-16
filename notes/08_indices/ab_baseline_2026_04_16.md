# A/B Baseline Results - 2026-04-16

Baseline experiments run after Phase 1 commit (825dcce). All tests compare
retrieval modes on the same ref (HEAD) with 5 runs each.

## Summary

| ID | Experiment | Decision | hit@1 | hit@k | MRR (delta) | Latency |
|---|---|---|---|---|---|---|
| H1 | legacy vs progressive_disclosure | beneficial | 0.578 | 0.867 | +0.004 | slower p95 |
| H2 | legacy vs semantic_hybrid | blocked | - | - | - | numpy/torch version conflict |
| H3 | two_stage vs compressed_attention | regression | 0.578 | -0.022 | -0.006 | faster p95 |
| H4 | scope_type_prefilter vs precomputed_index | beneficial | 0.578 | tied | tied | 6x faster |
| H5 | warm vs cold query (legacy) | beneficial | 0.578 | tied | tied | ~same |

## Key Findings

**H1 - progressive_disclosure is beneficial.** MRR improves slightly (+0.004) with
no hit rate change. Latency is higher (7.7ms vs 5.5ms p95 query), which is acceptable
for a presentation-layer mode. This validates building the three-layer retrieval UX
on top of progressive_disclosure.

**H2 - semantic_hybrid blocked.** The local sentence-transformers dependency has a
numpy 2.x / torch 2.2 conflict. Needs `pip install --upgrade torch numpy` or pinning.
Not a retrieval quality issue.

**H3 - compressed_attention is a regression.** hit@k drops -0.022 and MRR drops -0.006.
The mode is faster (4ms vs 7.7ms p95) but quality loss is not worth it. Do not use
compressed_attention as the default mode.

**H4 - precomputed_index is a clear win.** Quality identical, latency 6x faster
(6.5ms vs 41ms p95 query). The precomputed index eliminates repeated candidate
building. Strong candidate for default mode.

**H5 - cache behavior is not masking real costs.** Warm and cold query latency are
within 1ms of each other, suggesting the candidate cache isn't a significant factor
in production queries.

## Recommendation for Three-Layer Retrieval UX

Use `progressive_disclosure` as the base retrieval mode for the three-layer UX.
Consider `precomputed_index` as the default scoring backend (6x latency win with
no quality loss). The three-layer UX is a presentation concern on top of whichever
scoring mode is active.

## Full Reports

Detailed per-query breakdowns available in `.claude-mem/ab-results/h1..h5/ab_eval.md`.

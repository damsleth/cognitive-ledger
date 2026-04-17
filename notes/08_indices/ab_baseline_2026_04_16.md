# A/B Baseline Results - 2026-04-16

Full matrix of retrieval mode comparisons on the current corpus (HEAD, 5 runs each).

## All Modes vs Legacy

| Candidate mode | Decision | MRR delta | hit@k delta | p95 query (ms) |
|---|---|---|---|---|
| two_stage | beneficial | +0.004 | 0 | 5.1 -> 7.7 |
| compressed_attention | **regression** | -0.002 | **-0.022** | 5.0 -> 4.8 |
| scope_type_prefilter | beneficial | +0.004 | 0 | 5.0 -> 40.5 |
| precomputed_index | beneficial | +0.004 | 0 | 4.6 -> 6.1 |
| progressive_disclosure | beneficial | +0.004 | 0 | 5.3 -> 7.3 |
| semantic_hybrid | **beneficial** | **+0.108** | **+0.067** | 5.0 -> 2.4 |

## Cross-Mode (Top Performers)

| Baseline | Candidate | Decision | MRR delta | p95 query (ms) |
|---|---|---|---|---|
| two_stage | precomputed_index | beneficial | +0.001 | 12.0 -> 7.5 |
| two_stage | progressive_disclosure | beneficial (latency) | 0 | 10.0 -> 7.7 |
| precomputed_index | progressive_disclosure | **regression** | -0.001 | 6.2 -> 7.6 |
| scope_type_prefilter | precomputed_index | beneficial (latency) | 0 | 39.9 -> 5.7 |
| scope_type_prefilter | progressive_disclosure | **regression** | -0.001 | 39.7 -> 7.6 |

## Other

| Test | Decision | Notes |
|---|---|---|
| warm vs cold query (legacy) | beneficial (latency) | <1ms difference, cache not a factor |

## Rankings

By MRR (highest first):
1. **semantic_hybrid** - 0.8296 (best quality AND fastest query, requires embeddings)
2. **precomputed_index** - 0.7259 (best lexical, fast)
3. **scope_type_prefilter** - 0.7259 (same quality, slow without precompute)
4. **two_stage** - 0.7254
5. **progressive_disclosure** - 0.7254
6. **legacy** - 0.7217
7. **compressed_attention** - 0.7198 (regression, removed)

By latency (fastest first):
1. **semantic_hybrid** - ~2.4ms (precomputed embeddings)
2. **legacy** - ~5.0ms
3. **precomputed_index** - ~6.1ms
4. **progressive_disclosure** - ~7.3ms
5. **two_stage** - ~7.7ms
6. **scope_type_prefilter** - ~40ms (rebuilds candidates every query)

## Recommendations

1. **Best overall: `semantic_hybrid`** - Dominates every metric. +10.8% MRR, +15.6% hit@1, +6.7% hit@k vs legacy. Fastest at query time (2.4ms). Requires one-time `ledger embed build`.
2. **Default lexical: `precomputed_index`** - Best lexical MRR, second-fastest. No embedding dependency.
3. **Presentation layer: `progressive_disclosure`** - Same MRR as two_stage, adds disclosure levels for the three-layer UX.
4. **Removed: `compressed_attention`** - Only mode that regresses on hit@k.

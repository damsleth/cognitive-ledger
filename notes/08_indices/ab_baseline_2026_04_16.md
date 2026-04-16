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
| semantic_hybrid | blocked | - | - | numpy/torch conflict |

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
1. **precomputed_index** - 0.7259 (best quality, fast)
2. **scope_type_prefilter** - 0.7259 (same quality, slow without precompute)
3. **two_stage** - 0.7254
4. **progressive_disclosure** - 0.7254
5. **legacy** - 0.7217
6. **compressed_attention** - 0.7198 (regression, do not use)

By latency (fastest first):
1. **compressed_attention** - ~4.8ms (but quality regression)
2. **legacy** - ~5.0ms
3. **precomputed_index** - ~6.1ms
4. **progressive_disclosure** - ~7.3ms
5. **two_stage** - ~7.7ms
6. **scope_type_prefilter** - ~40ms (rebuilds candidates every query)

## Recommendations

1. **Default mode: `precomputed_index`** - Best MRR, second-fastest latency. Clear winner.
2. **Presentation layer: `progressive_disclosure`** - Same MRR as two_stage, adds disclosure levels for the three-layer UX.
3. **Avoid: `compressed_attention`** - Only mode that regresses on hit@k.
4. **Avoid: `scope_type_prefilter` standalone** - Same quality as precomputed_index but 7x slower. Only useful as a pipeline stage, not as the top-level mode.
5. **Fix: `semantic_hybrid`** - Blocked on torch/numpy version. Upgrade torch>=2.4 and numpy<2.

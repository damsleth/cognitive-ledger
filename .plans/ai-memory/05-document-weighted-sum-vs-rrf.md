# Plan 05 — Document weighted_sum > RRF + the append-only invariant

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** B2 (RRF is not a free win here) + E1 (append-only raw storage). **Gate:** none — docs only. **Needs:** nothing.

## Why
Pure anti-entropy. Two correct-but-non-obvious decisions are currently undocumented, so a future contributor could "fix" them and regress:
1. **`fusion: weighted_sum` beats `rrf` on this corpus.** RRF operates on ranks and discards the calibrated signal of the hand-tuned weights. Kim's own A/B: `semantic_hybrid` weighted_sum (hit@1 0.733, MRR 0.830) beats RRF (hit@1 0.467). The config (`ledger/config.py:932-960`, `fusion: str = "weighted_sum"`, `rrf_k: 60`) supports both — the default must stay weighted_sum, and *why* must be written down.
2. **Raw storage (YAAMS) is correctly append-only / chunk-based.** Report E1 + arXiv 2603.02473: raw chunk storage stays highly effective; retrieval is the bottleneck. The deterministic-ID `sha256(source:source_id)` + `INSERT OR IGNORE` design is an invariant, not an accident.

## Steps (no code, ponytail: smallest durable docs change)
1. Add a short "Ranking decisions" note near the fusion config explaining weighted_sum>RRF with the A/B numbers above and a one-line "do not switch the default to RRF without a fresh A/B win." Put it where a contributor will see it: a comment block at `ledger/config.py:932` (the `fusion` field docstring) **and** a line in `AGENTS.md` under retrieval guidance.
2. State the append-only invariant explicitly in `AGENTS.md`: "Raw YAAMS items are immutable; never compress/rewrite them. Deterministic ID = sha256(source:source_id), INSERT OR IGNORE. Retrieval quality, not storage compression, is where effort goes."
3. Confirm the actual semantic weights are documented accurately (verified in code): `semantic_weight_vector 0.55`, `_lexical 0.30`, `_scope 0.10`, `_recency 0.05` (`config.py:733-750`). The report's caveat flagged these as needing verification against `retrieval.py` — they're confirmed; record that they were verified.

## Acceptance gate
None (no behaviour change). Sanity:
```bash
.venv/bin/python -m pytest tests/ -q   # nothing broke
grep -n "weighted_sum" AGENTS.md ledger/config.py   # the rationale is present
```

## Done when
The weighted_sum>RRF rationale and the append-only invariant are written in both `AGENTS.md` and the relevant config docstring, with the A/B numbers. No code behaviour changed.

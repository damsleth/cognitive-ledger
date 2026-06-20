# autoresearch ideas

Ideas for the A/B loop (`ledger ab loop`) — one idea per bullet.
Move ideas to **Tried** after running them, regardless of verdict.

---

## Backlog

<!-- Add new ideas here. Planner picks from this section. -->

---

## Tried

| round | key | verdict | delta | note |
|-------|-----|---------|-------|------|
| 1 | consolidation_boost_once | discard | -0.0039 | Apply consolidation_boost to the FUSED cons score once after the RRF accumulation loop, not per-contribution. Current code multiplies every contribution inside the rank loop, effectively squaring the boost for dual-covered consolidations (evidenced by `medlemssak`, `russ`, `troika`). |
| 2 | bm25_magnitude_fts_blend | kept | +0.0015 | Blend normalized BM25 magnitude into FTS RRF contribution in _fuse(): alpha*normed_magnitude*contribution on top of rank-RRF for FTS lists. Exact-lexical-match confidence gaps now visible to ranker. Applied with alpha=0.05. |
| 3 | vec_distance_magnitude_blend | discarded | -0.0398 | Mirror bm25_magnitude_fts_blend for vector lists: normalize cosine distances to [0,1] and add alpha*normed_similarity*contribution for vec results. Quality dropped 0.0398, 2 regressions, p95 worsened to 157.9ms. Distance signal hurts more than it helps in the vec path. |
| 4 | bm25_attention_tokens_corpus | discarded | -0.0054 | In compute_bm25_scores, build the BM25 corpus from attention_tokens instead of note_tokens. attention_tokens captures title, statement, tags, slug, and next-action text — highest-signal fields — while note_tokens also includes the full body, diluting TF for short queries. Crashed during run; quality 0.5463 vs baseline. |
| 1 | attention_bm25_secondary_blend | discarded | 0 | In compute_bm25_scores, build a secondary BM25Okapi instance over attention_tokens in parallel with the existing note_tokens scorer, then return a blended score: (1-alpha)*note_bm25 + alpha*attention_bm25, with alpha ~0.2. Distinct from round-4 crash (which replaced corpus entirely); both scorers always run. Neutral result — no improvement. Configurable via LEDGER_BM25_ATTENTION_ALPHA. |
| 2 | tag_bm25_lexical_blend | discarded | -0.0097 | In lexical_score_component() in ledger/query.py, replace the flat 0.30*tag_overlap ratio with a BM25-scored tag signal: build a BM25Okapi instance over the tag_tokens field of all candidates in the semantic hybrid pool, normalize scores to [0,1], then use alpha*normed_tag_bm25 (alpha~0.30). 1 regression, p95 worsened to 286ms. Reverted. |

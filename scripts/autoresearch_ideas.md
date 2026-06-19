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

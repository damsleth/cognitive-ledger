# memcore

The shared retrieval contract between **cognitive-ledger** and **yaams**.
Both repos rank, fuse, rerank, and trust-annotate retrieval results; before
memcore, yaams carried ported copies of that code (`ledger/rerank.py`, the
`attach_trust_verdicts` logic, a mirrored `ScoredResult`) and the copies
drifted. This package is the one canonical copy.

## Install

```bash
pip install -e ./memcore        # from the cognitive-ledger checkout
```

Installs with **zero dependencies**. Inside the cognitive-ledger repo nothing
needs installing — the package is importable straight off the checkout root,
and the `cognitive-ledger` wheel bundles it.

## Modules

| Module | Contents | Origin |
|---|---|---|
| `memcore.schema` | `ScoredResult`, `RetrievalCandidate`, `ScoreComponents` | `ledger/retrieval_types.py` |
| `memcore.rrf` | `reciprocal_rank_fusion` (key → score), `fuse_ranked_lists` (ranked lists in, fused list out) | `ledger/query.py` / generalized from `ledger/tier1.py` `fuse_results` |
| `memcore.rerank` | Cross-encoder reranker: `get_reranker`, `rerank_pairs`, `candidate_text`, cache helpers | `ledger/rerank.py` |
| `memcore.trust` | `TrustVerdict`, `trust_verdict`, generalized `attach_trust_verdicts` | `ledger/scoring.py` + `ledger/retrieval.py` |

The ledger re-exports everything at the old import paths
(`ledger.retrieval_types.ScoredResult`, `ledger.rerank.*`,
`ledger.query.reciprocal_rank_fusion`, `ledger.scoring.trust_verdict`,
`ledger.retrieval.attach_trust_verdicts`), so existing callers are unaffected.

## The stdlib-only rule

memcore must stay importable and installable with **nothing but the standard
library**. Do not add runtime dependencies. Functions that genuinely need
numpy/sklearn (BM25 scoring, embedding math, PRF vector expansion) stay in
`ledger/` — nothing currently extracted needed them, so nothing was left
behind on that ground.

One nuance: `memcore.rerank.get_reranker` / `rerank_pairs` need
**sentence-transformers at call time**. The import is lazy, so installing and
importing memcore stays dependency-free; actually scoring pairs requires
`pip install memcore[rerank]` (or the ledger's
`./scripts/setup-venv.sh --embeddings`). Calling without it raises a
`RuntimeError` naming the fix.

## Consumers

- **cognitive-ledger** — via the re-export shims above.
- **yaams** (`../yaams`) — hybrid retrieval (mirrored `ScoredResult` + RRF),
  `promote` dedup and trust annotation. yaams should delete its ported copies
  and `pip install -e ../cognitive-ledger/memcore`.

Because yaams consumes these symbols across a repo boundary, treat every
public signature and dataclass field here as a contract: additive changes
only, and update both repos in one motion for anything else. The JSON seams
yaams also consumes (`ledger embed search --json` / `--batch`,
`ledger paths --json`) are locked by `tests/test_seam_golden.py` in the
ledger test suite.

# Plan 04 — Recognition-memory rerank: validate & tune the existing stage

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** B1 (HippoRAG-2 recognition memory; rerank ≥50 candidates). **Gate:** **T1**. **Needs:** Plan 01.

## Status — it already exists (report assumption corrected)
The report framed this as a "~50 LOC bolt-on." **It's already built.** A full rerank mode ships today:
- `ledger/rerank.py` — `get_reranker()` (cached CrossEncoder), `rerank_pairs()`.
- `ledger/query.py:818-860` — `rank_query_semantic_rerank()`: runs `semantic_hybrid` for `rerank_input_k`, cross-encoder re-scores, truncates to `rerank_output_k`.
- `ledger/config.py:841-868` — `rerank_model: "BAAI/bge-reranker-base"`, `rerank_input_k: 20`, `rerank_output_k: 8`, `rerank_batch_size: 32`, `rerank_max_length: 512`.
- Registered as retrieval mode `semantic_rerank` (`config.py:767-775`), **not the default** (default is `semantic_hybrid`).

So this plan is **validation + tuning**, not construction.

## The actual problems to fix
1. **`rerank_input_k: 20` violates the literature.** B1: "reranking only 20 is ineffective; 50→100 improves sharply." Tune `rerank_input_k` up to 50–60, but note the shortlist ceiling is `shortlist_max_candidates: 36` (`config.py:390`) — so first raise the shortlist for the rerank path, or the reranker only ever sees 36. Sweep both.
2. **`bge-reranker-base` is English-only.** Kim's corpus is no/en. Swap-test `BAAI/bge-reranker-v2-m3` (multilingual, the BGE-M3 sibling the report names) vs base — it matters for `lang:no` cases.
3. **Ergonomics:** the report wants `--rerank` to "preserve the fast FTS-only path." Today you get reranking by passing `--retrieval-mode semantic_rerank`. Decide: add a `--rerank` convenience flag on `ledger query` that selects `semantic_rerank`, or just document the mode. **Ponytail: document the existing mode; only add `--rerank` if T1 wins and it ships as default-on.** Don't build the flag for a feature that might not survive the gate.

## Acceptance gate — T1
Recognition-rerank ablation. Run `semantic_hybrid` (baseline) vs `semantic_rerank` (candidate) across candidate-pool sizes, on the 50–100 cases from Plan 01:
```bash
# baseline vs rerank, default pool
ledger ab run --baseline-ref HEAD --candidate-ref HEAD \
  --baseline-mode semantic_hybrid --candidate-mode semantic_rerank \
  --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 \
  --eval-runs 5 --query-runs 3 ; echo "exit=$?"

# sweep input_k (the literature's key variable) via ab loop
ledger ab loop --space rerank_space.yaml \
  --screen-cases tests/fixtures/retrieval_eval_screen.yaml \
  --holdout-cases tests/fixtures/retrieval_eval_holdout.yaml \
  --benchmark-cases tests/fixtures/retrieval_eval_cases.yaml
# rerank_space.yaml sweeps LEDGER_SHORTLIST_MAX {36,50,60} (register a LEDGER_RERANK_INPUT_K override if absent — see config.py:200-316)
```
**Ship rule (from report):** ship only if **MRR gain > latency-budget cost** (`semantic_rerank` adds a CrossEncoder forward pass — watch p95). Confirm the literature's "rerank ≥50" finding holds on this corpus by comparing pool sizes 24 vs 36 vs 60.
**Hard stop:** if T1 shows *no* gain at any pool size → "corpus too small for two-stage retrieval; stop and revisit at scale" (report). Record that finding and do **not** proceed to Plans 13/14.

## [bridge] note
B1 calls rerank a shared capability. YAAMS has no reranker (confirmed) — its promote dedup already shells out to `ledger embed`. If T1 wins, a follow-up can let `yaams promote` call ledger's reranker during dedup. Out of scope here; note it in the PR.

## Regression risk
Low. Adds latency only on the opt-in mode; fast `semantic_hybrid`/`--no-vector` paths untouched. Cache reranker by query hash if p95 is tight.

## Done when
T1 metrics table (baseline vs rerank × pool sizes × both reranker models) is in the PR; a ship/no-ship/stop decision is recorded against the rules above; if ship, `rerank_input_k` is tuned and (optionally) `bge-reranker-v2-m3` is the configured model.

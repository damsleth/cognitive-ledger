# Plan 07 — Validate the `prior` tie-breaker (prior_enabled ablation)

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** B3a (Generative-Agents recency+importance+relevance as cold-start substitute). **Gate:** its own A/B is the deliverable. **Needs:** Plan 01.

## Status — it's ON by default (report assumption corrected)
The report said "validate via the `prior_enabled: false` ablation you already expose," implying it's off. **It's on:** `prior_enabled: bool = True` (`ledger/config.py:536`). Implementation:
- `compute_prior_score()` `retrieval.py:287-342` — importance(=confidence) + recency(half-life decay) + query relevance, weighted `prior_w_importance 0.30` / `_recency 0.30` / `_relevance 0.40` (`config.py:580-594`), half-life 180d.
- `prior_tiebreak_factor()` `retrieval.py:345-357` — decays 1.0→0.0 across `prior_tie_band: 0.02` (`config.py:561`).
- `apply_prior_tiebreak()` `retrieval.py:1447-1503` — blends prior as a tie-breaker only within the band, can't displace a clear winner (this is commit 74f86ce's redesign).

So this is a **pure validation/measurement** task: prove the active tie-breaker earns its place, and that `prior_tie_band 0.02` is doing useful work (not a no-op, not a regressor).

## Steps (mostly A/B, minimal code)
1. **On/off ablation:**
```bash
ledger ab run --baseline-ref HEAD --candidate-ref HEAD \
  --baseline-env  "LEDGER_PRIOR_ENABLED=0" \
  --candidate-env "LEDGER_PRIOR_ENABLED=1" \
  --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 --eval-runs 5 ; echo $?
```
Interpret: candidate (prior on) should be ≥ baseline (prior off). Exit 0 = prior helps; exit 3 = prior is neutral (then it's harmless but document that it's not earning anything); exit 2 = **prior is hurting** → investigate, possibly lower its influence.
2. **Band sweep** — confirm `prior_tie_band 0.02` is the right width (memory notes a 0.20 band helped signal cases but regressed general — so this is sensitive):
```bash
ledger ab loop --space prior_band_space.yaml \
  --screen-cases tests/fixtures/retrieval_eval_screen.yaml \
  --holdout-cases tests/fixtures/retrieval_eval_holdout.yaml \
  --benchmark-cases tests/fixtures/retrieval_eval_cases.yaml
# prior_band_space.yaml: LEDGER_PRIOR_TIE_BAND: [0.01, 0.02, 0.05, 0.10]
```
3. **Cold-query slice** — priors matter most cold; rerun the on/off ablation with `--cold-query` to confirm the cold-start benefit the report attributes to it.
4. No code change unless the ablation says the default is wrong. If a better band emerges *and* clears holdout without general regression, update the default in `config.py` (one line) + `config.sample.yaml`.

## Acceptance gate
The ablation + band sweep tables in the PR, with a recorded verdict: keep `prior_enabled: True` and the chosen `prior_tie_band`, with the numbers that justify it. This plan can also conclude "prior is neutral, keep it (harmless cold-start insurance)" — that's a valid, documented outcome.

## Regression risk
None (measurement). Any default change is itself gated by the same harness.

## Done when
On/off + band-sweep A/B numbers are recorded, the tie-band default is confirmed or retuned (gated), and the cold-start benefit is quantified. This is the prerequisite reality-check before Plan 10 (Ebbinghaus) touches the same ranking real estate.

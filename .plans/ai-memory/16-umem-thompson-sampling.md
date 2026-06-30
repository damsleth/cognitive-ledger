# Plan 16 — U-Mem Thompson-sampling exploration in retrieval

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** B3 (U-Mem semantic-aware Thompson sampling; mitigates cold-start retrieval bias / rich-get-richer). **Gate:** no-regression A/B + explore/exploit metric. **Needs:** Plan 10 (Ebbinghaus reinforcement) **stable first**.

## Precondition
Report: "advanced cold-start; only after reinforcement is stable." Do **not** start this until Plan 10 has shipped and T4 has held. Thompson sampling is the *exploration* counterweight to reinforcement's *exploitation* — building it before reinforcement exists has nothing to counterbalance.

## Problem it solves
Pure similarity retrieval has a rich-get-richer trap: notes that never surface never accrue signals, so they can never warm up (Plan 10). U-Mem occasionally surfaces lower-similarity notes (exploration) to gather signal, breaking the trap.

## Status
- Retrieval ranking + the prior tie-breaker live in `ledger/retrieval.py` (`apply_prior_tiebreak` 1447-1503). Signals/strength from Plan 10.
- This is a small, bounded ranking perturbation — not a new mode.

## Steps
1. **Semantic-aware Thompson sampling over the shortlist.** Treat each candidate's "should I surface this" as a draw from a Beta posterior parameterized by its signal history (hits = successes, misses = failures) and modulated by semantic similarity (the "semantic-aware" part — don't explore wildly off-topic notes). Occasionally promote a high-uncertainty / low-exposure candidate into the returned set.
2. **Bound the exploration rate** with a config knob `LEDGER_EXPLORE_RATE` (float, default 0.0 = off). Register in `config.py` env overrides (183-316) + `config.sample.yaml`. At 0.0 the behaviour is identical to today (safe default).
3. **Keep it a tie-breaker-adjacent perturbation**, not a score override — consistent with the 74f86ce principle that ranking experiments stay as bounded tie-breaks, never linear terms that displace clear winners. Exploration only reorders within the uncertainty band, never demotes a strong match out of top-k.
4. Log exploration events as signals (a distinct `via`/type) so their downstream hit/miss feeds reinforcement (Plan 10) — closing the loop.

## Acceptance gate
```bash
# off vs low explore rate — must not regress general retrieval
ledger ab run --baseline-ref HEAD --candidate-ref HEAD \
  --baseline-env  "LEDGER_EXPLORE_RATE=0.0" \
  --candidate-env "LEDGER_EXPLORE_RATE=0.1" \
  --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 --eval-runs 5 ; echo $?
```
**Ship rule:** exploration must not regress general hit@1/MRR beyond a tiny tolerance (it trades a little immediate precision for long-run signal coverage). The real benefit is longitudinal — document the explore/exploit trade-off and measure cold-note exposure rate (fraction of low-signal notes that get surfaced over a query batch), not just point MRR. Ship default-off (`0.0`); only raise the default if longitudinal signal coverage improves without meaningful MRR loss.

## Regression risk
Moderate — exploration costs immediate precision by design. Contained: default-off, bounded rate, within-band only, never demotes strong matches. Build only after reinforcement (Plan 10) is stable so explored notes can actually warm up.

## Done when
Semantic-aware Thompson exploration available behind `LEDGER_EXPLORE_RATE` (default 0.0), reorders only within the uncertainty band, logs exploration as signals feeding Plan 10; A/B shows no meaningful general regression; explore/exploit trade-off documented. Default stays off pending longitudinal evidence.

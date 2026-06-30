# Plan 10 — Ebbinghaus reinforcement bridge to signal scoring

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** B3b (MemoryBank Ebbinghaus R=e^(−t/S); smooths the cold-start cliff). **Gate:** **T4**. **Needs:** Plan 01, Plan 07 (prior validated first).

## Problem
Signal scoring activates on a **hard binary** at 20 real signals (`signal_min_entries: 20`, `signals.py:502-552` activation gate; `score_weight_signal: 0.0` until then). Below 20 it contributes nothing; at 20 it snaps on. The report wants a **continuous ramp**: treat `retrieval_hit`/`preference_applied` as MemoryBank reinforcement events that bump a per-note strength via R = e^(−t/S), S incremented on recall and Δt reset — so the system warms up smoothly instead of cliff-edging.

## Status — signal machinery exists, the curve doesn't
- `ledger/signals.py`: `SIGNAL_TYPES` (36-47) incl. `retrieval_hit`, `preference_applied`; `summarize_signals()` (183-325) with `signal_score` formula (287-303): `sentiment * usage`, `usage = SIGNAL_USAGE_FLOOR + (1-floor)*min(hits/10,1)`, `SIGNAL_USAGE_FLOOR = 0.5` (line 52). Activation gate `activation_status()` (502-552), `signal_summary_if_active()` (555-569).
- Signals persisted in `notes/08_indices/signals.jsonl`.
- `validation_boost_per_signal 0.03`, `validation_boost_cap 0.15` (`config.py`).

## Steps
1. **Per-note strength via Ebbinghaus.** In `summarize_signals()`, compute a `strength` per note: S starts at 1, increments on each `retrieval_hit`/`preference_applied`; retention R = e^(−Δt/S) where Δt = days since last reinforcement (from signal timestamps in `signals.jsonl`). Reset Δt on recall. This is ~15 lines over data you already aggregate.
2. **Continuous ramp instead of the 20-signal cliff.** Replace the binary `signal_min_entries` gate's effect on *weight* with a ramp: signal contribution scales with `min(real_signals / signal_min_entries, 1.0)` × strength, so it grows 0→full across the warm-up band rather than snapping at 20. Keep `signal_min_entries` as the ramp's ceiling, not an on/off switch.
3. **Keep the existing safety rails:** synthetic signals stay down-weighted (`synthetic_weight 0.5`), boost stays capped (`validation_boost_cap 0.15`), real-only for full activation. The redesigned tie-break (`apply_prior_tiebreak`, 74f86ce) is where signal already applies as a tie-breaker — keep signal as a tie-breaker, not a linear term (per commit 74f86ce + the signal-state memory: linear-additive regressed general eval). **This is critical: do not reintroduce a linear signal term.**
4. New config knob `LEDGER_SIGNAL_REINFORCE` (bool, default off) to A/B the ramp vs the cliff. Register in `config.py` env overrides (183-316) + `config.sample.yaml`.

## Acceptance gate — T4
Cold-start ramp comparison via `--cold-query` + signal weight sweep. Compare three regimes as signal count grows 0→50: (a) signals off (today's `0.0` default), (b) hard-on at 20, (c) Ebbinghaus continuous:
```bash
# (a) vs (c)
ledger ab run --baseline-ref HEAD --candidate-ref HEAD --cold-query \
  --baseline-env  "LEDGER_SIGNAL_REINFORCE=0" "LEDGER_WEIGHT_SIGNAL=0.0" \
  --candidate-env "LEDGER_SIGNAL_REINFORCE=1" "LEDGER_WEIGHT_SIGNAL=0.1" \
  --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 ; echo $?
```
**Ship rule:** the continuous ramp should dominate in the **10–25 signal regime** (the report's claim) without regressing the general cases. Verify `false_positive_rate` on negative cases doesn't rise (reinforcement feedback loop risk).

## Regression risk
Moderate — reinforcement can create rich-get-richer feedback loops. Mitigations already in place: boost cap, synthetic down-weight, tie-breaker-not-linear. Ship behind `LEDGER_SIGNAL_REINFORCE` default-off until T4 clears. Plan 16 (Thompson sampling) is the exploration counterweight — do **not** start 16 until this is stable.

## Done when
Per-note Ebbinghaus strength computed from `signals.jsonl`; the 20-signal cliff becomes a continuous ramp (still tie-breaker, never linear); `LEDGER_SIGNAL_REINFORCE` knob added; T4 shows the ramp dominates 10–25 signals with no general/negative regression.

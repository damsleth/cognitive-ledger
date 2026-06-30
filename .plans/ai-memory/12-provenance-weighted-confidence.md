# Plan 12 — Provenance-weighted confidence as an A/B candidate

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** E2 (TierMem provenance-aware tiered memory; SSGM pre-merge validation). **Gate:** its own A/B. **Needs:** Plan 01.

## Status — fully built, just disabled (report assumption confirmed)
This is "an A/B candidate, not new code." Everything exists:
- `ledger/scoring.py:233-254` — `PROVENANCE_WEIGHTS`: explicit_statement 1.00 > validated 0.95 > corrected 0.90 > observed 0.85 > imported 0.80 > inferred 0.70; floor 0.70 (line 256).
- `ledger/scoring.py:335-374` — `effective_confidence()`: `provenance_weight × (base_confidence + min(boost_per × affirmations, cap))`.
- `ledger/retrieval.py:243-276` — `resolve_confidence()` wires it with `validation_boost_per_signal 0.03`, `validation_boost_cap 0.15`.
- `ledger/config.py:507-515` — `provenance_weighting_enabled: bool = False` (the switch), env `LEDGER_PROVENANCE_WEIGHTING`.

So this plan is **purely measurement**: does turning it on improve or at least not regress retrieval?

## Steps
1. Confirm the corpus actually carries provenance values worth weighting (notes with `provenance:` set). If most notes are unset (→ floor 0.70 uniformly), the A/B is a near-no-op and the finding is "not enough provenance signal yet — revisit after more promotion." Check via a quick frontmatter scan on the fixture corpus.
2. Run the A/B (no code change):
```bash
ledger ab run --baseline-ref HEAD --candidate-ref HEAD \
  --baseline-env  "LEDGER_PROVENANCE_WEIGHTING=0" \
  --candidate-env "LEDGER_PROVENANCE_WEIGHTING=1" \
  --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 --eval-runs 5 ; echo $?
```
3. If it wins (exit 0) and holds on holdout → flip the default to `True` in `config.py` + `config.sample.yaml` (one line). If neutral (exit 3) → keep off, document "behaviour-neutral on current corpus, revisit at scale" (matches how plans 42/43 shipped off-by-default). If regression (exit 2) → keep off, record which provenance class is dragging.

## Acceptance gate
The A/B table in the PR + a recorded keep-off / turn-on decision tied to the exit code. Provenance ordering and formula are confirmed-correct (verified in code) — note that in the PR so it's not re-litigated.

## Regression risk
None (measurement; default stays off unless the gate says otherwise).

## Done when
Provenance-weighting A/B run and recorded; default flipped to on only if it clears the gate on benchmark + holdout; otherwise documented as off-by-default with the reason.

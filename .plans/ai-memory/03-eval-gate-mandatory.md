# Plan 03 — Make the `ledger ab` exit-code gate mandatory

**Target executor:** one Sonnet subagent in `cognitive-ledger`.
**Report:** Stage 0 ("nothing below ships without passing through `ledger_ab`"). **Gate:** none — process/CI. **Blocks:** nothing technically, but should land in Stage 0 so later PRs inherit it.

## Why
The whole roadmap's anti-entropy guarantee is "every retrieval-affecting change passes `ledger ab run` with exit 0/3, never 2." Today that's a convention in prose. Make it a checkable gate so a regressing PR can't silently merge.

## Scope (pick the lightest that holds — ponytail)
This is process, not a feature. Do the minimum that makes the gate enforceable:

1. **A wrapper script** `scripts/ab_gate.sh` (or extend an existing `scripts/ledger_ab`): runs `ledger ab run --baseline-ref main --candidate-ref HEAD --cases tests/fixtures/retrieval_eval_cases.yaml --k 3 --eval-runs 5 --query-runs 3`, captures the exit code, and:
   - exit 0 → print "BENEFICIAL", succeed
   - exit 3 → print "NEUTRAL — only merge if behaviour-neutral/off-by-default", succeed with a warning
   - exit 2 → print the regressing metric delta, **fail (nonzero)**
   - exit 4 → print "INVALID SETUP", fail
   Exit codes are defined in `ledger/ab.py:24-27`.
2. **CI hook (only if CI exists):** check `.github/workflows/` — if there's a CI workflow, add a job that runs the wrapper on PRs that touch `ledger/retrieval.py`, `ledger/query.py`, `ledger/scoring.py`, `ledger/signals.py`, or `config.py`. If there is **no CI**, do not add one — document the gate in `AGENTS.md` / the plan-index conventions instead and ship the wrapper script. (YAGNI: don't stand up CI infrastructure for this.)
3. **Document** the gate once, in `AGENTS.md` near the existing eval guidance: "retrieval-affecting PRs must paste an `ab_gate.sh` run; exit 2 is a hard stop."

## Acceptance gate
```bash
bash scripts/ab_gate.sh ; echo "wrapper-exit=$?"   # runs clean against current main (should be 0 or 3)
```
Plus a self-check the wrapper interprets exit codes correctly: feed it a known-regressing env override and confirm it fails.
```bash
# ponytail self-check: a deliberately bad weight must trip the gate
ledger ab run --baseline-ref HEAD --candidate-ref HEAD \
  --candidate-env "LEDGER_WEIGHT_BM25=0.0" --cases tests/fixtures/retrieval_eval_cases.yaml; echo $?  # expect 2
```

## Done when
`ab_gate.sh` exists, returns nonzero on a regression and zero on a tie/win, CI wired only if CI already exists, and AGENTS.md names the gate as mandatory for retrieval-affecting PRs.

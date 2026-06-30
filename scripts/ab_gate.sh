#!/bin/sh
# Retrieval eval gate for PRs touching retrieval-affecting code.
# Run from repo root: bash scripts/ab_gate.sh
# Exit 0/3 = pass; exit 2/4 = fail.
# ponytail: gate script; nonzero on regression or invalid setup, zero on win/tie.
CASES="${CASES:-tests/fixtures/retrieval_eval_cases.yaml}"

ledger ab run \
  --baseline-ref main \
  --candidate-ref HEAD \
  --cases "$CASES" \
  --k 3 \
  --eval-runs 5 \
  --query-runs 3
code=$?

case $code in
  0) echo "BENEFICIAL" ;;
  3) echo "NEUTRAL — only merge if behaviour-neutral/off-by-default" ;;
  2) echo "REGRESSION — do not merge"; exit 2 ;;
  4) echo "INVALID SETUP — fix harness, not a result"; exit 4 ;;
  *) echo "UNKNOWN exit code $code"; exit "$code" ;;
esac

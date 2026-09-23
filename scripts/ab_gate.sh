#!/bin/sh
# Retrieval eval gate for PRs touching retrieval-affecting code.
# Run from repo root: bash scripts/ab_gate.sh
# Exit 0/3 = pass; exit 2/4 = fail.
# ponytail: gate script; nonzero on regression or invalid setup, zero on win/tie.
# The fixture corpus, never live notes: CI has no user config, and without an
# explicit notes dir the config guard refuses to start, so the gate used to
# die with exit 1 before measuring anything. CASES is corpus-relative - the
# harness requires the cases file to live inside the corpus.
CORPUS="${CORPUS:-$(pwd)/tests/fixtures/corpus/notes}"
CASES="${CASES:-08_indices/retrieval_eval_cases.yaml}"

LEDGER_NOTES_DIR="$CORPUS" ledger ab run \
  --corpus "$CORPUS" \
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

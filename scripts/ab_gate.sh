#!/bin/sh
# Retrieval eval gate for PRs touching retrieval-affecting code.
# Run from repo root: bash scripts/ab_gate.sh
# Exit 0/3 = pass; exit 2/4 = fail.
# ponytail: gate script; nonzero on regression or invalid setup, zero on win/tie.
# The fixture corpus, never live notes: CI has no user config, and without an
# explicit notes dir the config guard refuses to start, so the gate used to
# die with exit 1 before measuring anything. CASES is corpus-relative - the
# harness requires the cases file to live inside the corpus.
# BASELINE_REF: what HEAD is compared against. CI passes origin/main on a PR
# (a PR checkout has no local `main`) and the pre-push commit on a push.
# MODE: pin both arms to one retrieval mode (CI uses legacy: no embeddings).
# The run rewrites derived files (note_index.json) inside the corpus, so the
# default is a throwaway copy of the fixture corpus, not the tracked one.
if [ -z "${CORPUS:-}" ]; then
  CORPUS="$(mktemp -d)/notes"
  cp -R "$(pwd)/tests/fixtures/corpus/notes" "$CORPUS"
fi
source "$(dirname "${BASH_SOURCE[0]}")/layout.sh"
CASES="${CASES:-${LEDGER_INDICES_SUBDIR}/retrieval_eval_cases.yaml}"
BASELINE_REF="${BASELINE_REF:-main}"

set --
if [ -n "${MODE:-}" ]; then
  set -- --baseline-mode "$MODE" --candidate-mode "$MODE"
fi

LEDGER_NOTES_DIR="$CORPUS" ledger ab run \
  --corpus "$CORPUS" \
  --baseline-ref "$BASELINE_REF" \
  --candidate-ref HEAD \
  --cases "$CASES" \
  --k 3 \
  --eval-runs 5 \
  --query-runs 3 \
  "$@"
code=$?

case $code in
  0) echo "BENEFICIAL" ;;
  3) echo "NEUTRAL — only merge if behaviour-neutral/off-by-default" ;;
  2) echo "REGRESSION — do not merge"; exit 2 ;;
  4) echo "INVALID SETUP — fix harness, not a result"; exit 4 ;;
  *) echo "UNKNOWN exit code $code"; exit "$code" ;;
esac

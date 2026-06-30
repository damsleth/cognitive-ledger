#!/bin/sh
# Acceptance gate for Plan 01 (eval-cases-expansion).
# Run from repo root: sh tests/fixtures/run_gate.sh
LEDGER_ROOT="$(pwd)/tests/fixtures/corpus"
export LEDGER_ROOT
.venv/bin/python -m ledger eval \
  --cases tests/fixtures/retrieval_eval_cases.yaml \
  --k 3 \
  --strict-cases
echo "GATE_EXIT=$?"

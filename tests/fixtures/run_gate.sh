#!/bin/sh
# Acceptance gate for Plan 01 (eval-cases-expansion): strict eval on the
# fixture corpus. Run from repo root: sh tests/fixtures/run_gate.sh
# Exits with the eval's own status (it used to echo it and exit 0), and uses
# the `ledger` on PATH so it works in CI as well as from the dev venv.
# Ranking rewrites derived files inside the corpus; use a throwaway copy so
# the tracked fixture stays clean.
LEDGER_ROOT="$(mktemp -d)/corpus"
cp -R "$(pwd)/tests/fixtures/corpus" "$LEDGER_ROOT"
export LEDGER_ROOT
exec ledger eval \
  --cases tests/fixtures/retrieval_eval_cases.yaml \
  --k 3 \
  --strict-cases \
  --retrieval-mode "${MODE:-legacy}"

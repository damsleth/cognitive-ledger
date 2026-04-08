#!/usr/bin/env bash
# Automated safe maintenance for Cognitive Ledger.
#
# Runs index + lint (both safe operations) and writes results.
# Does NOT perform full sleep or append a sleep timeline entry.
# Full sleep requires agent judgment and should be done manually.
#
# Usage: ./scripts/sheep-auto.sh
# Cron: 0 6 * * * cd ~/Code/cognitive-ledger && ./scripts/sheep-auto.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${LEDGER_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
NOTES_DIR="${LEDGER_NOTES_DIR:-$ROOT_DIR/notes}"

if [ -n "${LEDGER_ROOT_DIR:-}" ] || [ -n "${LEDGER_SOURCE_ROOT:-}" ]; then
    echo "Deprecated ledger env vars detected. Use LEDGER_ROOT and LEDGER_SOURCE_NOTES_DIR." >&2
    exit 2
fi

# Activate venv if available
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate" 2>/dev/null || true
fi

SHEEP="$ROOT_DIR/scripts/sheep"
REPORT_FILE="$NOTES_DIR/08_indices/last_auto_maintenance.md"

echo "# Auto Maintenance Report" > "$REPORT_FILE"
echo "" >> "$REPORT_FILE"
echo "Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Run index
echo "## Index" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"
if bash "$SHEEP" index >> "$REPORT_FILE" 2>&1; then
    echo "" >> "$REPORT_FILE"
    echo "Index: OK" >> "$REPORT_FILE"
else
    echo "" >> "$REPORT_FILE"
    echo "Index: FAILED" >> "$REPORT_FILE"
fi
echo "" >> "$REPORT_FILE"

# Run lint
echo "## Lint" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"
lint_output=$(bash "$SHEEP" lint 2>&1)
lint_exit=$?
echo "$lint_output" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"

# Check status
echo "## Status" >> "$REPORT_FILE"
echo "" >> "$REPORT_FILE"
bash "$SHEEP" status >> "$REPORT_FILE" 2>&1
echo "" >> "$REPORT_FILE"

# Surface problems
errors=$(echo "$lint_output" | grep -c "^ERROR:" 2>/dev/null || echo "0")
if [ "$lint_exit" -ne 0 ] || [ "$errors" -gt 0 ]; then
    echo "## Action Required" >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
    echo "Lint found $errors error(s). Run \`sheep lint\` and fix them." >> "$REPORT_FILE"
    echo "" >> "$REPORT_FILE"
fi

echo "Auto maintenance complete. Report: $REPORT_FILE"

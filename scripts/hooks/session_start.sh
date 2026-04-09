#!/usr/bin/env bash
# Session start hook for Cognitive Ledger.
# Outputs a compact boot context payload for the agent.
#
# Usage: bash scripts/hooks/session_start.sh
# Integration: Claude Code SessionStart hook or manual invocation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="${LEDGER_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
NOTES_DIR="${LEDGER_NOTES_DIR:-$ROOT_DIR/notes}"

if [ -n "${LEDGER_ROOT_DIR:-}" ] || [ -n "${LEDGER_SOURCE_ROOT:-}" ]; then
    echo "Deprecated ledger env vars detected. Use LEDGER_ROOT and LEDGER_SOURCE_NOTES_DIR." >&2
    exit 2
fi

# Activate venv if available
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate" 2>/dev/null || true
fi

# Record session baseline for end-of-session capture
BASELINE_FILE="$NOTES_DIR/08_indices/.session_baseline"
mkdir -p "$(dirname "$BASELINE_FILE")"
{
    echo "{"
    echo "  \"head_sha\": \"$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || echo "")\","
    echo "  \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    echo "  \"working_tree\": $(git -C "$ROOT_DIR" status --porcelain=v1 --untracked-files=all 2>/dev/null | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '""')"
    echo "}"
} > "$BASELINE_FILE" 2>/dev/null || true

# First-run setup: inject FIRSTRUN.md if first_run: true in config.yaml
CONFIG_FILE="$ROOT_DIR/config.yaml"
FIRSTRUN_FILE="$ROOT_DIR/FIRSTRUN.md"
if [ -f "$CONFIG_FILE" ] && grep -q "^first_run: true" "$CONFIG_FILE" 2>/dev/null; then
    if [ -f "$FIRSTRUN_FILE" ]; then
        echo "# Ledger Context (First Run)"
        echo ""
        cat "$FIRSTRUN_FILE"
        exit 0
    fi
fi

# Primary path: ledger context --format boot emits the full payload
# (identity, facts, prefs, loops, maintenance status, signal stats)
if [ -x "$ROOT_DIR/scripts/ledger" ]; then
    python3 "$ROOT_DIR/scripts/ledger" context --format boot 2>/dev/null && exit 0
fi

# Fallback: manual boot payload (when ledger CLI is unavailable)
echo "# Session Boot Context"
echo ""

# Identity notes (always load - small and high-signal)
IDENTITY_DIR="$NOTES_DIR/01_identity"
if [ -d "$IDENTITY_DIR" ] && [ -n "$(ls -A "$IDENTITY_DIR"/*.md 2>/dev/null)" ]; then
    echo "## Identity"
    echo ""
    for f in "$IDENTITY_DIR"/id__*.md; do
        [ -f "$f" ] || continue
        name=$(basename "$f")
        summary=$(sed -n '/^---$/,/^---$/d; /^#/d; /^$/d; p' "$f" | head -1)
        echo "- \`$name\` - $summary"
    done
    echo ""
fi

# Open loops (compact list)
echo "## Open Loops"
echo ""
python3 "$ROOT_DIR/scripts/ledger" loops --limit 10 --width 120 2>/dev/null || echo "- (could not load loops)"
echo ""

# Sheep status
echo "## Maintenance"
echo ""
bash "$ROOT_DIR/scripts/sheep" status 2>/dev/null || echo "- (could not check status)"
echo ""

# Signal stats
SIGNALS_FILE="$NOTES_DIR/08_indices/signals.jsonl"
if [ -f "$SIGNALS_FILE" ]; then
    count=$(wc -l < "$SIGNALS_FILE" | tr -d ' ')
    corrections=$(grep -c '"correction"' "$SIGNALS_FILE" 2>/dev/null || echo "0")
    echo "## Signals"
    echo ""
    echo "- Total signals: $count"
    if [ "$corrections" -gt 0 ]; then
        echo "- Corrections pending review: $corrections"
    fi
    echo ""
fi

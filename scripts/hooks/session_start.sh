#!/usr/bin/env bash
# Session start hook for Cognitive Ledger.
# Outputs a compact boot context payload for the agent.
#
# Usage: bash scripts/hooks/session_start.sh
# Integration: Claude Code SessionStart hook or manual invocation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Activate venv if available
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate" 2>/dev/null || true
fi

# Use the ledger context subcommand if available
if [ -x "$ROOT_DIR/scripts/ledger" ]; then
    python3 "$ROOT_DIR/scripts/ledger" context --format boot 2>/dev/null && exit 0
fi

# Fallback: manual boot payload
echo "# Session Boot Context"
echo ""

# Identity notes (always load - small and high-signal)
IDENTITY_DIR="$ROOT_DIR/notes/01_identity"
if [ -d "$IDENTITY_DIR" ] && [ -n "$(ls -A "$IDENTITY_DIR"/*.md 2>/dev/null)" ]; then
    echo "## Identity"
    echo ""
    for f in "$IDENTITY_DIR"/id__*.md; do
        [ -f "$f" ] || continue
        name=$(basename "$f")
        # Extract first non-frontmatter, non-heading content line
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

# Signal stats (if available)
SIGNALS_FILE="$ROOT_DIR/notes/08_indices/signals.jsonl"
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

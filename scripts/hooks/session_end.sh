#!/usr/bin/env bash
# Session end hook for Cognitive Ledger.
# Rebuilds signal summary when needed and reports session activity.
#
# Usage: bash scripts/hooks/session_end.sh
# Integration: Claude Code Notification hook or manual invocation.
#
# Fire-and-forget: always exits 0.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Activate venv if available
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate" 2>/dev/null || true
fi

SIGNALS_FILE="$ROOT_DIR/notes/08_indices/signals.jsonl"
SUMMARY_FILE="$ROOT_DIR/notes/08_indices/signal_summary.json"

# Rebuild signal summary if signals have been added since last summary
if [ -f "$SIGNALS_FILE" ]; then
    signals_mtime=$(stat -c %Y "$SIGNALS_FILE" 2>/dev/null || stat -f %m "$SIGNALS_FILE" 2>/dev/null || echo "0")
    summary_mtime=0
    if [ -f "$SUMMARY_FILE" ]; then
        summary_mtime=$(stat -c %Y "$SUMMARY_FILE" 2>/dev/null || stat -f %m "$SUMMARY_FILE" 2>/dev/null || echo "0")
    fi

    if [ "$signals_mtime" -gt "$summary_mtime" ] 2>/dev/null; then
        python3 "$ROOT_DIR/scripts/ledger" signal summarize 2>/dev/null || true
    fi
fi

# Report notes modified this session (via git)
cd "$ROOT_DIR" 2>/dev/null || exit 0
modified=$(git diff --name-only HEAD 2>/dev/null | grep '^notes/' | wc -l | tr -d ' ')

if [ "$modified" -gt 0 ]; then
    echo "Session summary: $modified note(s) modified."
    if [ "$modified" -gt 5 ]; then
        echo "Consider running: ./scripts/sheep sleep"
    fi
fi

exit 0

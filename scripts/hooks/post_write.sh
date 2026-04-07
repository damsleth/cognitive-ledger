#!/usr/bin/env bash
# Post-write hook for Cognitive Ledger.
# Appends a timeline entry after a note is created or updated.
#
# Usage: bash scripts/hooks/post_write.sh <action> <path> <description>
# Integration: Claude Code PostToolUse hook for Write/Edit on notes/ paths.
#
# This script is fire-and-forget: it always exits 0 to avoid blocking the agent.

set -uo pipefail

ACTION="${1:-}"
NOTE_PATH="${2:-}"
DESCRIPTION="${3:-}"

# Early exit if not a notes/ operation
case "$NOTE_PATH" in
    notes/*) ;;
    */notes/*) ;;
    *) exit 0 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Validate action
case "$ACTION" in
    created|updated|archived|deleted|closed) ;;
    *) exit 0 ;;
esac

# Normalize path to be relative to root
REL_PATH="$NOTE_PATH"
if [[ "$REL_PATH" == "$ROOT_DIR/"* ]]; then
    REL_PATH="${REL_PATH#$ROOT_DIR/}"
fi

TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
TIMELINE_MD="$ROOT_DIR/notes/08_indices/timeline.md"
TIMELINE_JSONL="$ROOT_DIR/notes/08_indices/timeline.jsonl"

# Append to markdown timeline
if [ -f "$TIMELINE_MD" ]; then
    echo "$TIMESTAMP | $ACTION | $REL_PATH | $DESCRIPTION" >> "$TIMELINE_MD" 2>/dev/null || true
fi

# Append to JSONL timeline (pass values as argv to avoid injection)
JSONL_ENTRY=$(python3 -c "
import json, sys
print(json.dumps({
    'ts': sys.argv[1],
    'action': sys.argv[2],
    'path': sys.argv[3],
    'desc': sys.argv[4],
}, ensure_ascii=False))
" "$TIMESTAMP" "$ACTION" "$REL_PATH" "$DESCRIPTION" 2>/dev/null || echo "")

if [ -n "$JSONL_ENTRY" ]; then
    echo "$JSONL_ENTRY" >> "$TIMELINE_JSONL" 2>/dev/null || true
fi

exit 0

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
ROOT_DIR="${LEDGER_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"

if [ -n "${LEDGER_ROOT_DIR:-}" ] || [ -n "${LEDGER_SOURCE_ROOT:-}" ]; then
    echo "Deprecated ledger env vars detected. Use LEDGER_ROOT and LEDGER_SOURCE_NOTES_DIR." >&2
    exit 2
fi

# Activate venv if available
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate" 2>/dev/null || true
fi

resolve_notes_dir() {
    if [ -n "${LEDGER_NOTES_DIR:-}" ]; then
        printf '%s\n' "$LEDGER_NOTES_DIR"
        return
    fi
    python3 "$ROOT_DIR/scripts/ledger" paths --field ledger_notes_dir 2>/dev/null || printf '%s\n' "$ROOT_DIR/notes"
}

NOTES_DIR="$(resolve_notes_dir)"

SIGNALS_FILE="$NOTES_DIR/08_indices/signals.jsonl"
SUMMARY_FILE="$NOTES_DIR/08_indices/signal_summary.json"

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

# Report notes modified this session (via git, against the resolved notes dir)
modified=0
if git -C "$NOTES_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    tracked=$(git -C "$NOTES_DIR" diff --name-only --relative HEAD -- . 2>/dev/null || true)
    untracked=$(git -C "$NOTES_DIR" ls-files --others --exclude-standard -- . 2>/dev/null || true)
    modified=$(
        {
            printf '%s\n' "$tracked"
            printf '%s\n' "$untracked"
        } | awk 'NF && $0 !~ /^08_indices\//' | wc -l | tr -d ' '
    )
fi

if [ "$modified" -gt 0 ]; then
    echo "Session summary: $modified note(s) modified."
    if [ "$modified" -gt 5 ]; then
        echo "Consider running: ./scripts/sheep sleep"
    fi
fi

exit 0

#!/usr/bin/env bash
# Session-end passive capture wrapper.
# Activates venv and runs the Python capture script.
#
# Usage: bash scripts/hooks/session_end_capture.sh
# Fire-and-forget: always exits 0.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Activate venv if available
if [ -f "$ROOT_DIR/.venv/bin/activate" ]; then
    source "$ROOT_DIR/.venv/bin/activate" 2>/dev/null || true
fi

python3 "$ROOT_DIR/scripts/hooks/session_end_capture.py" 2>/dev/null || true

exit 0

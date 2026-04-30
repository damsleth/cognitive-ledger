#!/usr/bin/env bash
# Install the ledger CLI into a pipx-managed venv so it's on PATH from anywhere.
# Usage: ./scripts/add-to-path.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v pipx &>/dev/null; then
    echo "pipx not found. Install it first: brew install pipx" >&2
    exit 1
fi

pipx install --force -e "$REPO_DIR"
echo "Done. 'ledger' is now on PATH."
echo "Test with: ledger --help"

#!/bin/bash
# Build standalone ledger-tui binary

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "error: missing Python environment: $ROOT_DIR/.venv" >&2
  echo "run: ./scripts/setup-venv.sh --dev" >&2
  exit 1
fi

# Build with PyInstaller (outputs to tui/dist/)
"$PYTHON_BIN" -m PyInstaller \
    --onefile \
    --name ledger-tui \
    --clean \
    --noconfirm \
    --distpath dist \
    --workpath build \
    --specpath . \
    --hidden-import=rich._unicode_data \
    --hidden-import=rich._unicode_data.unicode17_0_0 \
    --collect-submodules=rich \
    --collect-submodules=textual \
    __main__.py

echo ""
echo "Build complete: $SCRIPT_DIR/dist/ledger-tui"

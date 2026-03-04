#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PYTHON_BIN="python3"
INSTALL_DEV=true
INSTALL_EMBEDDINGS=true
RECREATE=false

usage() {
  cat <<USAGE
Usage: ./scripts/setup-venv.sh [--minimal] [--dev] [--embeddings] [--full] [--python <python-bin>] [--recreate]

Options:
  --minimal           Install only base dependencies from pyproject.toml
  --dev               Install development dependencies (pyinstaller, pytest)
  --embeddings        Install local-embedding dependencies
                      (pinned sentence-transformers/torch stack)
  --full              Install both --dev and --embeddings (default behavior)
  --python <bin>      Python interpreter to use for creating the venv (default: python3)
  --recreate          Delete and recreate .venv before installing
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --minimal)
      INSTALL_DEV=false
      INSTALL_EMBEDDINGS=false
      shift
      ;;
    --dev)
      INSTALL_DEV=true
      shift
      ;;
    --embeddings)
      INSTALL_EMBEDDINGS=true
      shift
      ;;
    --full)
      INSTALL_DEV=true
      INSTALL_EMBEDDINGS=true
      shift
      ;;
    --python)
      if [[ $# -lt 2 ]]; then
        echo "error: --python requires a value" >&2
        exit 1
      fi
      PYTHON_BIN="$2"
      shift 2
      ;;
    --recreate)
      RECREATE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "error: python interpreter not found: $PYTHON_BIN" >&2
  exit 1
fi

if [[ "$RECREATE" == "true" && -d "$VENV_DIR" ]]; then
  rm -rf "$VENV_DIR"
fi

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel

# Build extras spec from flags
EXTRAS=""
if [[ "$INSTALL_DEV" == "true" ]]; then
  EXTRAS="${EXTRAS}dev,"
fi
if [[ "$INSTALL_EMBEDDINGS" == "true" ]]; then
  EXTRAS="${EXTRAS}embeddings,"
fi
EXTRAS="${EXTRAS%,}"  # trim trailing comma

if [[ -n "$EXTRAS" ]]; then
  if ! "$VENV_DIR/bin/python" -m pip install -e "$ROOT_DIR[$EXTRAS]"; then
    echo "" >&2
    echo "error: failed to install optional dependencies [$EXTRAS]." >&2
    echo "hint: sentence-transformers/torch may not support this Python version." >&2
    echo "hint: try --python python3.12 (or python3.11), then rerun with --recreate --embeddings." >&2
    echo "hint: you can still use OpenAI embeddings without local embedding deps." >&2
    exit 1
  fi
else
  "$VENV_DIR/bin/python" -m pip install -e "$ROOT_DIR"
fi

echo "Python environment ready: $VENV_DIR"
if [[ "$INSTALL_EMBEDDINGS" != "true" ]]; then
  echo "Local embedding deps not installed."
  echo "To add them: ./scripts/setup-venv.sh --embeddings"
fi
echo "Run scripts directly (they auto-use .venv if present), or use:"
echo "  source $VENV_DIR/bin/activate"

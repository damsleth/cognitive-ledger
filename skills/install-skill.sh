
#!/usr/bin/env bashw
set -euo pipefail

# Install symlinks for the cognitive-ledger skill into the expected skill folders

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/cognitive-ledger"

TARGETS=(
    "$HOME/.codex/skills/cognitive-ledger"
    "$HOME/.claude/skills/cognitive-ledger"
    "$HOME/.copilot/skills/cognitive-ledger"
)

if [ ! -e "$SKILL_SRC" ]; then
    echo "Source skill folder not found: $SKILL_SRC" >&2
    exit 1
fi

for target in "${TARGETS[@]}"; do
    mkdir -p "$(dirname "$target")"
    
    if [ -L "$target" ] || [ -e "$target" ]; then
        rm -rf "$target"
    fi
    
    ln -s "$SKILL_SRC" "$target"
    echo "Created symlink: $target -> $SKILL_SRC"
done

echo "All done."

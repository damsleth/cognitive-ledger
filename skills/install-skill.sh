
#!/usr/bin/env bash
set -euo pipefail

# Install symlinks for the notes skill into the expected skill folders

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/notes"

TARGETS=(
    "$HOME/.codex/skills/notes"
    "$HOME/.claude/skills/notes"
    "$HOME/.copilot/skills/notes"
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

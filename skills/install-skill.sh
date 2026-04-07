#!/usr/bin/env bash
set -euo pipefail

# Install symlinks for the notes skill into the expected skill folders.
# Safety: does not overwrite existing real directories (user customizations).
# Use --force to override.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$SCRIPT_DIR/notes"
FORCE=false

for arg in "$@"; do
    case "$arg" in
        --force) FORCE=true ;;
    esac
done

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

    if [ -L "$target" ]; then
        # Existing symlink
        current_target=$(readlink "$target" 2>/dev/null || echo "")
        if [ "$current_target" = "$SKILL_SRC" ]; then
            echo "Already linked: $target -> $SKILL_SRC (skipped)"
            continue
        fi
        # Symlink points elsewhere
        if [ "$FORCE" = true ]; then
            rm -f "$target"
            echo "Removed stale symlink: $target -> $current_target"
        else
            echo "WARN: $target points to $current_target (not $SKILL_SRC)"
            echo "  Use --force to update, or remove manually"
            continue
        fi
    elif [ -d "$target" ]; then
        # Real directory (user customizations)
        if [ "$FORCE" = true ]; then
            rm -rf "$target"
            echo "Removed existing directory: $target"
        else
            echo "WARN: $target is a real directory (possible customizations)"
            echo "  Use --force to replace, or remove manually"
            continue
        fi
    elif [ -e "$target" ]; then
        # Some other file type
        if [ "$FORCE" = true ]; then
            rm -f "$target"
        else
            echo "WARN: $target exists but is not a symlink or directory. Use --force to replace."
            continue
        fi
    fi

    ln -s "$SKILL_SRC" "$target"
    echo "Created symlink: $target -> $SKILL_SRC"
done

echo "Done."

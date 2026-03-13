#!/bin/bash
# Install git hooks from scripts/hooks/ into .git/hooks/
# Run once after clone: bash scripts/setup-hooks.sh

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_SRC="$REPO_DIR/scripts/hooks"
HOOKS_DST="$REPO_DIR/.git/hooks"

if [ ! -d "$HOOKS_SRC" ]; then
    echo "ERROR: scripts/hooks/ not found"
    exit 1
fi

for hook in "$HOOKS_SRC"/*; do
    name=$(basename "$hook")
    cp "$hook" "$HOOKS_DST/$name"
    chmod +x "$HOOKS_DST/$name"
    echo "Installed: $name"
done

echo "Done. All hooks installed."

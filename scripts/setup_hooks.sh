#!/bin/bash
# One-time setup for new machine
# Usage: bash scripts/setup_hooks.sh

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_DIR="${REPO_DIR}/.git/hooks"
BRAIN_DB="${REPO_DIR}/brain-export.db"
BRAIN_NAME="ema-trading-bot"
NMEM_DIR="$HOME/.neuralmemory/brains"

echo "=== EMA-Trading-Bot Setup ==="

# 1. Detect platform + nmem path
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
    NMEM="$(where nmem 2>/dev/null || echo "")"
    PLATFORM="windows"
else
    NMEM="$(which nmem 2>/dev/null || echo "")"
    PLATFORM="unix"
fi

if [ -z "$NMEM" ]; then
    echo "nmem not found, trying python module..."
    NMEM_CMD='python3 -c "from neural_memory.cli import app; app()" --'
else
    NMEM_CMD="$NMEM"
fi

echo "Platform: $PLATFORM"
echo "nmem: ${NMEM_CMD:-not found}"

# 2. Import brain DB
echo ""
echo "--- Brain Import ---"

mkdir -p "$NMEM_DIR"
if [ -f "$BRAIN_DB" ]; then
    cp "$BRAIN_DB" "${NMEM_DIR}/${BRAIN_NAME}.db"
    echo "Imported brain-export.db -> ~/.neuralmemory/brains/${BRAIN_NAME}.db"
else
    echo "WARNING: brain-export.db not found"
fi

# 3. Update neuralmemory config
NMEM_CONFIG_TOML="$HOME/.neuralmemory/config.toml"
NMEM_CONFIG_JSON="$HOME/.neuralmemory/config.json"

if [ -f "$NMEM_CONFIG_TOML" ]; then
    sed -i.bak "s/current_brain = \".*\"/current_brain = \"${BRAIN_NAME}\"/" "$NMEM_CONFIG_TOML"
    rm -f "${NMEM_CONFIG_TOML}.bak"
    echo "Updated config.toml: current_brain = ${BRAIN_NAME}"
fi

if [ -f "$NMEM_CONFIG_JSON" ]; then
    sed -i.bak "s/\"current_brain\": \".*\"/\"current_brain\": \"${BRAIN_NAME}\"/" "$NMEM_CONFIG_JSON"
    rm -f "${NMEM_CONFIG_JSON}.bak"
    echo "Updated config.json: current_brain = ${BRAIN_NAME}"
fi

# 4. Install post-commit hook
echo ""
echo "--- Git Hooks ---"
cat > "${HOOKS_DIR}/post-commit" << 'HOOK'
#!/bin/bash
# Auto-update active_context.md after each commit

echo "Auto-updating active_context.md..."

python3 scripts/auto_update_context.py 2>/dev/null

# If context was updated, add it to the commit
if git diff --quiet knowledge/active_context.md; then
    echo "No changes needed"
else
    echo "Committing context update..."
    git add knowledge/active_context.md
    git commit --amend --no-edit --no-verify
    echo "Context updated and committed"
fi

# --- Neural Memory auto-capture ---
if [ -f "scripts/nmem_commit_store.sh" ]; then
    bash scripts/nmem_commit_store.sh
fi
HOOK
chmod +x "${HOOKS_DIR}/post-commit"
echo "Installed post-commit hook"

# 5. Install pre-push hook
cat > "${HOOKS_DIR}/pre-push" << 'HOOK'
#!/bin/bash
# Pre-push: sync neural memory brain DB

echo "Syncing neural memory before push..."
if [ -f "scripts/nmem_maintenance.sh" ]; then
    bash scripts/nmem_maintenance.sh

    # Auto-commit brain-export.db if changed
    if ! git diff --quiet brain-export.db 2>/dev/null; then
        echo "Brain DB changed, committing..."
        git add brain-export.db
        git commit -m "chore: sync brain-export.db" --no-verify
    fi
fi
HOOK
chmod +x "${HOOKS_DIR}/pre-push"
echo "Installed pre-push hook"

# 6. Verify
echo ""
echo "--- Verify ---"
if [ -f "${NMEM_DIR}/${BRAIN_NAME}.db" ]; then
    echo "Brain DB: $(ls -lh "${NMEM_DIR}/${BRAIN_NAME}.db" | awk '{print $5}')"
fi
echo "=== Setup Complete ==="

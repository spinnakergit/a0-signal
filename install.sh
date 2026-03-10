#!/bin/bash
# Install the Signal plugin into an Agent Zero instance.
#
# Usage:
#   ./install.sh                          # Auto-detect Agent Zero root (/a0 or /git/agent-zero)
#   ./install.sh /path/to/agent-zero      # Install to specified path
#
# For Docker:
#   docker exec <container> bash -c "cd /tmp && ./install.sh"
#   Or: docker cp signal-plugin/ <container>:/a0/usr/plugins/signal && \
#       docker exec <container> ln -sf /a0/usr/plugins/signal /a0/plugins/signal

set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-detect A0 root: /a0 is the runtime copy, /git/agent-zero is the source
if [ -n "${1:-}" ]; then
    A0_ROOT="$1"
elif [ -d "/a0/plugins" ]; then
    A0_ROOT="/a0"
elif [ -d "/git/agent-zero/plugins" ]; then
    A0_ROOT="/git/agent-zero"
else
    echo "Error: Cannot find Agent Zero. Pass the path as argument."
    exit 1
fi

PLUGIN_DIR="$A0_ROOT/usr/plugins/signal"

echo "=== Signal Plugin Installer ==="
echo "Source:  $SCRIPT_DIR"
echo "Target:  $PLUGIN_DIR"
echo ""

# Create target directory
mkdir -p "$PLUGIN_DIR"

# Copy plugin files
echo "Copying plugin files..."
cp -r "$SCRIPT_DIR/plugin.yaml" "$PLUGIN_DIR/"
cp -r "$SCRIPT_DIR/default_config.yaml" "$PLUGIN_DIR/"
cp -r "$SCRIPT_DIR/initialize.py" "$PLUGIN_DIR/"
cp -r "$SCRIPT_DIR/helpers" "$PLUGIN_DIR/"
cp -r "$SCRIPT_DIR/tools" "$PLUGIN_DIR/"
cp -r "$SCRIPT_DIR/prompts" "$PLUGIN_DIR/"
cp -r "$SCRIPT_DIR/api" "$PLUGIN_DIR/"
cp -r "$SCRIPT_DIR/webui" "$PLUGIN_DIR/"
cp -r "$SCRIPT_DIR/extensions" "$PLUGIN_DIR/"

# Copy the standalone bridge runner to A0 root
# MUST live at /a0/ root to avoid Python import shadowing
# (plugins/signal/helpers/ would shadow A0's core /a0/helpers/)
if [ -f "$SCRIPT_DIR/run_signal_bridge.py" ]; then
    cp "$SCRIPT_DIR/run_signal_bridge.py" "$A0_ROOT/run_signal_bridge.py"
    chmod 755 "$A0_ROOT/run_signal_bridge.py"
    echo "Bridge runner installed at: $A0_ROOT/run_signal_bridge.py"
fi

# Copy docs and README if present
[ -d "$SCRIPT_DIR/docs" ] && cp -r "$SCRIPT_DIR/docs" "$PLUGIN_DIR/"
[ -f "$SCRIPT_DIR/README.md" ] && cp "$SCRIPT_DIR/README.md" "$PLUGIN_DIR/"
[ -f "$SCRIPT_DIR/LICENSE" ] && cp "$SCRIPT_DIR/LICENSE" "$PLUGIN_DIR/"

# Create data directory with restrictive permissions
mkdir -p "$PLUGIN_DIR/data"
chmod 700 "$PLUGIN_DIR/data"

# Copy skills to usr/skills
SKILLS_DIR="$A0_ROOT/usr/skills"
echo "Copying skills..."
for skill_dir in "$SCRIPT_DIR/skills"/*/; do
    skill_name="$(basename "$skill_dir")"
    mkdir -p "$SKILLS_DIR/$skill_name"
    cp -r "$skill_dir"* "$SKILLS_DIR/$skill_name/"
done

# Run initialization (install Python deps + optionally signal-cli)
INIT_FLAGS=""
if [ "${SIGNAL_INTEGRATED:-}" = "1" ] || [ "${1:-}" = "--integrated" ] || [ "${2:-}" = "--integrated" ]; then
    INIT_FLAGS="--integrated"
    echo "Installing dependencies + signal-cli native binary (integrated mode)..."
else
    echo "Installing dependencies (external mode)..."
    echo "  Tip: Pass --integrated or set SIGNAL_INTEGRATED=1 to also install signal-cli natively."
fi
python3 "$PLUGIN_DIR/initialize.py" $INIT_FLAGS || python "$PLUGIN_DIR/initialize.py" $INIT_FLAGS

# Enable plugin
touch "$PLUGIN_DIR/.toggle-1"

# Create symlink so 'from plugins.signal.helpers...' imports work
SYMLINK="$A0_ROOT/plugins/signal"
if [ ! -e "$SYMLINK" ]; then
    ln -sf "$PLUGIN_DIR" "$SYMLINK"
    echo "Created symlink: $SYMLINK -> $PLUGIN_DIR"
fi

# Create symlink for agent_init extension
# A0 only scans /a0/extensions/python/agent_init/, not plugin subdirectories
INIT_EXT="$A0_ROOT/extensions/python/agent_init/_10_signal_chat.py"
if [ ! -e "$INIT_EXT" ] && [ -f "$PLUGIN_DIR/extensions/python/agent_init/_10_signal_chat.py" ]; then
    mkdir -p "$(dirname "$INIT_EXT")"
    ln -sf "$PLUGIN_DIR/extensions/python/agent_init/_10_signal_chat.py" "$INIT_EXT"
    echo "Created extension symlink: $INIT_EXT"
fi

# If /a0 is a runtime copy of /git/agent-zero, also install there
if [ "$A0_ROOT" = "/a0" ] && [ -d "/git/agent-zero/usr" ]; then
    GIT_PLUGIN="/git/agent-zero/usr/plugins/signal"
    mkdir -p "$(dirname "$GIT_PLUGIN")"
    cp -r "$PLUGIN_DIR" "$GIT_PLUGIN" 2>/dev/null || true
fi

echo ""
echo "=== Installation complete ==="
echo "Plugin installed to: $PLUGIN_DIR"
echo "Skills installed to: $SKILLS_DIR"
echo ""
if [ -n "$INIT_FLAGS" ]; then
    echo "Mode: Integrated (signal-cli native binary installed inside the container)"
    echo ""
    echo "Next steps:"
    echo "  1. Start the signal-cli daemon:"
    echo "       supervisorctl start signal_cli"
    echo ""
    echo "  2. Link your phone number (from inside the container):"
    echo "       /opt/signal-cli-native/bin/signal-cli --config /opt/signal-cli-data link -n AgentZero"
    echo "     Then scan the QR code with Signal app > Settings > Linked Devices"
    echo ""
    echo "  3. Copy signal-cli data to persistent location:"
    echo "       cp -r /root/.local/share/signal-cli/data/* /opt/signal-cli-data/"
    echo ""
    echo "  4. Configure the plugin in the WebUI (phone number, allowed contacts)"
    echo "     Or set SIGNAL_PHONE_NUMBER in /a0/usr/.env"
    echo ""
    echo "  5. Enable autostart and start the bridge:"
    echo "       supervisorctl start signal_cli signal_bridge"
    echo ""
    echo "  6. Restart Agent Zero: supervisorctl restart run_ui"
    echo ""
    echo "  See docs/QUICKSTART.md for the complete step-by-step guide."
else
    echo "Mode: External (requires a separate signal-cli-rest-api container)"
    echo ""
    echo "Next steps:"
    echo "  1. Set up signal-cli-rest-api (Docker container)"
    echo "     See docs/SETUP_SIGNAL_API.md for setup instructions."
    echo "  2. Register or link a phone number with signal-cli"
    echo "  3. Configure the plugin in the Signal settings (WebUI)"
    echo "     Or set SIGNAL_API_URL and SIGNAL_PHONE_NUMBER environment variables"
    echo "  4. Restart Agent Zero: supervisorctl restart run_ui"
    echo "  5. Ask the agent: 'Send a Signal message to +1234567890'"
fi

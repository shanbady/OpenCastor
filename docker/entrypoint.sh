#!/bin/bash
set -e

# Ensure ~/.opencastor exists and is writable (needed for wizard token storage)
mkdir -p "${HOME:-/home/castor}/.opencastor"

CONFIG_PATH="${CASTOR_CONFIG:-/app/config/robot.rcan.yaml}"

# If no config exists, scaffold a minimal one and print clear instructions
if [ ! -f "$CONFIG_PATH" ]; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║           OpenCastor — First Run Setup                      ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  No config found at: $CONFIG_PATH"
    echo ""
    echo "  Generating a minimal starter config..."
    echo ""

    python -m castor.init_config --output "$CONFIG_PATH"

    echo "  ✓ Created: $CONFIG_PATH"
    echo ""
    echo "  Next steps:"
    echo "  1. Edit the config:  nano ./config/robot.rcan.yaml"
    echo "  2. Add your AI provider key to .env (ANTHROPIC_API_KEY, GOOGLE_API_KEY, etc.)"
    echo "  3. Restart: docker compose restart"
    echo ""
    echo "  For interactive setup:  docker run -it --rm opencastor castor wizard"
    echo "  Full docs: https://opencastor.com/docs/"
    echo ""
fi

exec "$@"

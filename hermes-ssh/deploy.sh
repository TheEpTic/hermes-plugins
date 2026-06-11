#!/usr/bin/env bash
# deploy.sh — symlink hermes-ssh into ~/.hermes/plugins/
#
# Usage:
#   ./deploy.sh          # install
#   ./deploy.sh --clean  # uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_PLUGINS="${HERMES_PLUGINS:-$HOME/.hermes/plugins}"
PLUGIN_NAME="hermes-ssh"
SRC="$SCRIPT_DIR/src/ssh_tools"
DST="$HERMES_PLUGINS/$PLUGIN_NAME"

if [[ "${1:-}" == "--clean" ]]; then
    if [ -L "$DST" ]; then
        rm "$DST"
        echo "removed: $DST"
    else
        echo "nothing to remove"
    fi
    exit 0
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "Usage: $0 [--clean]"
    echo "  No args    Symlink plugin into ~/.hermes/plugins/"
    echo "  --clean    Remove the symlink"
    exit 0
fi

mkdir -p "$HERMES_PLUGINS"

if [ -L "$DST" ]; then
    rm "$DST"
elif [ -d "$DST" ]; then
    echo "SKIP: $DST exists and is not a symlink — not removing"
    exit 1
fi

ln -s "$SRC" "$DST"
echo "deployed: $PLUGIN_NAME -> $SRC"
echo "Restart Hermes (/reset or gateway restart) to load."

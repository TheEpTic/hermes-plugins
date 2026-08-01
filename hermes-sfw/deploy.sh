#!/usr/bin/env bash
# deploy.sh — symlink hermes-sfw Python + desktop assets into ~/.hermes/
#
# Usage:
#   ./deploy.sh          # install
#   ./deploy.sh --clean  # uninstall

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_PLUGINS="${HERMES_PLUGINS:-$HOME/.hermes/plugins}"
HERMES_DESKTOP_PLUGINS="${HERMES_DESKTOP_PLUGINS:-$HOME/.hermes/desktop-plugins}"
PLUGIN_NAME="hermes-sfw"
SRC="$SCRIPT_DIR/src/hermes_sfw"
DST="$HERMES_PLUGINS/$PLUGIN_NAME"
DESKTOP_SRC="$SCRIPT_DIR/desktop-plugins/$PLUGIN_NAME"
DESKTOP_DST="$HERMES_DESKTOP_PLUGINS/$PLUGIN_NAME"

remove_link() {
    local path="$1"
    if [ -L "$path" ]; then
        rm "$path"
        echo "removed: $path"
    fi
}

install_link() {
    local source="$1"
    local destination="$2"
    if [ -L "$destination" ]; then
        rm "$destination"
    elif [ -e "$destination" ]; then
        echo "SKIP: $destination exists and is not a symlink — not removing"
        exit 1
    fi
    ln -s "$source" "$destination"
    echo "deployed: $destination -> $source"
}

if [[ "${1:-}" == "--clean" ]]; then
    remove_link "$DST"
    remove_link "$DESKTOP_DST"
    exit 0
fi

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    echo "Usage: $0 [--clean]"
    echo "  No args    Symlink Python plugin + desktop plugin"
    echo "  --clean    Remove only the symlinks created by this script"
    exit 0
fi

mkdir -p "$HERMES_PLUGINS" "$HERMES_DESKTOP_PLUGINS"
install_link "$SRC" "$DST"
install_link "$DESKTOP_SRC" "$DESKTOP_DST"
echo "reload the Hermes Desktop plugin inventory when you choose; this script does not restart services."

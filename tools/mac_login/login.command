#!/bin/bash
set -euo pipefail
umask 077

case "${0##*/}" in
    'OCDJ Instagram Connector.command') options=(instagram --allow-keychain-prompt) ;;
    'OCDJ Playback Connector.command') options=(playback) ;;
    *) exit 2 ;;
esac

install_dir="$HOME/Library/Application Support/OCDJ/login-connectors"
cd "$install_dir/code"
exec "$HOME/.local/share/ocdj/mac-connectors-runtime/bin/python" -u -m tools.mac_login.restore \
    "${options[@]}" --config "$install_dir/config.json" --detach

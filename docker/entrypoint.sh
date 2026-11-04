#!/bin/sh
# Generate a template config on first run, then exec the service.
set -eu

CONFIG="${OATH_RELAY_CONFIG:-/config/config.toml}"

if [ ! -f "$CONFIG" ]; then
    echo "oath-relay: no config at $CONFIG, writing a template" >&2
    mkdir -p "$(dirname "$CONFIG")" 2>/dev/null || true
    oath-relay init "$CONFIG" >&2 || \
        echo "oath-relay: could not write $CONFIG; mount one read-only instead" >&2
fi

exec oath-relay "$@"

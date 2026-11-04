#!/bin/sh
# oath-relay container entrypoint.
#
# Runs as root only long enough to honour PUID/PGID, then drops privileges.
# Starting the container with `--user` also works: the drop is skipped.
set -eu

CONFIG="${OATH_RELAY_CONFIG:-/config/config.jsonc}"
PUID="${PUID:-10001}"
PGID="${PGID:-10001}"

if [ ! -f "$CONFIG" ]; then
    echo "oath-relay: no config at $CONFIG, writing a template" >&2
    mkdir -p "$(dirname "$CONFIG")" 2>/dev/null || true
    oath-relay init "$CONFIG" >&2 || \
        echo "oath-relay: could not write $CONFIG; mount one instead" >&2
fi

if [ "$(id -u)" = "0" ]; then
    getent group "$PGID" >/dev/null 2>&1 || groupadd -g "$PGID" oath-run 2>/dev/null || true
    getent passwd "$PUID" >/dev/null 2>&1 || \
        useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin oath-run 2>/dev/null || true

    # Make sure the persisted state directory belongs to the target user.
    [ -d /state ] && chown -R "$PUID:$PGID" /state 2>/dev/null || true

    exec gosu "$PUID:$PGID" oath-relay "$@"
fi

exec oath-relay "$@"

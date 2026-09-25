#!/bin/sh

set -eu

keyring_dir="$HOME/.local/share/keyrings"
password_file="$keyring_dir/.ductor-password"

mkdir -p "$keyring_dir"
chmod 700 "$keyring_dir"

if [ -n "${DUCTOR_KEYRING_PASSWORD:-}" ]; then
    keyring_password=$DUCTOR_KEYRING_PASSWORD
elif [ -f "$password_file" ]; then
    keyring_password=$(cat "$password_file")
else
    keyring_password=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    (umask 077 && printf '%s' "$keyring_password" >"$password_file")
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/ductor-runtime}"
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"

export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
dbus-daemon --session --fork --address="$DBUS_SESSION_BUS_ADDRESS"
printf '%s' "$keyring_password" | gnome-keyring-daemon --unlock --components=secrets >/dev/null
unset keyring_password
unset DUCTOR_KEYRING_PASSWORD

# billion-context proxy (disable with BILI_ENABLED=0)
if [ "${BILI_ENABLED:-1}" = "1" ] && command -v bili >/dev/null 2>&1; then
    bili_dir="$HOME/.ductor/billion-context"
    mkdir -p "$bili_dir"
    if [ ! -f "$bili_dir/claude-mcp.json" ]; then
        printf '%s\n' '{"mcpServers":{"bili":{"command":"bili","args":["mcp"]}}}' \
            >"$bili_dir/claude-mcp.json"
    fi
    if [ ! -f "$bili_dir/claude-bili-settings.json" ]; then
        printf '%s\n' '{"env":{"ANTHROPIC_BASE_URL":"http://127.0.0.1:8787/bili/https://api.z.ai/api/anthropic"}}' \
            >"$bili_dir/claude-bili-settings.json"
    fi
    (
        set +e
        while :; do
            bili
            sleep 2
        done
    ) >/dev/null 2>&1 &
    bili_port="${ACP_PORT:-8787}"
    bili_tries=0
    while [ "$bili_tries" -lt 40 ]; do
        if curl -fsS "http://127.0.0.1:${bili_port}/__bili/health" >/dev/null 2>&1; then
            break
        fi
        bili_tries=$((bili_tries + 1))
        sleep 0.5
    done
fi

exec "$@"

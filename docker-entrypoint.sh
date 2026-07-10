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

exec "$@"

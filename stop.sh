#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-${1:-quest-bot}}"
UNIT_NAME="$SERVICE_NAME.service"

fail() {
    printf 'stop.sh: %s\n' "$*" >&2
    exit 1
}

if (( $# > 1 )); then
    fail "Usage: ./stop.sh [service-name]"
fi
if [[ ! "$SERVICE_NAME" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
    fail "Service name may contain only letters, digits, dot, underscore, and hyphen."
fi
if ! command -v systemctl >/dev/null 2>&1; then
    fail "systemctl is unavailable; this script requires a systemd server."
fi

if [[ "$(id -u)" -eq 0 ]]; then
    SYSTEMCTL=(systemctl)
else
    if ! command -v sudo >/dev/null 2>&1; then
        fail "sudo is required to stop the system service."
    fi
    sudo -v
    SYSTEMCTL=(sudo systemctl)
fi

if ! "${SYSTEMCTL[@]}" cat "$UNIT_NAME" >/dev/null 2>&1; then
    fail "$UNIT_NAME is not installed. Run deploy.sh first."
fi

if ! "${SYSTEMCTL[@]}" is-active --quiet "$UNIT_NAME"; then
    printf '%s is already stopped.\n' "$UNIT_NAME"
    exit 0
fi

"${SYSTEMCTL[@]}" stop "$UNIT_NAME"
if "${SYSTEMCTL[@]}" is-active --quiet "$UNIT_NAME"; then
    fail "$UNIT_NAME is still active after the stop request. Check: sudo journalctl -u $UNIT_NAME -n 80"
fi

printf '%s stopped successfully. It remains enabled and will start on the next reboot.\n' "$UNIT_NAME"
printf 'Logs: sudo journalctl -u %s -f\n' "$UNIT_NAME"

#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SERVICE_NAME="${SERVICE_NAME:-quest-bot}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$PROJECT_DIR/.venv"
UNIT_NAME="$SERVICE_NAME.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"

fail() {
    printf 'deploy.sh: %s\n' "$*" >&2
    exit 1
}

if [[ "$(id -u)" -eq 0 ]]; then
    fail "Run this script as the deployment user, not with sudo. It will use sudo for systemd operations."
fi
if ! command -v sudo >/dev/null 2>&1; then
    fail "sudo is required to install and manage the systemd service."
fi
if ! command -v systemctl >/dev/null 2>&1; then
    fail "systemctl is unavailable; this deployment script requires a systemd server."
fi
if ! command -v pg_dump >/dev/null 2>&1; then
    fail "pg_dump is required for shutdown backups. Install the matching PostgreSQL client package."
fi
if [[ "$PROJECT_DIR" =~ [[:space:]] ]]; then
    fail "The project path must not contain spaces: $PROJECT_DIR"
fi
if [[ ! "$SERVICE_NAME" =~ ^[a-zA-Z0-9_.-]+$ ]]; then
    fail "SERVICE_NAME may contain only letters, digits, dot, underscore, and hyphen."
fi
if [[ ! -f "$PROJECT_DIR/main.py" || ! -f "$PROJECT_DIR/requirements.txt" ]]; then
    fail "Run this script from a Quest Bot checkout containing main.py and requirements.txt."
fi
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    fail "Missing $PROJECT_DIR/.env. Copy .env.example to .env and fill in the required values first."
fi
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    fail "Python executable '$PYTHON_BIN' was not found. Install Python 3.11+ or set PYTHON_BIN."
fi
"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("Python 3.11 or newer is required.")
PY

sudo -v
chmod 600 "$PROJECT_DIR/.env"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("The existing .venv uses Python older than 3.11; remove .venv and rerun deploy.sh.")
PY
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$PROJECT_DIR/requirements.txt"

cd "$PROJECT_DIR"
"$VENV_DIR/bin/python" - <<'PY'
import asyncio
from quest_bot.config import Settings
from quest_bot.database import Database

settings = Settings.from_env()
if not settings.database_dsn.startswith(("postgres://", "postgresql://", "postgresql+asyncpg://")):
    raise SystemExit("DATABASE_URL must be set to a PostgreSQL connection URL for deployment.")

async def validate_database() -> None:
    database = Database(
        settings.database_dsn,
        pool_min_size=settings.database_pool_min_size,
        pool_max_size=settings.database_pool_max_size,
    )
    try:
        await database.initialize()
        await database.seed_superadmins(settings.superadmin_ids)
    finally:
        await database.close()

asyncio.run(validate_database())
print("Configuration is valid and the PostgreSQL schema is ready.")
PY
SERVICE_STOP_TIMEOUT_SECONDS="$("$VENV_DIR/bin/python" -c 'from quest_bot.config import Settings; print(Settings.from_env().service_stop_timeout_seconds)')"

SERVICE_USER="$(id -un)"
SERVICE_GROUP="$(id -gn)"
LOG_DIR="/var/log/quest-bot"
sudo install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0700 "$LOG_DIR"
sudo -u "$SERVICE_USER" touch "$LOG_DIR/log" "$LOG_DIR/log_err"
sudo -u "$SERVICE_USER" chmod 0600 "$LOG_DIR/log" "$LOG_DIR/log_err"
UNIT_TEMP="$(mktemp)"
trap 'rm -f "$UNIT_TEMP"' EXIT
cat >"$UNIT_TEMP" <<EOF
[Unit]
Description=Telegram Quest Bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$PROJECT_DIR
ExecStart=$VENV_DIR/bin/python $PROJECT_DIR/main.py
Restart=always
RestartSec=5
TimeoutStopSec=$SERVICE_STOP_TIMEOUT_SECONDS
KillSignal=SIGTERM
UMask=0077
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=QUEST_BOT_LOG_DIR=$LOG_DIR
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=$LOG_DIR
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

[Install]
WantedBy=multi-user.target
EOF

sudo install -o root -g root -m 0644 "$UNIT_TEMP" "$UNIT_PATH"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT_NAME"
if sudo systemctl is-active --quiet "$UNIT_NAME"; then
    sudo systemctl restart "$UNIT_NAME"
else
    sudo systemctl start "$UNIT_NAME"
fi

sleep 2
if ! sudo systemctl is-active --quiet "$UNIT_NAME"; then
    sudo systemctl --no-pager --full status "$UNIT_NAME" || true
    sudo journalctl -u "$UNIT_NAME" -n 80 --no-pager || true
    fail "The service did not stay active. Check the logs above and run: sudo journalctl -u $UNIT_NAME -f"
fi

printf '\nService deployed and enabled: %s\n' "$UNIT_NAME"
printf 'Status: sudo systemctl status %s\n' "$UNIT_NAME"
printf 'Logs:   sudo journalctl -u %s -f\n' "$UNIT_NAME"
printf 'Stop:   sudo systemctl stop %s\n' "$UNIT_NAME"

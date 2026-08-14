#!/usr/bin/env bash
# Run this ON THE SERVER as root, from inside the cloned repo directory.
set -euo pipefail

APP_DIR="/opt/phantom-vpn-bot"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${BOT_TOKEN:-}" ]; then
  echo "Set BOT_TOKEN env var before running, e.g.:"
  echo "  BOT_TOKEN=xxxx:yyyy ./deploy/install.sh"
  exit 1
fi

apt-get update -y
apt-get install -y python3 python3-venv python3-pip

mkdir -p "$APP_DIR"
rsync -a --exclude '.git' --exclude 'deploy' "$REPO_DIR"/ "$APP_DIR"/

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

cat > "$APP_DIR/.env" <<EOF
BOT_TOKEN=${BOT_TOKEN}
ADMIN_CHAT_ID=${ADMIN_CHAT_ID:-0}
CHANNEL_URL=${CHANNEL_URL:-https://t.me/IceFallVPN}
SUPPORT_URL=${SUPPORT_URL:-https://t.me/IceFallVPNSupport}
STARS_PROVIDER_TOKEN=${STARS_PROVIDER_TOKEN:-}
DB_PATH=${APP_DIR}/vpn_bot.db
EOF
chmod 600 "$APP_DIR/.env"

cp "$REPO_DIR/deploy/coinraterobot.service" /etc/systemd/system/coinraterobot.service
systemctl daemon-reload
systemctl enable coinraterobot
systemctl restart coinraterobot

echo "Done. Check status with: systemctl status coinraterobot"
echo "Logs: journalctl -u coinraterobot -f"

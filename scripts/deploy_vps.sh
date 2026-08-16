#!/usr/bin/env bash
#
# Ubuntu VPS deployment script for UNOITACHI Bot
# Tested on Ubuntu 22.04 / 24.04 (x86_64)
#
# Usage:  sudo bash deploy_vps.sh
#
set -euo pipefail

APP_DIR="/opt/unoitachi-bot"
REPO_URL="${REPO_URL:-https://github.com/Sangamlabs/ecoitachi.git}"

echo "==> [1/6] Updating system packages"
apt-get update -y
apt-get upgrade -y

echo "==> [2/6] Installing base dependencies"
apt-get install -y python3 python3-venv python3-pip git curl

echo "==> [3/6] Installing MongoDB Community 8.0"
if ! command -v mongod >/dev/null 2>&1; then
  curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc | \
    gpg --dearmor -o /usr/share/keyrings/mongodb-server-8.0.gpg
  echo "deb [ arch=amd64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] \
https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/8.0 multiverse" \
    > /etc/apt/sources.list.d/mongodb-org-8.0.list
  apt-get update -y
  apt-get install -y mongodb-org
  systemctl enable --now mongod
fi
systemctl is-active --quiet mongod || systemctl start mongod

echo "==> [4/6] Cloning project into ${APP_DIR}"
if [ ! -d "${APP_DIR}/.git" ]; then
  git clone "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" pull --ff-only
fi

echo "==> [5/6] Creating venv and installing dependencies"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

echo "==> [6/6] Configuring environment and systemd service"
if [ ! -f "${APP_DIR}/.env" ]; then
  cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  echo "!!!  Edit ${APP_DIR}/.env and fill in API_ID, API_HASH, BOT_TOKEN and OWNER_ID."
  echo "!!!  Then run:  sudo systemctl start unoitachi"
  exit 0
fi

chown -R "$(id -un)" "${APP_DIR}" 2>/dev/null || true

sed "s/^User=ubuntu/User=$(id -un)/" \
  "${APP_DIR}/deploy/unoitachi.service" > /etc/systemd/system/unoitachi.service

systemctl daemon-reload
systemctl enable unoitachi
systemctl restart unoitachi

echo ""
echo "==> Done. Status:"
systemctl status unoitachi --no-pager || true
echo "Logs:  journalctl -u unoitachi -f"

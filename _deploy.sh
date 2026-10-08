#!/usr/bin/env bash
set -euo pipefail

# Déploiement sur un hôte Linux (root). Exemple :
#   scp -r jaj-transcode root@host:/tmp/
#   ssh root@host 'bash /tmp/jaj-transcode/_deploy.sh'
#
# Ou depuis un clone git déjà présent :
#   INSTALL_DIR=/opt/jaj-transcode bash _deploy.sh

INSTALL_DIR="${INSTALL_DIR:-/opt/jaj-transcode}"
SERVICE_NAME="jaj-transcode.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_SYSTEM="/etc/jaj-transcode.env"

echo "==> Installation dans ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.git' \
  --exclude 'data' \
  --exclude '.env' \
  "${SCRIPT_DIR}/" "${INSTALL_DIR}/"

if [[ ! -f "${ENV_SYSTEM}" ]]; then
  echo "==> Création de ${ENV_SYSTEM} depuis .env.example (à éditer)"
  install -m 600 "${INSTALL_DIR}/.env.example" "${ENV_SYSTEM}"
fi

echo "==> Environnement Python"
if [[ ! -d "${INSTALL_DIR}/.venv" ]]; then
  python3 -m venv "${INSTALL_DIR}/.venv"
fi
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

mkdir -p "${INSTALL_DIR}/data"

echo "==> systemd"
install -m 644 "${INSTALL_DIR}/deploy/jaj-transcode.service" "/etc/systemd/system/${SERVICE_NAME}"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"
systemctl --no-pager status "${SERVICE_NAME}" || true

PORT="$(grep -E '^JAJ_PORT=' "${ENV_SYSTEM}" 2>/dev/null | cut -d= -f2- || true)"
PORT="${PORT:-8791}"
echo "==> Terminé. UI: http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT}/"
echo "    Config: ${ENV_SYSTEM}"

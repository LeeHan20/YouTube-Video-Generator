#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$(pwd)}"
APP_SERVICE="${APP_SERVICE:-youtube-video-generator}"
WORKER_SERVICE="${WORKER_SERVICE:-youtube-video-generator-worker}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_SYSTEMD="${INSTALL_SYSTEMD:-0}"
INSTALL_WORKER="${INSTALL_WORKER:-0}"

cd "$APP_DIR"

missing=0
for file in .env service-account.json client_secret.json; do
  if [[ ! -f "$file" ]]; then
    echo "Missing required server-only file: $APP_DIR/$file"
    missing=1
  fi
done

if [[ "$missing" -eq 1 ]]; then
  exit 1
fi

if [[ ! -d .venv ]]; then
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [[ "$INSTALL_SYSTEMD" == "1" ]]; then
  sudo cp deploy/systemd/youtube-video-generator.service "/etc/systemd/system/${APP_SERVICE}.service"

  if [[ "$INSTALL_WORKER" == "1" ]]; then
    sudo cp deploy/systemd/youtube-video-generator-worker.service "/etc/systemd/system/${WORKER_SERVICE}.service"
  fi

  sudo systemctl daemon-reload
  sudo systemctl enable "${APP_SERVICE}.service"
fi

sudo systemctl restart "${APP_SERVICE}.service"

if systemctl list-unit-files "${WORKER_SERVICE}.service" >/dev/null 2>&1; then
  sudo systemctl restart "${WORKER_SERVICE}.service"
fi

sudo systemctl --no-pager --full status "${APP_SERVICE}.service" | sed -n '1,14p'

#!/usr/bin/env bash
# Install deeptraversal on the AmneziaWG server itself.
#   sudo DEST=/opt/deeptraversal ./deploy/install.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${DEST:-/opt/deeptraversal}"
PYTHON="${PYTHON:-python3}"

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root (sudo)" >&2
  exit 1
fi

command -v "$PYTHON" >/dev/null || { echo "python3 not found" >&2; exit 1; }
if ! command -v awg >/dev/null; then
  echo "warning: 'awg' not in PATH — install AmneziaWG first, or set BACKEND=mock" >&2
fi

install -d -m 0755 "$DEST"
tar -C "$SRC" -cf - --exclude=.venv --exclude=.git --exclude=.env --exclude='*.db' . \
  | tar -C "$DEST" -xf -

"$PYTHON" -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"

if [ ! -f "$DEST/.env" ]; then
  cp "$DEST/.env.example" "$DEST/.env"
  echo "created $DEST/.env from the example"
fi
chmod 600 "$DEST/.env"

install -m 0644 "$DEST/deploy/awgbot.service" /etc/systemd/system/awgbot.service
systemctl daemon-reload

cat <<EOF

installed to $DEST

next steps:
  1. edit $DEST/.env  (BOT_TOKEN, ADMIN_IDS, ENDPOINT_HOST, AWG_CONF)
  2. check:  $DEST/.venv/bin/python -m awgbot --env $DEST/.env --check
  3. start:  systemctl enable --now awgbot && journalctl -u awgbot -f
EOF

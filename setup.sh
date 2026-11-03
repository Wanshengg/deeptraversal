#!/usr/bin/env bash
# deeptraversal — one-command installer for a server that already runs AmneziaWG.
#
#   curl -fsSL https://raw.githubusercontent.com/Wanshengg/deeptraversal/main/setup.sh | sudo bash
#   # или из клонированного репозитория:
#   sudo ./setup.sh
#
# Спрашивает только токен бота и ваш Telegram ID. Всё остальное (контейнер Amnezia или
# нативная установка, конфиг, интерфейс, порт, подсеть, public key, clientsTable)
# определяется автоматически.
set -euo pipefail

REPO="${REPO:-https://github.com/Wanshengg/deeptraversal.git}"
REF="${REF:-main}"
DEST="${DEST:-/opt/deeptraversal}"
SERVICE="${SERVICE:-awgbot}"
TOKEN=""
ADMIN=""
HOST=""
PORT=""
DRY_RUN=0
NO_START=0
SOURCE=""

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
deeptraversal installer

  --token TOKEN    токен бота от @BotFather
  --admin ID       ваш Telegram ID (можно несколько через запятую)
  --host HOST      публичный адрес сервера (по умолчанию определится сам)
  --port PORT      порт AmneziaWG (по умолчанию из конфига)
  --dest DIR       куда ставить (по умолчанию /opt/deeptraversal)
  --service NAME   имя systemd-юнита (по умолчанию awgbot)
  --repo URL       другой git-репозиторий
  --ref BRANCH     другая ветка/тег
  --dry-run        только показать, что найдено и что будет сделано
  --no-start       установить, но не запускать сервис
  -h, --help       эта справка
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --token) TOKEN="${2:-}"; shift 2 ;;
    --admin) ADMIN="${2:-}"; shift 2 ;;
    --host) HOST="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --dest) DEST="${2:-}"; shift 2 ;;
    --service) SERVICE="${2:-}"; shift 2 ;;
    --repo) REPO="${2:-}"; shift 2 ;;
    --ref) REF="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --no-start) NO_START=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "неизвестный аргумент: $1 (см. --help)" ;;
  esac
done

# --- preconditions -----------------------------------------------------------
if [ "$DRY_RUN" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
  die "нужен root: sudo ./setup.sh (или curl ... | sudo bash)"
fi
command -v python3 >/dev/null || die "нет python3 — поставьте его (apt install python3)"
python3 - <<'PY' || die "нужен python3 >= 3.8"
import sys
sys.exit(0 if sys.version_info >= (3, 8) else 1)
PY
command -v git >/dev/null || die "нет git — поставьте его (apt install git)"

# --- source code -------------------------------------------------------------
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SELF_DIR/awgbot/__init__.py" ]; then
  SOURCE="$SELF_DIR"
  log "ставлю из локального каталога $SOURCE"
else
  SOURCE=""
  log "репозитория рядом нет — клонирую $REPO ($REF) в $DEST"
fi

install_pkg() {
  if command -v apt-get >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" >/dev/null
  elif command -v dnf >/dev/null; then dnf install -y -q "$@" >/dev/null
  elif command -v yum >/dev/null; then yum install -y -q "$@" >/dev/null
  elif command -v apk >/dev/null; then apk add --no-cache "$@" >/dev/null
  else return 1
  fi
}

if [ "$DRY_RUN" -eq 0 ]; then
  mkdir -p "$DEST"
  if [ -n "$SOURCE" ] && [ "$(cd "$SOURCE" && pwd)" != "$(cd "$DEST" && pwd)" ]; then
    tar -C "$SOURCE" -cf - --exclude=.venv --exclude=.git --exclude=.env . | tar -C "$DEST" -xf -
  elif [ -z "$SOURCE" ]; then
    if [ -d "$DEST/.git" ]; then
      git -C "$DEST" fetch -q origin && git -C "$DEST" reset -q --hard "origin/$REF"
    else
      git clone -q --branch "$REF" "$REPO" "$DEST"
    fi
  fi
fi

PY="$DEST/.venv/bin/python"

# --- detection (по возможности — до установки venv, на системном python) -----
DETECT_CMD=(python3 -m awgbot.detect)
if [ "$DRY_RUN" -eq 0 ]; then
  if ! "$PY" -c "import awgbot" >/dev/null 2>&1 || [ ! -x "$PY" ]; then
    log "создаю виртуальное окружение"
    rm -rf "$DEST/.venv"
    if ! python3 -m venv "$DEST/.venv" >/dev/null 2>&1 || [ ! -x "$PY" ]; then
      log "ставлю python3-venv"
      install_pkg python3-venv || install_pkg python3.12-venv || install_pkg python3-pip \
        || warn "не удалось поставить venv-пакет автоматически"
      rm -rf "$DEST/.venv"
      python3 -m venv "$DEST/.venv" || die "не вышло создать venv в $DEST/.venv"
    fi
  fi
  log "ставлю зависимости"
  "$PY" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  "$PY" -m pip install -q -r "$DEST/requirements.txt"
  DETECT_CMD=("$PY" -m awgbot.detect)
fi

log "ищу установленную Amnezia (контейнер или нативный awg)"
DETECT_ARGS=()
if [ -n "$HOST" ]; then DETECT_ARGS+=(--host "$HOST"); fi
if [ -n "$PORT" ]; then DETECT_ARGS+=(--port "$PORT"); fi
DETECT_CWD="$DEST"
[ -d "$DETECT_CWD" ] || DETECT_CWD="$SELF_DIR"
if ! DETECTED="$(cd "$DETECT_CWD" && PYTHONPATH="$DEST:${PYTHONPATH:-}" "${DETECT_CMD[@]}" "${DETECT_ARGS[@]}")"; then
  die "не удалось определить установку Amnezia. Проверьте, что AmneziaWG поднят (awg show / docker ps), или задайте параметры вручную в .env"
fi
printf '%s\n' "$DETECTED"

if [ "$DRY_RUN" -eq 1 ]; then
  log "dry-run: изменения не вносились"
  exit 0
fi

# --- credentials -------------------------------------------------------------
if [ -z "$TOKEN" ]; then
  [ -t 0 ] || die "нужен --token (или запустите интерактивно)"
  read -rp "Токен бота от @BotFather: " TOKEN
fi
if [ -z "$ADMIN" ]; then
  [ -t 0 ] || die "нужен --admin (ваш Telegram ID)"
  read -rp "Ваш Telegram ID (число, можно несколько через запятую): " ADMIN
fi
printf '%s' "$TOKEN" | grep -Eq '^[0-9]+:[A-Za-z0-9_-]{30,}$' \
  || die "токен не похож на токен Bot API (ожидается 123456:ABC-DEF...)"
printf '%s' "$ADMIN" | grep -Eq '^[0-9]+([,[:space:]]*[0-9]+)*$' \
  || die "ADMIN_IDS должен быть числом или списком чисел через запятую"

# --- .env --------------------------------------------------------------------
log "пишу $DEST/.env"
ENV_ARGS=(--write-env "$DEST/.env" --dest "$DEST" --token "$TOKEN" --admin "$ADMIN")
if [ -n "$HOST" ]; then ENV_ARGS+=(--host "$HOST"); fi
if [ -n "$PORT" ]; then ENV_ARGS+=(--port "$PORT"); fi
"$PY" -m awgbot.detect "${ENV_ARGS[@]}" >/dev/null
chmod 600 "$DEST/.env"

# --- systemd -----------------------------------------------------------------
log "ставлю systemd-юнит $SERVICE"
install -m 644 "$DEST/deploy/awgbot.service" "/etc/systemd/system/$SERVICE.service"
sed -i "s#/opt/deeptraversal#$DEST#g" "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload

log "проверяю настройки"
"$PY" -m awgbot --env "$DEST/.env" --check || die "--check не прошёл, смотрите вывод выше"

if [ "$NO_START" -eq 1 ]; then
  log "готово. Запуск: systemctl enable --now $SERVICE"
  exit 0
fi

log "запускаю $SERVICE"
systemctl enable --now "$SERVICE" >/dev/null 2>&1 || systemctl restart "$SERVICE"
sleep 4
if ! systemctl is-active --quiet "$SERVICE"; then
  journalctl -u "$SERVICE" -n 20 --no-pager || true
  die "сервис не поднялся"
fi

USERNAME="$(journalctl -u "$SERVICE" --since '-1 min' --no-pager 2>/dev/null \
  | sed -n 's/.*connected as @\([A-Za-z0-9_]*\).*/\1/p' | tail -1)"

BOT_LABEL="см. логи (journalctl)"
if [ -n "$USERNAME" ]; then BOT_LABEL="@$USERNAME"; fi

cat <<EOF

$(printf '\033[1;32m✓\033[0m') deeptraversal установлен и запущен
  каталог:   $DEST
  сервис:    systemctl status $SERVICE   (логи: journalctl -u $SERVICE -f)
  бот:       $BOT_LABEL
  ключи:     $DEST/awgbot.db (0600) + $DEST/export/

Дальше в Телеграме:
  1. откройте бота $BOT_LABEL и нажмите /start
  2. «Получить ключ» → имя устройства → приходит ссылка vpn:// и файл .conf
  3. чужим людям бот отвечает только после вашего «Выдать»

Полезное:
  $PY -m awgbot --env $DEST/.env --list            # актуальные ключи
  $PY -m awgbot --env $DEST/.env --prune           # подчистить удалённое мимо бота
  $PY -m awgbot --env $DEST/.env --sync-clients-table
EOF

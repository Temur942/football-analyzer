#!/usr/bin/env bash
# =============================================================================
# run_bot_local.sh — запуск бота на машине разработчика с базой на сервере.
#
# Зачем: с сервера в РФ Telegram недоступен. Если на машине разработчика есть
# доступ к Telegram (например, включён VPN), бот можно поднять локально, а базу
# брать с сервера через SSH-туннель — данные те же, ничего копировать не надо.
#
# Секреты не печатаются и не сохраняются в файлы: токен и DSN читаются с
# сервера на время запуска.
#
# Использование:
#   bash scripts/run_bot_local.sh          # запуск
#   bash scripts/run_bot_local.sh --check  # только проверить доступ к Telegram
# =============================================================================
set -euo pipefail

KEY="${KEY:-$HOME/.ssh/football_analyzer_deploy}"
SERVER="${SERVER:-root@168.113.176.166}"
LOCAL_PORT="${LOCAL_PORT:-15432}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

telegram_ok() {
  curl -s -o /dev/null -m 8 https://api.telegram.org/
}

if ! telegram_ok && [ "${WAIT_FOR_TELEGRAM:-0}" = "1" ]; then
  echo "Telegram пока недоступен. Жду включения VPN (до 30 минут)..."
  for _ in $(seq 1 360); do
    sleep 5
    if telegram_ok; then
      break
    fi
  done
fi

if ! telegram_ok; then
  echo "ОШИБКА: Telegram недоступен с этой машины." >&2
  echo "Включите VPN так, чтобы он покрывал весь трафик, и запустите:" >&2
  echo "  WAIT_FOR_TELEGRAM=1 bash scripts/run_bot_local.sh" >&2
  exit 1
fi
echo "Telegram доступен"

if [ "${1:-}" = "--check" ]; then
  echo "Проверка пройдена, запуск не запрошен."
  exit 0
fi

# --- данные с сервера (в вывод не попадают) ---------------------------------
# Одно соединение вместо пяти: пять подряд падают целиком, если SSH моргнёт
# (проверено 08.11.2026 — временный сброс соединения останавливал запуск).
env_dump=""
for attempt in 1 2 3 4 5; do
  env_dump=$(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$SERVER" \
    'grep -E "^(DATABASE_URL|BOT_TOKEN|BOT_ADMIN_IDS|CLOSED_BETA|CHANNEL_ID)=" /etc/football-analyzer/stage.env' 2>/dev/null) || true
  if [ -n "$env_dump" ]; then
    break
  fi
  echo "SSH не ответил, повтор через 5 секунд (попытка $attempt из 5)" >&2
  sleep 5
done

read_value() {
  printf '%s\n' "$env_dump" | grep -m1 "^$1=" | cut -d= -f2-
}

DB_URL=$(read_value DATABASE_URL)
BOT_TOKEN=$(read_value BOT_TOKEN)
BOT_ADMIN_IDS=$(read_value BOT_ADMIN_IDS)
CLOSED_BETA=$(read_value CLOSED_BETA)
CHANNEL_ID=$(read_value CHANNEL_ID)

if [ -z "$BOT_TOKEN" ]; then
  echo "ОШИБКА: BOT_TOKEN пуст в env-файле сервера" >&2
  exit 1
fi

# Пробрасываем порт базы на локальную машину и запускаем бота под присмотром.
# pkill по шаблону здесь опасен: он может совпасть с командной строкой того,
# кто запустил скрипт, и убить супервизор. Поэтому туннель живёт под своим PID.
export DATABASE_URL="${DB_URL/@127.0.0.1:5432/@127.0.0.1:${LOCAL_PORT}}"
export BOT_TOKEN
export BOT_ADMIN_IDS="${BOT_ADMIN_IDS:-}"
export CLOSED_BETA="${CLOSED_BETA:-true}"
export CHANNEL_ID="${CHANNEL_ID:-}"
export APP_ENV=local
export DELIVERY_DRY_RUN=true

cd "$ROOT"

start_tunnel() {
  ssh -i "$KEY" -N \
      -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=15 \
      -o ServerAliveCountMax=3 \
      -L "${LOCAL_PORT}:127.0.0.1:5432" "$SERVER" &
  TUNNEL_PID=$!
  for _ in $(seq 1 20); do
    if (echo > "/dev/tcp/127.0.0.1/${LOCAL_PORT}") >/dev/null 2>&1; then
      echo "туннель к базе готов (порт ${LOCAL_PORT})"
      return 0
    fi
    sleep 1
  done
  echo "ВНИМАНИЕ: туннель не поднялся за 20 секунд" >&2
  return 1
}

trap 'kill "$TUNNEL_PID" 2>/dev/null || true' EXIT

start_tunnel || true

# Сторож туннеля: SSH-соединение к базе умирает тихо, и бот узнаёт об этом
# только когда перестаёт писать пульс. Проверяем порт каждые 20 секунд.
watch_tunnel() {
  while true; do
    sleep 20
    if ! (echo > "/dev/tcp/127.0.0.1/${LOCAL_PORT}") >/dev/null 2>&1; then
      echo "туннель к базе умер — поднимаю заново" >&2
      kill "$TUNNEL_PID" 2>/dev/null || true
      start_tunnel || true
    fi
  done
}
watch_tunnel &

# set -e сверху: без `|| true` падение бота убило бы сам супервизор,
# а падать он должен ровно для того, чтобы поднять бота заново.
while true; do
  code=0
  .venv/bin/python -m football_analyzer.main || code=$?
  echo "бот завершился (код $code), перезапуск через 10 секунд" >&2
  sleep 10
done

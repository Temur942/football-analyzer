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
DB_URL=$(ssh -i "$KEY" -o BatchMode=yes "$SERVER" 'grep "^DATABASE_URL=" /etc/football-analyzer/stage.env | cut -d= -f2-')
BOT_TOKEN=$(ssh -i "$KEY" -o BatchMode=yes "$SERVER" 'grep "^BOT_TOKEN=" /etc/football-analyzer/stage.env | cut -d= -f2-')
BOT_ADMIN_IDS=$(ssh -i "$KEY" -o BatchMode=yes "$SERVER" 'grep "^BOT_ADMIN_IDS=" /etc/football-analyzer/stage.env | cut -d= -f2-')
CLOSED_BETA=$(ssh -i "$KEY" -o BatchMode=yes "$SERVER" 'grep "^CLOSED_BETA=" /etc/football-analyzer/stage.env | cut -d= -f2-')
CHANNEL_ID=$(ssh -i "$KEY" -o BatchMode=yes "$SERVER" 'grep "^CHANNEL_ID=" /etc/football-analyzer/stage.env | cut -d= -f2-')

if [ -z "$BOT_TOKEN" ]; then
  echo "ОШИБКА: BOT_TOKEN пуст в env-файле сервера" >&2
  exit 1
fi

# Пробрасываем порт базы на локальную машину.
ssh -i "$KEY" -N \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=3 \
    -L "${LOCAL_PORT}:127.0.0.1:5432" "$SERVER" &
TUNNEL_PID=$!
trap 'kill "$TUNNEL_PID" 2>/dev/null || true' EXIT

# Ждём, пока туннель реально начнёт принимать соединения: 2 секунды сна
# оказалось мало — бот падал на подключении к базе.
for attempt in $(seq 1 20); do
  if (echo > "/dev/tcp/127.0.0.1/${LOCAL_PORT}") >/dev/null 2>&1; then
    echo "туннель к базе готов (порт ${LOCAL_PORT})"
    break
  fi
  sleep 1
done

export DATABASE_URL="${DB_URL/@127.0.0.1:5432/@127.0.0.1:${LOCAL_PORT}}"
export BOT_TOKEN
export BOT_ADMIN_IDS="${BOT_ADMIN_IDS:-}"
export CLOSED_BETA="${CLOSED_BETA:-true}"
export CHANNEL_ID="${CHANNEL_ID:-}"
export APP_ENV=local
export DELIVERY_DRY_RUN=true

cd "$ROOT"
exec .venv/bin/python -m football_analyzer.main

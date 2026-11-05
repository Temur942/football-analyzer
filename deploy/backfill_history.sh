#!/usr/bin/env bash
# =============================================================================
# backfill_history.sh — разовая выгрузка истории для модели.
#
# Что делает:
#   1. по каждому сезону ищет прошедшие матчи (14 лиг; кубки пропускаются —
#      у них календарь диапазоном, для них нужен отдельный перебор дат);
#   2. затем догружает статистику (углы, ЖК, голы по таймам) по всем матчам.
#
# Возобновляемость: матч считается обработанным только после записи статистики
# (stats_fetched_at). Повторный запуск продолжит с места обрыва.
#
# Деньги: не тратятся. Источник бесплатный. Но идёт долго (часы) и грузит
# чужой API — пауза между запросами обязательна.
#
# Запуск на сервере:
#   nohup bash deploy/backfill_history.sh > /var/log/football-backfill.log 2>&1 &
# Прогресс:
#   tail -f /var/log/football-backfill.log
# =============================================================================
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

APP_ENV_FILE=${APP_ENV_FILE:-/etc/football-analyzer/stage.env}
DBURL=$(grep '^DATABASE_URL=' "$APP_ENV_FILE" | cut -d= -f2-)
if [ -z "$DBURL" ]; then
  echo "ОШИБКА: не нашёл DATABASE_URL в $APP_ENV_FILE" >&2
  exit 1
fi

PY=.venv/bin/python
SEASONS=${SEASONS:-"2023-2024 2024-2025 2025-2026 2026-2027"}
PAUSE_DISCOVER=${PAUSE_DISCOVER:-0.3}
PAUSE_STATS=${PAUSE_STATS:-0.4}

run() { sudo -u football env DATABASE_URL="$DBURL" "$PY" "$@"; }

echo "=== СТАРТ $(date -u '+%Y-%m-%d %H:%M UTC') ==="
for season in $SEASONS; do
  echo "--- поиск матчей: $season ---"
  run -m football_analyzer.cli discover-history --season "$season" --pause "$PAUSE_DISCOVER" || true
done

echo "--- статистика матчей $(date -u '+%H:%M UTC') ---"
run -m football_analyzer.cli sync-stats --limit 100000 --pause "$PAUSE_STATS" || true

echo "=== КОНЕЦ $(date -u '+%Y-%m-%d %H:%M UTC') ==="

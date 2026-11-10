#!/usr/bin/env bash
# =============================================================================
# refresh_content.sh — ежедневная генерация контента.
#
# Что делает:
#   1. делает превью матчей на сегодня (2 группы) и на завтра (1 группа);
#   2. пересобирает страницы блога;
#   3. ставит новые тексты в очередь канала.
#
# Запускается таймером раз в сутки. Если матчей нет — просто ничего не создаёт.
# =============================================================================
set -euo pipefail

APP_DIR=/var/www/football-analyzer-stage
cd "$APP_DIR"

export DATABASE_URL="$(grep '^DATABASE_URL=' /etc/football-analyzer/stage.env | cut -d= -f2-)"
PY="$APP_DIR/.venv/bin/python"
TODAY=$(TZ=Europe/Moscow date +%F)
TOMORROW=$(TZ=Europe/Moscow date -v+1d +%F 2>/dev/null || TZ=Europe/Moscow date -d tomorrow +%F)

echo "=== превью на $TODAY ==="
$PY scripts/make_previews.py --date "$TODAY" --groups 2 --per-group 4 \
    --slug-prefix "day-$(echo "$TODAY" | tr -d '-')" 2>&1 | tail -3 || true

echo "=== превью на $TOMORROW ==="
$PY scripts/make_previews.py --date "$TOMORROW" --groups 1 --per-group 5 \
    --slug-prefix "day-$(echo "$TOMORROW" | tr -d '-')" 2>&1 | tail -2 || true

echo "=== страницы блога ==="
$PY scripts/build_blog.py 2>&1 | tail -1

echo "=== очередь канала ==="
$PY -m football_analyzer.cli seed-blog --dir docs/blog 2>&1 | tail -3

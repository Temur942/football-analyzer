#!/usr/bin/env bash
# =============================================================================
# enable-accuracy-timer.sh — включить ежедневную пересборку accuracy.json
# на уже работающем сервере, без повторного провижининга.
#
# Запуск на сервере под root:
#   bash /var/www/football-analyzer-stage/deploy/enable-accuracy-timer.sh
#   bash /var/www/football-analyzer-stage/deploy/enable-accuracy-timer.sh prod
#
# Что делает:
#   1. ставит football-analyzer-accuracy.service и .timer (суффикс -prod для прода)
#   2. прописывает юнитам правильные пути и env-файл
#   3. включает таймер и сразу запускает одну пересборку
#   4. показывает, когда следующая и что записалось в файл
#
# Идемпотентен: повторный запуск ничего не ломает.
# =============================================================================
set -euo pipefail

ENV_NAME="${1:-stage}"
APP_DIR="/var/www/football-analyzer-stage"
ENV_FILE="/etc/football-analyzer/stage.env"
suffix=""
if [ "$ENV_NAME" = "prod" ]; then
  suffix="-prod"
  APP_DIR="/var/www/football-analyzer-prod"
  ENV_FILE="/etc/football-analyzer/prod.env"
fi

[ "$(id -u)" -eq 0 ] || { echo "ОШИБКА: нужен root" >&2; exit 1; }
[ -d "$APP_DIR" ] || { echo "ОШИБКА: нет каталога $APP_DIR" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "ОШИБКА: нет env-файла $ENV_FILE" >&2; exit 1; }

install_unit() {
  local src="$1" name="$2"
  sed -e "s#/var/www/football-analyzer-stage#${APP_DIR}#g" \
      -e "s#/etc/football-analyzer/stage.env#${ENV_FILE}#g" \
      -e "s#^Description=#Description=[${ENV_NAME}] #" \
      "$src" > "/etc/systemd/system/${name}"
  chmod 644 "/etc/systemd/system/${name}"
  echo "установлен юнит ${name}"
}

install_unit "$APP_DIR/deploy/football-analyzer-accuracy.service" "football-analyzer-accuracy${suffix}.service"
install_unit "$APP_DIR/deploy/football-analyzer-accuracy.timer" "football-analyzer-accuracy${suffix}.timer"

# Таймер должен запускать свой же сервис, иначе прод запустит стейдж.
sed -i "s#football-analyzer-accuracy.service#football-analyzer-accuracy${suffix}.service#g" \
  "/etc/systemd/system/football-analyzer-accuracy${suffix}.timer"

systemctl daemon-reload
systemctl enable --now "football-analyzer-accuracy${suffix}.timer" >/dev/null
echo "таймер включён: football-analyzer-accuracy${suffix}.timer"

echo "=== разовая пересборка прямо сейчас ==="
systemctl start "football-analyzer-accuracy${suffix}.service"
sleep 2
systemctl is-active "football-analyzer-accuracy${suffix}.service" || true
journalctl -u "football-analyzer-accuracy${suffix}" -n 15 --no-pager || true

echo "=== следующая пересборка ==="
systemctl list-timers "football-analyzer-accuracy${suffix}.timer" --no-pager || true

echo "=== что записалось в файл ==="
LANDING="$APP_DIR/docs/landing/accuracy.json"
if [ -f "$LANDING" ]; then
  ls -l "$LANDING"
  head -c 300 "$LANDING"; echo
else
  echo "ВНИМАНИЕ: файл $LANDING не появился — смотрите лог выше" >&2
fi

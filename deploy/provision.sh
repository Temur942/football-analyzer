#!/usr/bin/env bash
# =============================================================================
# provision.sh — подготовка нового VDS под football-analyzer (Ubuntu 24.04).
#
# Идемпотентен: повторный запуск ничего не ломает и ничего не удаляет.
# Запуск под root:  bash provision.sh
#
# Что делает:
#   1. системного пользователя football (не работаем под root)
#   2. каталоги /var/www и /etc/football-analyzer
#   3. настройку PostgreSQL под 2 ГБ RAM и только localhost
#   4. роль и базу football, пароль генерируется один раз
#   5. файл окружения /etc/football-analyzer/stage.env (640 root:football)
#   6. firewall: наружу только SSH
#   7. клон репозитория и venv с зависимостями
#   8. unit для systemd (устанавливается, но НЕ включается: нужен BOT_TOKEN)
#
# Чего НЕ делает: не запускает бота, не открывает порты наружу, не трогает
# секреты (кроме сгенерированного пароля БД).
# =============================================================================
set -euo pipefail

# Окружение передаётся первым аргументом: stage (по умолчанию) или prod.
# Стейджу и проду — разные каталоги, разные базы, разные юниты systemd.
APP_ENV_NAME="${1:-stage}"
case "$APP_ENV_NAME" in
  stage)
    APP_DIR=/var/www/football-analyzer-stage
    DB_NAME=football
    DB_USER=football
    ;;
  prod)
    APP_DIR=/var/www/football-analyzer-prod
    DB_NAME=football_prod
    # Отдельная роль: иначе развёртывание прода меняет пароль роли стейджа
    # и ломает доступ к уже работающей базе (проверено на живом сервере).
    DB_USER=football_prod
    ;;
  *)
    echo "ОШИБКА: окружение должно быть stage или prod" >&2
    exit 1
    ;;
esac

APP_USER=football
REPO=https://github.com/Temur942/football-analyzer.git
CONF_DIR=/etc/football-analyzer
ENV_FILE="$CONF_DIR/$APP_ENV_NAME.env"

log() { printf '\n=== %s ===\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "ОШИБКА: скрипт запускается под root" >&2; exit 1; }

# --------------------------------------------------------------------------- #
log "1/10 системный пользователь"
if id -u "$APP_USER" >/dev/null 2>&1; then
  echo "пользователь $APP_USER уже есть"
else
  useradd -m -s /bin/bash "$APP_USER"
  echo "создан пользователь $APP_USER"
fi

# --------------------------------------------------------------------------- #
log "2/10 каталоги"
install -d -o "$APP_USER" -g "$APP_USER" /var/www
install -d -m 750 -o root -g "$APP_USER" "$CONF_DIR"
echo "готово: /var/www, $CONF_DIR"

# --------------------------------------------------------------------------- #
log "3/10 PostgreSQL: настройка под 2 ГБ RAM"
PG_VER=$(find /etc/postgresql -maxdepth 1 -mindepth 1 -type d -printf '%f\n' | sort -V | tail -1)
PG_CONF_DIR="/etc/postgresql/$PG_VER/main"
if [ ! -d "$PG_CONF_DIR" ]; then
  echo "ОШИБКА: не найден конфиг PostgreSQL в $PG_CONF_DIR" >&2
  exit 1
fi
install -d "$PG_CONF_DIR/conf.d"
cat > "$PG_CONF_DIR/conf.d/10-football.conf" <<'EOF'
# football-analyzer: сервер на 2 ГБ RAM, база только для локального бота.
shared_buffers = 256MB
effective_cache_size = 768MB
work_mem = 8MB
maintenance_work_mem = 64MB
max_connections = 40
listen_addresses = 'localhost'
EOF
systemctl restart postgresql
systemctl is-active --quiet postgresql && echo "postgresql перезапущен и активен"

# --------------------------------------------------------------------------- #
log "4/10 роль и база"
# Пароль генерируется один раз и живёт только в файле окружения.
if grep -q '^DB_PASSWORD=' "$ENV_FILE" 2>/dev/null; then
  DB_PASSWORD=$(sed -n 's/^DB_PASSWORD=//p' "$ENV_FILE" | head -1)
  echo "пароль БД взят из существующего $ENV_FILE"
else
  DB_PASSWORD=$(openssl rand -hex 24)
  echo "сгенерирован новый пароль БД"
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 -q <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
    CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD';
  ELSE
    ALTER ROLE $DB_USER WITH LOGIN PASSWORD '$DB_PASSWORD';
  END IF;
END
\$\$;
SQL

if sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
  echo "база $DB_NAME уже есть"
else
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
  echo "создана база $DB_NAME"
fi

# --------------------------------------------------------------------------- #
log "5/10 файл окружения"
if [ -f "$ENV_FILE" ]; then
  echo "$ENV_FILE уже существует — не перезаписываю (проверьте вручную)"
else
  cat > "$ENV_FILE" <<EOF
# Секреты стейджа football-analyzer. Права 640 root:$APP_USER, в git не попадает.
APP_ENV=$APP_ENV_NAME
DB_PASSWORD=$DB_PASSWORD
DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@127.0.0.1:5432/$DB_NAME?sslmode=disable
# Токен от @BotFather: заполнить перед запуском бота.
BOT_TOKEN=
# Погода: Open-Meteo, ключ не нужен.
WEATHER_API_URL=https://api.open-meteo.com/v1/forecast
HTTP_TIMEOUT_SECONDS=20
LOG_LEVEL=INFO
FORECAST_LEAD_HOURS=5
# Кэш soccerdata внутри проекта: домашний каталог сервису закрыт.
SOCCERDATA_DIR=$APP_DIR/var/soccerdata
EOF
  chown root:"$APP_USER" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  echo "создан $ENV_FILE (пароль БД сгенерирован, в вывод не печатается)"
fi

# --------------------------------------------------------------------------- #
log "6/10 firewall"
# На образах FirstVDS ufw уже открыт широко: 80/443, 21, 25/465/587, 53, 110,
# 143/993/995 и панель ispmanager. Нам из этого не нужно НИЧЕГО — сервис
# слушает только SSH и локальный PostgreSQL. Открытые почтовые порты — это
# приглашение для спам-ботов и DNS-амплификации.
ufw --force reset >/dev/null 2>&1
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
# Проверка ДО включения: иначе можно остаться без доступа к серверу.
ufw show added | grep -qi openssh || {
  echo "ОШИБКА: правило SSH не добавлено, firewall не включаю" >&2
  exit 1
}
ufw --force enable >/dev/null
ufw status | head -8

# --------------------------------------------------------------------------- #
log "7/10 код и venv"
if [ -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" fetch --quiet origin
  sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard --quiet origin/main
  echo "код обновлён до origin/main"
else
  install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
  sudo -u "$APP_USER" git clone --quiet "$REPO" "$APP_DIR"
  echo "репозиторий склонирован"
fi

install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR/var"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
  echo "venv создан"
fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet -e "$APP_DIR"
echo "зависимости установлены"

# --------------------------------------------------------------------------- #
log "8/10 systemd"
# Имена юнитов: стейдж без суффикса, прод с -prod. Содержимое подставляется
# из шаблонов репозитория: пути, env-файл и ссылки между юнитами.
suffix=""
[ "$APP_ENV_NAME" = "prod" ] && suffix="-prod"

install_unit() {
  local src="$1" name="$2"
  sed -e "s#/var/www/football-analyzer-stage#${APP_DIR}#g" \
      -e "s#/etc/football-analyzer/stage.env#${ENV_FILE}#g" \
      -e "s#^Description=#Description=[${APP_ENV_NAME}] #" \
      "$src" > "/etc/systemd/system/${name}"
  chmod 644 "/etc/systemd/system/${name}"
}

install_unit "$APP_DIR/deploy/football-analyzer-stage.service" "football-analyzer-stage${suffix}.service"
install_unit "$APP_DIR/deploy/football-analyzer-sync.service" "football-analyzer-sync${suffix}.service"
install_unit "$APP_DIR/deploy/football-analyzer-sync.timer" "football-analyzer-sync${suffix}.timer"
install_unit "$APP_DIR/deploy/football-analyzer-deliver.service" "football-analyzer-deliver${suffix}.service"
install_unit "$APP_DIR/deploy/football-analyzer-deliver.timer" "football-analyzer-deliver${suffix}.timer"

# Таймеры должны запускать свои же сервисы (иначе прод запустит стейдж).
for unit in /etc/systemd/system/football-analyzer-*${suffix}.timer; do
  sed -i "s#football-analyzer-sync.service#football-analyzer-sync${suffix}.service#g" "$unit"
  sed -i "s#football-analyzer-deliver.service#football-analyzer-deliver${suffix}.service#g" "$unit"
done

systemctl daemon-reload
# Сбор расписания и рассылка не требуют токена — включаем сразу.
systemctl enable --now "football-analyzer-sync${suffix}.timer" >/dev/null
systemctl enable --now "football-analyzer-deliver${suffix}.timer" >/dev/null
echo "таймеры ${APP_ENV_NAME} включены (расписание, рассылка)"
echo "unit бота установлен (не включён: нужен доступ к Telegram)"

log "9/10 доступ: только по ключу"
# Парольный вход выключаем ПОСЛЕ того, как убедились, что ключ работает
# (скрипт запускается по SSH с ключом — иначе он бы сюда не дошёл).
install -d -m 755 /etc/ssh/sshd_config.d
# Имя с 00- НЕ случайно: sshd берёт ПЕРВОЕ встреченное значение, а образ
# хостера кладёт 40-hosting.conf с PasswordAuthentication yes. Файл 60- был бы
# проигнорирован — проверено на живом сервере 05.11.2026.
rm -f /etc/ssh/sshd_config.d/60-football.conf
cat > /etc/ssh/sshd_config.d/00-football.conf <<'EOF'
# football-analyzer: вход только по ключу. root — только с ключом.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
if sshd -t 2>/tmp/sshd-test.err; then
  systemctl reload ssh 2>/dev/null || systemctl reload sshd
  echo "вход по паролю отключён, конфиг проверен (sshd -t)"; sshd -T | grep -E "^passwordauthentication|^permitrootlogin"
else
  echo "ОШИБКА: sshd -t не принял конфиг, откатываю" >&2
  cat /tmp/sshd-test.err >&2
  rm -f /etc/ssh/sshd_config.d/00-football.conf
  exit 1
fi

# --------------------------------------------------------------------------- #
log "10/10 резервные копии базы"
install -m 755 "$APP_DIR/deploy/football-db-backup.sh" /usr/local/bin/football-db-backup.sh
install_unit "$APP_DIR/deploy/football-db-backup.service" "football-db-backup${suffix}.service"
install_unit "$APP_DIR/deploy/football-db-backup.timer" "football-db-backup${suffix}.timer"
if [ "$APP_ENV_NAME" = "prod" ]; then
  # Прод-база называется иначе: передаём имя и префикс файлов дампа.
  sed -i "/^\[Service\]/a Environment=DB_NAME=${DB_NAME}\nEnvironment=PREFIX=${DB_NAME}" \
    "/etc/systemd/system/football-db-backup${suffix}.service"
fi
sed -i "s#football-db-backup.service#football-db-backup${suffix}.service#g" \
  "/etc/systemd/system/football-db-backup${suffix}.timer"
systemctl daemon-reload
systemctl enable --now "football-db-backup${suffix}.timer" >/dev/null
echo "таймер бэкапа ${APP_ENV_NAME} включён (ежедневно 03:30 UTC)"

log "ГОТОВО"
echo "База: $DB_NAME, пользователь: $DB_USER, host 127.0.0.1"
echo "Окружение: $ENV_FILE"
echo "Код: $APP_DIR"
echo "Проверка: sudo -u $APP_USER env DATABASE_URL=\"\$(grep ^DATABASE_URL= $ENV_FILE | cut -d= -f2-)\" $APP_DIR/.venv/bin/python -m football_analyzer.cli sync-schedule --days 1"

#!/usr/bin/env bash
# =============================================================================
# football-db-backup.sh — ежедневная резервная копия базы.
#
# Формат: pg_dump -Fc (custom) — сжатый и восстанавливается через pg_restore.
# Хранение: 7 дней, старое удаляется.
# Проверка: после дампа файл читается pg_restore --list; пустой или битый
# дамп — ошибка (код 1), а не «наверное, записалось».
#
# Запуск: systemd-таймер football-db-backup.timer (ежедневно в 03:30 UTC).
# Ручной: systemctl start football-db-backup.service
# =============================================================================
set -euo pipefail

BACKUP_DIR=/var/backups/football
DB_NAME=football
KEEP_DAYS=7

# Каталог принадлежит postgres: дамп создаёт он же, поэтому файл ему же
# и читается при восстановлении. Проверено на практике: дамп от root
# PostgreSQL прочитать не может, и бэкап оказывается бесполезен.
install -d -m 700 -o postgres -g postgres "$BACKUP_DIR"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
FILE="$BACKUP_DIR/football-$STAMP.dump"

# Дамп от имени postgres: у роли football может не быть прав на чтение всех схем.
# -f вместо ">" — тогда файл создаёт сам postgres и он же сможет его прочитать.
if ! sudo -u postgres pg_dump -Fc --no-owner --no-privileges -f "$FILE" "$DB_NAME" 2>/tmp/football-backup.err; then
  echo "ОШИБКА: pg_dump не смог снять копию" >&2
  cat /tmp/football-backup.err >&2 || true
  rm -f "$FILE"
  exit 1
fi

# Проверка, что дамп читается. Размер меньше 1 КБ — подозрительно.
if ! sudo -u postgres pg_restore --list "$FILE" >/dev/null 2>&1; then
  echo "ОШИБКА: дамп $FILE не открывается pg_restore" >&2
  exit 1
fi
SIZE=$(stat -c %s "$FILE")
if [ "$SIZE" -lt 1024 ]; then
  echo "ОШИБКА: дамп подозрительно мал ($SIZE байт)" >&2
  exit 1
fi

echo "копия создана: $FILE ($SIZE байт)"

# Ротация: удаляем старше KEEP_DAYS дней.
DELETED=$(find "$BACKUP_DIR" -name 'football-*.dump' -mtime +"$KEEP_DAYS" -print -delete | wc -l)
echo "удалено старых копий: $DELETED"
echo "всего копий: $(find "$BACKUP_DIR" -name 'football-*.dump' | wc -l)"

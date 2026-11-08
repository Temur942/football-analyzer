#!/usr/bin/env bash
# =============================================================================
# setup-landing.sh — привязать домен к лендингу и выпустить HTTPS-сертификат.
#
# Запуск на РФ-сервере под root:
#   bash /root/setup-landing.sh analyzer.ru
#
# Что делает:
#   1. конфиг nginx для домена (статика из docs/landing)
#   2. проверка конфига и перезагрузка
#   3. сертификат Let's Encrypt с редиректом на https
#
# Требует: DNS A-запись домена уже указывает на этот сервер.
# =============================================================================
set -euo pipefail

DOMAIN="${1:?укажите домен без https, например analyzer.ru}"
EMAIL="${EMAIL:-khidrov@yandex.ru}"
APP_DIR=/var/www/football-analyzer-stage
LANDING="$APP_DIR/docs/landing"
SITE=/etc/nginx/sites-available/football-landing

[ "$(id -u)" -eq 0 ] || { echo "ОШИБКА: нужен root" >&2; exit 1; }
[ -f "$LANDING/index.html" ] || { echo "ОШИБКА: нет $LANDING/index.html" >&2; exit 1; }

echo "=== 1/3 проверка DNS ==="
RESOLVED=$(getent hosts "$DOMAIN" | awk '{print $1; exit}' || true)
echo "домен $DOMAIN указывает на: ${RESOLVED:-не найдено}"
if [ -z "$RESOLVED" ]; then
  echo "ВНИМАНИЕ: DNS ещё не обновился. Настройте A-запись и повторите." >&2
  exit 1
fi
OUR_IP=$(curl -s -m 10 https://api.ipify.org || true)
if [ -n "$OUR_IP" ] && [ "$RESOLVED" != "$OUR_IP" ]; then
  echo "ВНИМАНИЕ: домен указывает на $RESOLVED, а сервер — $OUR_IP." >&2
  echo "Проверьте A-запись; продолжаю, но сертификат может не выпуститься." >&2
fi

echo "=== 2/3 конфиг nginx ==="
cat > "$SITE" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    root $LANDING;
    index index.html;

    charset utf-8;
    add_header X-Content-Type-Options nosniff;

    location / {
        try_files \$uri \$uri/ =404;
    }

    # Точность обновляется скриптом — кэш только мешает.
    location = /accuracy.json {
        add_header Cache-Control "no-cache";
    }
}
NGINX
ln -sf "$SITE" /etc/nginx/sites-enabled/football-landing
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
echo "nginx настроен на $DOMAIN"

echo "=== 3/3 сертификат ==="
if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect -m "$EMAIL"; then
  echo "HTTPS готов: https://$DOMAIN"
else
  echo "Сертификат не получен. Обычно причина — DNS ещё не указывает на сервер." >&2
  exit 1
fi

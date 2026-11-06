#!/usr/bin/env bash
# =============================================================================
# setup-client.sh — туннель на основном сервере (РФ) до зарубежного узла.
#
# Задача: отправить через туннель ТОЛЬКО трафик к Telegram, оставив всё
# остальное (ESPN, PyPI, погода) напрямую. Так меньше зависимость от узла и
# меньше трафика.
#
# Данные пользователей при этом остаются на этом сервере: за границу уходит
# только шифрованный трафик к api.telegram.org.
#
# Запуск на сервере в РФ, под root:
#   bash setup-client.sh <IP_УЗЛА> <ПУБЛИЧНЫЙ_КЛЮЧ_УЗЛА>
# Идемпотентен.
# =============================================================================
set -euo pipefail

NODE_IP="${1:-}"
NODE_PUBKEY="${2:-}"
WG_PORT="${WG_PORT:-51820}"
WG_IF="${WG_IF:-wg0}"
KEY_DIR=/etc/wireguard

# Официальные диапазоны Telegram (Bot API живёт внутри них).
TELEGRAM_RANGES="149.154.160.0/20, 91.108.4.0/22, 91.108.8.0/22, 91.108.12.0/22, 91.108.16.0/22, 91.108.56.0/22"

log() { printf '\n=== %s ===\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "ОШИБКА: нужен root" >&2; exit 1; }
[ -n "$NODE_IP" ] && [ -n "$NODE_PUBKEY" ] || {
  echo "Использование: bash setup-client.sh <IP_УЗЛА> <ПУБЛИЧНЫЙ_КЛЮЧ_УЗЛА>" >&2
  exit 1
}

log "1/4 пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -qq wireguard wireguard-tools >/dev/null
echo "wireguard установлен"

log "2/4 ключи клиента"
install -d -m 700 "$KEY_DIR"
if [ ! -f "$KEY_DIR/client.key" ]; then
  umask 077
  wg genkey > "$KEY_DIR/client.key"
  wg pubkey < "$KEY_DIR/client.key" > "$KEY_DIR/client.pub"
  echo "ключи созданы"
else
  echo "ключи уже есть"
fi

log "3/4 конфигурация: через туннель только адреса Telegram"
cat > "$KEY_DIR/$WG_IF.conf" <<EOF
[Interface]
Address = 10.66.0.2/24
PrivateKey = $(cat "$KEY_DIR/client.key")
# Маршруты строятся только для AllowedIPs ниже — остальное идёт напрямую.
Table = auto
MTU = 1380

[Peer]
PublicKey = $NODE_PUBKEY
Endpoint = $NODE_IP:$WG_PORT
AllowedIPs = $TELEGRAM_RANGES
# Держим NAT-сессию на узле живой.
PersistentKeepalive = 25
EOF
chmod 600 "$KEY_DIR/$WG_IF.conf"

systemctl enable --now "wg-quick@$WG_IF" >/dev/null 2>&1 || systemctl restart "wg-quick@$WG_IF"
sleep 2

log "4/4 проверка"
echo "--- маршруты к Telegram (должны идти через $WG_IF) ---"
ip route get 149.154.166.110 | head -2
echo "--- маршрут к google (должен идти напрямую) ---"
ip route get 142.250.74.46 | head -2
echo "--- состояние туннеля ---"
wg show "$WG_IF" | head -6 || true

log "ГОТОВО"
echo "Публичный ключ этого сервера (его нужно добавить на узле):"
cat "$KEY_DIR/client.pub"
echo
echo "Проверка Telegram:"
echo "  curl -s -m 15 -o /dev/null -w '%{http_code}\\n' https://api.telegram.org/"

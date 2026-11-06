#!/usr/bin/env bash
# =============================================================================
# setup-node.sh — зарубежный узел-ретранслятор для Telegram.
#
# Что делает: ставит WireGuard, включает пересылку пакетов и NAT, слушает
# UDP 51820. Из РФ-сервера приходит только трафик к адресам Telegram.
#
# ВАЖНО: узел НИЧЕГО не хранит. Персональные данные остаются на сервере в РФ
# (152-ФЗ, локализация). Здесь только транспорт.
#
# Запуск на зарубежном VPS (Ubuntu 24.04 / Debian 12), под root:
#   bash setup-node.sh
# Повторный запуск с ключом клиента — добавление пира:
#   bash setup-node.sh <ПУБЛИЧНЫЙ_КЛЮЧ_КЛИЕНТА>
#
# Идемпотентен.
# =============================================================================
set -euo pipefail

WG_PORT="${WG_PORT:-51820}"
WG_IF="${WG_IF:-wg0}"
KEY_DIR=/etc/wireguard

log() { printf '\n=== %s ===\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "ОШИБКА: нужен root" >&2; exit 1; }

log "1/5 пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq wireguard wireguard-tools iptables >/dev/null
echo "wireguard установлен"

log "2/5 ключи сервера"
install -d -m 700 "$KEY_DIR"
if [ ! -f "$KEY_DIR/node.key" ]; then
  umask 077
  wg genkey > "$KEY_DIR/node.key"
  wg pubkey < "$KEY_DIR/node.key" > "$KEY_DIR/node.pub"
  echo "ключи созданы"
else
  echo "ключи уже есть"
fi

log "3/5 пересылка пакетов"
cat > /etc/sysctl.d/90-wireguard.conf <<'EOF'
net.ipv4.ip_forward = 1
EOF
sysctl -q --system
echo "ip_forward = $(cat /proc/sys/net/ipv4/ip_forward)"

log "4/5 интерфейс и NAT"
iface=$(ip -4 route show default | awk '{print $5; exit}')
cat > "$KEY_DIR/$WG_IF.conf" <<EOF
[Interface]
Address = 10.66.0.1/24
ListenPort = $WG_PORT
PrivateKey = $(cat "$KEY_DIR/node.key")

# Разрешаем клиенту выходить в интернет через этот узел.
PostUp = iptables -A FORWARD -i $WG_IF -j ACCEPT
PostUp = iptables -A FORWARD -o $WG_IF -j ACCEPT
PostUp = iptables -t nat -A POSTROUTING -o $iface -j MASQUERADE
PostDown = iptables -D FORWARD -i $WG_IF -j ACCEPT || true
PostDown = iptables -D FORWARD -o $WG_IF -j ACCEPT || true
PostDown = iptables -t nat -D POSTROUTING -o $iface -j MASQUERADE || true
EOF
chmod 600 "$KEY_DIR/$WG_IF.conf"

# Пир добавляется, если передан ключ клиента.
if [ -n "${1:-}" ]; then
  if grep -q "$1" "$KEY_DIR/$WG_IF.conf" 2>/dev/null; then
    echo "пир уже добавлен"
  else
    cat >> "$KEY_DIR/$WG_IF.conf" <<EOF

[Peer]
PublicKey = $1
AllowedIPs = 10.66.0.2/32
EOF
    echo "пир добавлен"
  fi
fi

systemctl enable --now "wg-quick@$WG_IF" >/dev/null 2>&1 || systemctl restart "wg-quick@$WG_IF"
echo "интерфейс поднят"

log "5/5 firewall"
if command -v ufw >/dev/null 2>&1; then
  ufw allow "$WG_PORT/udp" >/dev/null 2>&1 || true
  # Всё, кроме SSH и WireGuard, наружу не нужно.
  for port in 80 443 21 25 53 110 143 465 587 993 995; do
    ufw delete allow "$port" >/dev/null 2>&1 || true
    ufw delete allow "$port/tcp" >/dev/null 2>&1 || true
    ufw delete allow "$port/udp" >/dev/null 2>&1 || true
  done
  ufw status | head -8
fi

log "ГОТОВО"
echo "Публичный ключ узла: $(cat "$KEY_DIR/node.pub")"
echo "Адрес для клиента:  <IP-этого-VPS>:$WG_PORT"
echo
echo "Проверка после подключения клиента:"
echo "  wg show"

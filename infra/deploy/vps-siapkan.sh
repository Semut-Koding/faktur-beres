#!/usr/bin/env bash
# Menyiapkan VPS Faktur Beres (Ubuntu Server 26.04, RAM 2 GB). Dijalankan dengan sudo, aman diulang:
#   ssh ubuntu@<vps> 'sudo bash ~/faktur-beres/infra/deploy/vps-siapkan.sh'
# Hanya untuk VPS. Jangan dijalankan di laptop pengembang (CLAUDE.md).
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "galat: jalankan dengan sudo" >&2; exit 1; }
TARGET_USER="${SUDO_USER:-ubuntu}"
SWAP_FILE="/swapfile-fakturberes"
SWAP_SIZE="4G"

echo "== Swap $SWAP_SIZE"
if swapon --show=NAME --noheadings | grep -qx "$SWAP_FILE"; then
  echo "  sudah aktif"
else
  if [[ ! -f "$SWAP_FILE" ]]; then
    fallocate -l "$SWAP_SIZE" "$SWAP_FILE"
    chmod 600 "$SWAP_FILE"
    mkswap "$SWAP_FILE" >/dev/null
  fi
  swapon "$SWAP_FILE"
  grep -q "^$SWAP_FILE " /etc/fstab || echo "$SWAP_FILE none swap sw 0 0" >>/etc/fstab
  echo "  diaktifkan"
fi
printf 'vm.swappiness=20\n' >/etc/sysctl.d/60-fakturberes.conf
sysctl -q -p /etc/sysctl.d/60-fakturberes.conf

echo "== Zona waktu Asia/Jakarta"
timedatectl set-timezone Asia/Jakarta

echo "== Paket"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q podman podman-compose caddy ufw rsync pipx uidmap openssl curl

echo "== Firewall: hanya 22, 80, 443"
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
ufw status | sed 's/^/  /'

echo "== Linger untuk $TARGET_USER (layanan user tetap jalan tanpa sesi login)"
loginctl enable-linger "$TARGET_USER"

echo "Penyiapan VPS selesai."

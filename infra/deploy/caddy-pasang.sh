#!/usr/bin/env bash
# Memasang Caddyfile Faktur Beres di VPS. Dijalankan dengan sudo, aman diulang:
#   ssh ubuntu@<vps> 'sudo bash ~/faktur-beres/infra/deploy/caddy-pasang.sh'
# Sandi basic auth dibaca dari .env (BASIC_AUTH_SANDI), di-hash dengan `caddy hash-password`, dan
# tidak pernah dicetak. Hanya hash yang ditulis ke /etc/caddy/Caddyfile.
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "galat: jalankan dengan sudo" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$HERE/../../.env"
DOMAIN="${FAKTURBERES_DOMAIN:-fakturberes.farhanginting.my.id}"
[[ -f "$ENV_FILE" ]] || { echo "galat: .env belum ada; jalankan env-buat.sh" >&2; exit 1; }

value_of() {
  grep -E "^$1=" "$ENV_FILE" | head -n1 | cut -d= -f2-
}

user="$(value_of BASIC_AUTH_USER)"
[[ -n "$user" && -n "$(value_of BASIC_AUTH_SANDI)" ]] \
  || { echo "galat: BASIC_AUTH_USER atau BASIC_AUTH_SANDI kosong di .env" >&2; exit 1; }
hash="$(value_of BASIC_AUTH_SANDI | caddy hash-password)"
[[ "$hash" == \$2* ]] || { echo "galat: hash sandi tidak terbentuk" >&2; exit 1; }

target="/etc/caddy/Caddyfile"
staged="$(mktemp)"
trap 'rm -f "$staged"' EXIT
# Hash bcrypt memuat $ dan /, jadi diganti lewat awk (bukan sed) dengan nilai dari lingkungan.
DOMAIN="$DOMAIN" BASIC_USER="$user" BASIC_HASH="$hash" awk '
  {
    gsub(/__DOMAIN__/, ENVIRON["DOMAIN"])
    gsub(/__BASIC_USER__/, ENVIRON["BASIC_USER"])
    while ((i = index($0, "__BASIC_HASH__")) > 0)
      $0 = substr($0, 1, i - 1) ENVIRON["BASIC_HASH"] substr($0, i + length("__BASIC_HASH__"))
    print
  }' "$HERE/Caddyfile" >"$staged"
unset hash

caddy validate --adapter caddyfile --config "$staged" >/dev/null 2>&1 \
  || { echo "galat: Caddyfile tidak valid" >&2; caddy validate --adapter caddyfile --config "$staged" 2>&1 | tail -n 5 >&2; exit 1; }
install -m 644 -o root -g root "$staged" "$target"
systemctl enable caddy >/dev/null 2>&1 || true
systemctl reload caddy 2>/dev/null || systemctl restart caddy
echo "Caddy dimuat ulang untuk $DOMAIN, odoo.$DOMAIN, mail.$DOMAIN."

#!/usr/bin/env bash
# Membuat ~/faktur-beres/.env di VPS dari .env.example. Dijalankan di VPS, aman diulang: nilai
# yang sudah ada tidak pernah ditimpa, dan tidak ada nilai rahasia yang dicetak.
# GEMINI_API_KEY dibiarkan kosong; pemilik mengisinya sendiri dengan `nano ~/faktur-beres/.env`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
DOMAIN="${FAKTURBERES_DOMAIN:-fakturberes.farhanginting.my.id}"
umask 077

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$REPO_ROOT/.env.example" "$ENV_FILE"
  echo ".env dibuat dari .env.example"
fi
chmod 600 "$ENV_FILE"

has_value() {
  grep -qE "^$1=.+" "$ENV_FILE"
}

# set_if_empty KEY VALUE: isi hanya bila kosong atau belum ada. Nilai tidak dicetak.
set_if_empty() {
  local key="$1" value="$2"
  if has_value "$key"; then
    printf '  %-22s sudah ada\n' "$key"
    return
  fi
  if grep -qE "^$key=" "$ENV_FILE"; then
    # Nilai hanya huruf, angka, dan : / @ . - _ sehingga aman untuk sed dengan pembatas |.
    sed -i "s|^$key=.*|$key=$value|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
  printf '  %-22s diisi\n' "$key"
}

random() {
  openssl rand -hex 24
}

for key in ODOO_DB_PASSWORD ODOO_ADMIN_PASSWORD APP_DB_PASSWORD DEMO_KODE_AP DEMO_KODE_APPROVER BASIC_AUTH_SANDI; do
  set_if_empty "$key" "$(random)"
done
if ! has_value APP_DB_URL; then
  app_password="$(grep -E '^APP_DB_PASSWORD=' "$ENV_FILE" | head -n1 | cut -d= -f2-)"
  set_if_empty APP_DB_URL "postgresql+psycopg://fakturberes:${app_password}@127.0.0.1:25432/fakturberes"
  unset app_password
fi
set_if_empty BASIC_AUTH_USER "juri"
set_if_empty INBOX_INTERVAL_DETIK "10"
set_if_empty PUBLIC_ODOO_URL "https://odoo.$DOMAIN"
set_if_empty PUBLIC_MAILPIT_URL "https://mail.$DOMAIN"

if has_value GEMINI_API_KEY; then
  echo "  GEMINI_API_KEY         sudah ada"
else
  echo "  GEMINI_API_KEY         KOSONG: pemilik mengisinya dengan nano $ENV_FILE"
fi

#!/usr/bin/env bash
# npm run services:up — nyalakan layanan fakturberes dan tunggu semuanya sehat.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

require_env_file

running=0
for service in "${SERVICES[@]}"; do
  [[ "$(container_state "$service")" == "running" ]] && running=$((running + 1))
done

# Port hanya diperiksa bila layanan belum menyala (saat menyala, port dipegang proyek ini).
if (( running == 0 )); then
  busy=()
  for port in "${PORTS[@]}"; do
    if ss -ltnH "sport = :$port" | grep -q .; then busy+=("$port"); fi
  done
  if (( ${#busy[@]} > 0 )); then
    die "port sudah dipakai proses lain: ${busy[*]}. Jangan matikan proses itu; laporkan ke pemilik."
  fi
fi

echo "== Menyalakan proyek compose $PROJECT"
compose up -d >/dev/null
echo "== Menunggu layanan sehat (batas ${WAIT_TIMEOUT:-300} detik)"
wait_healthy "${SERVICES[@]}"

cat <<INFO

Layanan Faktur Beres menyala:
  Odoo         http://localhost:28069  (login admin, sandi ODOO_ADMIN_PASSWORD di .env)
  Mailpit      http://localhost:28025  (SMTP 127.0.0.1:21025)
  app-db       127.0.0.1:25432         (database dan user fakturberes, sandi APP_DB_PASSWORD di .env)
  odoo-db      hanya di jaringan kontainer (tidak dibuka ke host)
INFO

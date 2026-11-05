# shellcheck shell=bash
# Helper Odoo bersama untuk odoo-init, odoo-snapshot, dan odoo-reset. Di-source, tidak dijalankan.
# Perintah CLI Odoo berjalan di kontainer sekali jalan (`compose run --rm`) dari definisi layanan
# odoo (image, konfigurasi, volume yang sama) saat layanan odoo dihentikan.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DB="fakturberes"
SNAPSHOT_DB="fakturberes_seed"
ODOO_LOG="var/log/odoo-cli.log"

mkdir -p "$(dirname "$ODOO_LOG")"

require_odoo_services() {
  require_env_file
  [[ "$(container_state odoo-db)" == "running" && "$(container_state odoo)" != "tidak ada" ]] \
    || die "layanan belum menyala; jalankan npm run services:up"
  wait_healthy odoo-db >/dev/null
}

# Kueri baca saja ke PostgreSQL milik Odoo (lewat soket lokal di kontainer odoo-db).
odoo_sql() {
  compose exec -T odoo-db psql -U odoo -d "$1" -v ON_ERROR_STOP=1 -tAc "$2" </dev/null
}

db_exists() {
  [[ "$(odoo_sql postgres "SELECT 1 FROM pg_database WHERE datname = '$1'")" == "1" ]]
}

odoo_cli() {
  printf '\n=== %s odoo %s\n' "$(date -Is)" "$*" >>"$ODOO_LOG"
  if ! compose run --rm --no-deps -T odoo odoo "$@" </dev/null >>"$ODOO_LOG" 2>&1; then
    echo "galat: 'odoo $1 $2' gagal. Bagian akhir $ODOO_LOG:" >&2
    tail -n 30 "$ODOO_LOG" >&2
    exit 1
  fi
}

STOPPED_BY_US=0
stop_odoo() {
  if [[ "$(container_state odoo)" == "running" ]]; then
    echo "  (layanan odoo dihentikan sementara)"
    compose stop odoo >/dev/null
    STOPPED_BY_US=1
  fi
}

start_odoo() {
  if [[ "$(container_state odoo)" != "running" ]]; then
    compose start odoo >/dev/null
  fi
  STOPPED_BY_US=0
  wait_healthy odoo >/dev/null
}

# Bila skrip gagal di tengah, layanan odoo yang dihentikan skrip dinyalakan lagi.
trap 'if (( STOPPED_BY_US )); then compose start odoo >/dev/null 2>&1 || true; fi' EXIT

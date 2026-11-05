#!/usr/bin/env bash
# npm run odoo:init — siapkan database Odoo `fakturberes`. Aman diulang: langkah yang kondisinya
# sudah terpenuhi dilewati dan dilaporkan "sudah ada".
#
# Modul dipasang lewat CLI Odoo di kontainer sekali jalan (`compose run --rm`) saat layanan odoo
# dihentikan. Pemeriksaan kondisi memakai kueri baca saja ke PostgreSQL milik Odoo.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

DB="fakturberes"
LANG_CODE="id_ID"
COUNTRY="ID"
MODULES=(purchase stock account l10n_id)
LOG="var/log/odoo-init.log"

require_env_file
[[ "$(container_state odoo-db)" == "running" && "$(container_state odoo)" != "tidak ada" ]] \
  || die "layanan belum menyala; jalankan npm run services:up"
wait_healthy odoo-db >/dev/null
mkdir -p "$(dirname "$LOG")"
printf '\n=== odoo:init %s\n' "$(date -Is)" >>"$LOG"

# Kueri baca saja ke PostgreSQL milik Odoo (lewat soket lokal di kontainer odoo-db).
odoo_sql() {
  compose exec -T odoo-db psql -U odoo -d "$1" -v ON_ERROR_STOP=1 -tAc "$2" </dev/null
}

db_exists() {
  [[ "$(odoo_sql postgres "SELECT 1 FROM pg_database WHERE datname = '$1'")" == "1" ]]
}

module_state() {
  odoo_sql "$DB" "SELECT state FROM ir_module_module WHERE name = '$1'"
}

# Kontainer sekali jalan dari definisi layanan odoo (image, konfigurasi, dan volume yang sama).
odoo_cli() {
  if ! compose run --rm --no-deps -T odoo odoo "$@" </dev/null >>"$LOG" 2>&1; then
    echo "galat: 'odoo $1 $2' gagal. Bagian akhir $LOG:" >&2
    tail -n 30 "$LOG" >&2
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

# Bila skrip gagal di tengah, layanan odoo yang kita hentikan dinyalakan lagi.
trap 'if (( STOPPED_BY_US )); then compose start odoo >/dev/null 2>&1 || true; fi' EXIT

echo "== Database $DB"
if db_exists "$DB"; then
  echo "  sudah ada"
else
  stop_odoo
  echo "  membuat database (bahasa $LANG_CODE, negara perusahaan $COUNTRY, tanpa data demo)"
  odoo_cli db init "$DB" --language "$LANG_CODE" --country "$COUNTRY"
  db_exists "$DB" || die "database $DB tidak terbentuk; lihat $LOG"
  echo "  dibuat"
fi

echo "== Modul"
missing=()
for module in "${MODULES[@]}"; do
  state="$(module_state "$module")"
  case "$state" in
    installed) echo "  $module: sudah terpasang" ;;
    "") echo "  $module: tidak tersedia di image, dilewati" ;;
    *) missing+=("$module") ;;
  esac
done
if (( ${#missing[@]} > 0 )); then
  stop_odoo
  echo "  memasang: ${missing[*]}"
  odoo_cli module install -d "$DB" "${missing[@]}"
  for module in "${missing[@]}"; do
    [[ "$(module_state "$module")" == "installed" ]] || die "modul $module gagal dipasang; lihat $LOG"
    echo "  $module: dipasang"
  done
fi

echo "== Bahasa $LANG_CODE"
if [[ "$(odoo_sql "$DB" "SELECT active FROM res_lang WHERE code = '$LANG_CODE'")" == "t" ]]; then
  echo "  sudah aktif"
else
  stop_odoo
  odoo_cli i18n loadlang -d "$DB" -l "$LANG_CODE"
  echo "  dimuat"
fi

demo="$(odoo_sql "$DB" "SELECT count(*) FROM ir_module_module WHERE demo")"
[[ "$demo" == "0" ]] || die "ada $demo modul dengan data demo; database harus tanpa demo"
echo "== Data demo: tidak ada"

start_odoo
echo "== Layanan odoo sehat"

uv run python -m faktur_beres.seed.init

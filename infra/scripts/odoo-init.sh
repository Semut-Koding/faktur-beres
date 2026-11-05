#!/usr/bin/env bash
# npm run odoo:init — siapkan database Odoo `fakturberes`. Aman diulang: langkah yang kondisinya
# sudah terpenuhi dilewati dan dilaporkan "sudah ada".
#
# Modul dipasang lewat CLI Odoo di kontainer sekali jalan (`compose run --rm`) saat layanan odoo
# dihentikan. Pemeriksaan kondisi memakai kueri baca saja ke PostgreSQL milik Odoo.
source "$(dirname "${BASH_SOURCE[0]}")/odoo-lib.sh"

LANG_CODE="id_ID"
COUNTRY="ID"
MODULES=(purchase stock account l10n_id)

require_odoo_services

module_state() {
  odoo_sql "$DB" "SELECT state FROM ir_module_module WHERE name = '$1'"
}

echo "== Database $DB"
if db_exists "$DB"; then
  echo "  sudah ada"
else
  stop_odoo
  echo "  membuat database (bahasa $LANG_CODE, negara perusahaan $COUNTRY, tanpa data demo)"
  odoo_cli db init "$DB" --language "$LANG_CODE" --country "$COUNTRY"
  db_exists "$DB" || die "database $DB tidak terbentuk; lihat $ODOO_LOG"
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
    [[ "$(module_state "$module")" == "installed" ]] || die "modul $module gagal dipasang; lihat $ODOO_LOG"
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

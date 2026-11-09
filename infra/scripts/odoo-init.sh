#!/usr/bin/env bash
# npm run odoo:init — siapkan database Odoo `fakturberes`. Aman diulang: langkah yang kondisinya
# sudah terpenuhi dilewati dan dilaporkan "sudah ada".
#
# Modul dipasang lewat CLI Odoo di kontainer sekali jalan (`compose run --rm`) saat layanan odoo
# dihentikan. Pemeriksaan kondisi memakai kueri baca saja ke PostgreSQL milik Odoo. Addon Faktur
# Beres (infra/odoo/addons) dipasang bila belum ada, dan di-upgrade hanya bila versi di
# __manifest__.py lebih baru dari versi di database (isi addon dikunci tes sidik terhadap versinya).
# Versi sama: Odoo tidak dihentikan.
source "$(dirname "${BASH_SOURCE[0]}")/odoo-lib.sh"

LANG_CODE="id_ID"
COUNTRY="ID"
MODULES=(purchase stock account l10n_id)
ADDON="faktur_beres_agent"

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
  # account memilih chart of accounts dari negara perusahaan saat dipasang; tanpa negara ID
  # yang terpasang chart generik. Database seperti itu tidak diteruskan.
  country="$(odoo_sql "$DB" "SELECT co.code FROM res_company c JOIN res_partner p ON p.id = c.partner_id LEFT JOIN res_country co ON co.id = p.country_id WHERE c.id = 1")"
  if [[ "$(module_state account)" != "installed" && "$country" != "$COUNTRY" ]]; then
    die "database $DB ada tetapi negara perusahaan bukan $COUNTRY (dibuat di luar odoo:init?). Hapus database itu dengan odoo db drop, lalu jalankan odoo:init lagi."
  fi
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

addon_db_version() {
  odoo_sql "$DB" "SELECT COALESCE(latest_version, '') FROM ir_module_module WHERE name = '$ADDON'"
}

echo "== Addon $ADDON"
manifest_version="$(uv run python -m faktur_beres.devtools.addon versi)"
if [[ "$(module_state "$ADDON")" == "installed" ]]; then
  db_version="$(addon_db_version)"
  relation="$(uv run python -m faktur_beres.devtools.addon banding "$db_version")" \
    || die "versi addon di database tidak terbaca: '$db_version'"
  case "$relation" in
    sama)
      echo "  versi sama ($manifest_version), tidak di-upgrade"
      ;;
    lebih_baru)
      stop_odoo
      odoo_cli module upgrade --outdated -d "$DB" "$ADDON"
      [[ "$(addon_db_version)" == "$manifest_version" ]] \
        || die "addon $ADDON tidak naik ke $manifest_version; lihat $ODOO_LOG"
      echo "  di-upgrade dari $db_version ke $manifest_version"
      ;;
    *)
      die "addon $ADDON di database ($db_version) lebih baru dari repo ($manifest_version); periksa checkout repo"
      ;;
  esac
else
  stop_odoo
  odoo_cli module install -d "$DB" "$ADDON"
  [[ "$(module_state "$ADDON")" == "installed" ]] || die "addon $ADDON gagal dipasang; lihat $ODOO_LOG"
  echo "  dipasang, versi $(addon_db_version)"
fi

demo="$(odoo_sql "$DB" "SELECT count(*) FROM ir_module_module WHERE demo")"
[[ "$demo" == "0" ]] || die "ada $demo modul dengan data demo; database harus tanpa demo"
echo "== Data demo: tidak ada"

start_odoo
echo "== Layanan odoo sehat"

uv run python -m faktur_beres.seed.init

#!/usr/bin/env bash
# npm run odoo:snapshot — simpan kondisi seed: salin database fakturberes beserta filestore-nya
# menjadi fakturberes_seed (`odoo db duplicate`). Snapshot lama diganti. Hanya dijalankan bila
# odoo:check lulus, supaya reset tidak pernah mengembalikan data yang salah.
source "$(dirname "${BASH_SOURCE[0]}")/odoo-lib.sh"

require_odoo_services
db_exists "$DB" || die "database $DB belum ada; jalankan npm run odoo:init dan npm run odoo:seed"

echo "== Memeriksa isi Odoo sebelum snapshot (odoo:check)"
if ! uv run python -m faktur_beres.seed.check >/dev/null; then
  die "odoo:check gagal; snapshot tidak dibuat. Lihat npm run odoo:check"
fi
echo "  lulus"

start=$SECONDS
echo "== Snapshot $DB -> $SNAPSHOT_DB"
db_exists "$SNAPSHOT_DB" && echo "  snapshot lama diganti"
stop_odoo
odoo_cli db duplicate --force "$DB" "$SNAPSHOT_DB"
db_exists "$SNAPSHOT_DB" || die "snapshot $SNAPSHOT_DB tidak terbentuk; lihat $ODOO_LOG"
start_odoo
echo "== Snapshot selesai dalam $((SECONDS - start)) detik. Kembalikan kapan saja: npm run odoo:reset"

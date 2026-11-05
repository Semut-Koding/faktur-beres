#!/usr/bin/env bash
# npm run odoo:reset — kembalikan database fakturberes ke snapshot fakturberes_seed. Database dan
# filestore lama dihapus lalu diganti salinan snapshot (`odoo db duplicate --force`). Volume tidak
# pernah dihapus. Target: kurang dari 1 menit (dipakai harness evaluasi sebelum setiap percobaan).
source "$(dirname "${BASH_SOURCE[0]}")/odoo-lib.sh"

start_ns=$(date +%s%N)
require_odoo_services
if ! db_exists "$SNAPSHOT_DB"; then
  die "snapshot $SNAPSHOT_DB belum ada. Jalankan npm run odoo:snapshot setelah odoo:seed dan odoo:check lulus."
fi

echo "== Reset $DB dari $SNAPSHOT_DB"
stop_odoo
odoo_cli db duplicate --force "$SNAPSHOT_DB" "$DB"
start_odoo
uv run python -m faktur_beres.seed.after_reset
elapsed_ms=$(( ($(date +%s%N) - start_ns) / 1000000 ))
printf '== Reset selesai dalam %d.%01d detik\n' $((elapsed_ms / 1000)) $(((elapsed_ms % 1000) / 100))

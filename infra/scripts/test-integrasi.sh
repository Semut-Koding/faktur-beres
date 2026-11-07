#!/usr/bin/env bash
# npm run test:integrasi — tes integrasi terhadap Odoo dengan key agent (pytest -m integrasi).
# Odoo dikembalikan ke snapshot seed sebelum dan sesudah tes. Reset sesudah tes tetap berjalan
# walaupun tes gagal atau dihentikan, dan kode keluar mengikuti hasil tes (reset yang gagal
# membuat kode keluar bukan nol). Argumen tambahan diteruskan ke pytest.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SCRIPTS="$REPO_ROOT/infra/scripts"
started=$SECONDS

final_reset() {
  local code=$?
  trap - EXIT
  echo
  echo "== odoo:reset (sesudah tes)"
  if ! bash "$SCRIPTS/odoo-reset.sh"; then
    echo "galat: odoo:reset sesudah tes gagal; jalankan npm run odoo:reset" >&2
    (( code == 0 )) && code=1
  fi
  echo "== test:integrasi selesai dalam $((SECONDS - started)) detik, kode keluar $code"
  exit "$code"
}

echo "== odoo:reset (sebelum tes)"
bash "$SCRIPTS/odoo-reset.sh"
trap final_reset EXIT

echo
echo "== pytest -m integrasi"
uv run python -m pytest -m integrasi -p no:cacheprovider -s "$@"

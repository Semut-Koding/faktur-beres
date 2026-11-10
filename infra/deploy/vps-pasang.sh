#!/usr/bin/env bash
# Memasang Faktur Beres di VPS sampai basis data siap. Dijalankan di VPS sebagai user biasa,
# setelah vps-siapkan.sh dan sinkron.sh, aman diulang:
#   ssh ubuntu@<vps> 'bash ~/faktur-beres/infra/deploy/vps-pasang.sh'
# Node tidak dipakai di VPS: perintah di balik skrip npm dijalankan langsung.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
export PATH="$HOME/.local/bin:$PATH"

echo "== uv"
command -v uv >/dev/null || pipx install uv >/dev/null
uv --version
uv sync --quiet

echo "== .env"
bash infra/deploy/env-buat.sh

echo "== Penyetelan memori (compose.override.yaml)"
cp infra/deploy/compose.override.yaml compose.override.yaml

echo "== Kontainer"
bash infra/scripts/services-up.sh

echo "== Odoo: init, seed, snapshot"
bash infra/scripts/odoo-init.sh
uv run python -m faktur_beres.seed.load
bash infra/scripts/odoo-snapshot.sh

echo "== Basis data aplikasi"
uv run python -m faktur_beres.db migrasi

echo "Pemasangan selesai. Berikutnya: isi GEMINI_API_KEY di .env, lalu vps-nyalakan.sh."

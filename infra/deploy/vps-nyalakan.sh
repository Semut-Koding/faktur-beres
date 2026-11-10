#!/usr/bin/env bash
# Mengindeks kebijakan (butuh GEMINI_API_KEY) lalu memasang dan menyalakan layanan user.
# Dijalankan di VPS sebagai user biasa setelah vps-pasang.sh, aman diulang:
#   ssh ubuntu@<vps> 'bash ~/faktur-beres/infra/deploy/vps-nyalakan.sh'
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
export PATH="$HOME/.local/bin:$PATH"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

grep -qE '^GEMINI_API_KEY=.+' .env \
  || { echo "galat: GEMINI_API_KEY kosong. Isi dengan: nano $REPO_ROOT/.env" >&2; exit 1; }

echo "== Indeks kebijakan (RAG)"
uv run python -m faktur_beres.tools.policy_cli indeks

echo "== Layanan user systemd"
mkdir -p "$HOME/.config/systemd/user"
cp infra/deploy/systemd/fakturberes-layanan.service infra/deploy/systemd/fakturberes-api.service \
  "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable fakturberes-layanan.service fakturberes-api.service >/dev/null 2>&1
systemctl --user start fakturberes-layanan.service
systemctl --user restart fakturberes-api.service

echo "== Menunggu API"
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1:28000/api/metrik; then
    echo "  API menjawab di 127.0.0.1:28000"
    free -m
    exit 0
  fi
  sleep 2
done
echo "galat: API belum menjawab. Lihat: journalctl --user -u fakturberes-api -n 50" >&2
exit 1

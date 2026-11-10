#!/usr/bin/env bash
# Menyalin repo dari laptop ke VPS (rsync lewat SSH). Dijalankan di laptop:
#   bash infra/deploy/sinkron.sh [user@host]
#
# Yang tidak pernah dikirim: rahasia dan berkas runtime (.env, var/), lingkungan lokal (.venv,
# node_modules, .git), dan semua label evaluasi. Label tidak boleh ada di mesin yang menjalankan
# agent (CLAUDE.md), jadi dari eval/ hanya ringkasan hasil yang ikut. Berkas yang dikecualikan
# juga tidak dihapus di VPS. dashboard/dist dibangun dulu di laptop, lalu ikut dikirim.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET="${1:-ubuntu@43.173.40.20}"
cd "$REPO_ROOT"

echo "== Membangun dashboard"
npm --prefix dashboard run build >/dev/null

echo "== Menyalin ke $TARGET:faktur-beres/"
rsync -az --delete --human-readable --stats \
  --include='/.env.example' --exclude='/.env' --exclude='/.env.*' \
  --exclude='/.git/' --exclude='/.venv/' --exclude='node_modules/' --exclude='/var/' \
  --exclude='/compose.override.yaml' \
  --exclude='__pycache__/' --exclude='.pytest_cache/' --exclude='.mypy_cache/' --exclude='.ruff_cache/' \
  --exclude='/_privat/' --exclude='/privat/' --exclude='*.zip' \
  --include='/eval/' --include='/eval/ringkasan-terakhir.json' --exclude='/eval/*' \
  --include='/tests/' --include='/tests/data/' --include='/tests/data/addon_fingerprint.json' \
  --exclude='/tests/data/*' --exclude='/tests/*' \
  "$REPO_ROOT/" "$TARGET:faktur-beres/" | grep -E 'Number of (regular )?files|Total transferred' | sed 's/^/  /'

echo "Sinkron selesai."

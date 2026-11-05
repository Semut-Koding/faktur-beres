# shellcheck shell=bash
# Pustaka bersama skrip Podman Faktur Beres. Di-source oleh skrip lain, tidak dijalankan langsung.
# Aturan (CLAUDE.md, ADR-0003): hanya proyek compose `fakturberes`, semua path dikutip,
# tidak pernah menyentuh kontainer, volume, atau jaringan milik proyek lain.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PROJECT="fakturberes"
SERVICES=(odoo-db app-db mailpit odoo)
PORTS=(28069 25432 21025 28025)

# Banner "Executing external compose provider" dari podman tidak perlu tampil.
export PODMAN_COMPOSE_WARNING_LOGS=false

compose() {
  podman compose -p "$PROJECT" "$@"
}

die() {
  echo "galat: $*" >&2
  exit 1
}

require_env_file() {
  [[ -f "$REPO_ROOT/.env" ]] || die ".env belum ada; jalankan npm run setup"
}

# Nama kontainer untuk satu layanan (kosong bila belum dibuat).
container_of() {
  local service="$1" name
  name="$(compose ps --format '{{.Label "io.podman.compose.service"}} {{.Names}}' 2>/dev/null \
    | awk -v s="$service" '$1 == s { print $2; exit }')"
  # Pengaman: hanya kontainer milik proyek ini.
  if [[ -n "$name" && "$name" != "${PROJECT}"_* ]]; then
    die "kontainer '$name' bukan milik proyek $PROJECT"
  fi
  printf '%s' "$name"
}

container_state() {
  local name
  name="$(container_of "$1")"
  [[ -n "$name" ]] || { printf 'tidak ada'; return; }
  podman inspect --format '{{.State.Status}}' "$name" 2>/dev/null || printf 'tidak diketahui'
}

health_of() {
  local name
  name="$(container_of "$1")"
  [[ -n "$name" ]] || { printf -- '-'; return; }
  podman inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' \
    "$name" 2>/dev/null || printf -- '-'
}

# Tunggu sampai healthcheck semua layanan yang disebut lulus, dengan batas waktu total.
wait_healthy() {
  local timeout="${WAIT_TIMEOUT:-300}" start=$SECONDS service name
  for service in "$@"; do
    while :; do
      name="$(container_of "$service")"
      if [[ -n "$name" ]]; then
        if [[ "$(health_of "$service")" == "healthy" ]] \
          || podman healthcheck run "$name" >/dev/null 2>&1; then
          break
        fi
      fi
      if (( SECONDS - start > timeout )); then
        echo "galat: $service belum sehat setelah ${timeout} detik" >&2
        echo "lihat log: npm run services:logs -- $service" >&2
        return 1
      fi
      sleep 2
    done
    printf '  %-8s sehat (%ss)\n' "$service" "$(( SECONDS - start ))"
  done
}

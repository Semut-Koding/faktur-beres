#!/usr/bin/env bash
# npm run services:logs -- <layanan> [opsi podman logs, mis. --tail 100 atau -f]
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

if (( $# == 0 )); then
  echo "pakai: npm run services:logs -- <layanan> [--tail N] [-f]" >&2
  echo "layanan: ${SERVICES[*]}" >&2
  exit 2
fi
compose logs "$@"

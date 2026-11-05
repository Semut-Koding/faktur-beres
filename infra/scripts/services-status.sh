#!/usr/bin/env bash
# npm run services:status — status dan kesehatan layanan fakturberes.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

printf '%-9s %-28s %-10s %-10s %s\n' "LAYANAN" "KONTAINER" "STATUS" "KESEHATAN" "PORT"
for service in "${SERVICES[@]}"; do
  name="$(container_of "$service")"
  ports="-"
  if [[ -n "$name" ]]; then
    ports="$(podman inspect --format '{{range $p, $b := .NetworkSettings.Ports}}{{range $b}}{{.HostIp}}:{{.HostPort}}->{{$p}} {{end}}{{end}}' "$name" 2>/dev/null || true)"
  fi
  printf '%-9s %-28s %-10s %-10s %s\n' "$service" "${name:--}" "$(container_state "$service")" \
    "$(health_of "$service")" "${ports:--}"
done

echo
echo "Volume:"
podman volume ls --filter "label=io.podman.compose.project=$PROJECT" --format '  {{.Name}}'

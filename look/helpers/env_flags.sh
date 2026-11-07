#!/bin/bash
# vps-look helper: print only the configured env var names of the configured
# container. The name pattern arrives via the environment; nothing else can
# match, and the service user cannot widen it (sudoers pins this script and
# the server is root-owned configuration).
set -u
container="${VPSLOOK_FLAG_CONTAINER:-}"
names="${VPSLOOK_FLAG_NAMES:-}"
if [ -z "$container" ] || [ -z "$names" ]; then
  echo "ERROR: env_flags not configured" >&2; exit 2
fi
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" \
  | grep -E "^($names)=" || true

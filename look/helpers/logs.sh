#!/bin/bash
# vps-look helper: recent logs for allowlisted services only.
# Usage: logs.sh <service> <lines 1..300>
# Allowlists arrive via the environment from the MCP server, space-padded
# so membership tests are exact.
set -u
svc="${1:-}"; n="${2:-}"
DOCKER_OK="${VPSLOOK_DOCKER_OK:- }"
UNIT_OK="${VPSLOOK_UNIT_OK:- }"
case "$n" in ''|*[!0-9]*) echo "ERROR: lines must be an integer 1..300" >&2; exit 2;; esac
if [ "$n" -lt 1 ] || [ "$n" -gt 300 ]; then echo "ERROR: lines must be 1..300" >&2; exit 2; fi
case "$DOCKER_OK" in *" $svc "*) docker logs --tail "$n" "$svc" 2>&1; exit 0;; esac
case "$UNIT_OK" in *" $svc "*) journalctl -u "$svc" -n "$n" --no-pager -o short-iso 2>&1; exit 0;; esac
echo "ERROR: service not in allowlist: $svc" >&2; exit 2

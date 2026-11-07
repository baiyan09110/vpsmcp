#!/bin/bash
# vps-look helper: docker containers + allowlisted systemd units. No args.
set -u
echo "== docker containers =="
docker inspect --format '{{.Name}} | image={{.Config.Image}} | state={{.State.Status}} | started={{.State.StartedAt}} | restarts={{.RestartCount}}' $(docker ps -q) 2>/dev/null | sed 's#^/##'
echo "== systemd units (allowlist) =="
for u in ${VPSLOOK_UNITS:-}; do
  st=$(systemctl is-active "$u" 2>/dev/null || echo "unknown")
  ts=$(systemctl show "$u" -p ActiveEnterTimestamp --value 2>/dev/null)
  echo "$u | active=$st | since=$ts"
done

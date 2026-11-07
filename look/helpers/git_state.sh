#!/bin/bash
# vps-look helper: read-only git state of the configured repo. No args.
# Repo and upstream arrive via the environment from the MCP server.
set -u
export GIT_OPTIONAL_LOCKS=0
repo="${VPSLOOK_REPO:-/opt/your-app}"
upstream="${VPSLOOK_UPSTREAM:-origin/main}"
cd "$repo" || { echo "ERROR: repo dir missing"; exit 1; }
echo "branch: $(git branch --show-current)"
counts=$(git rev-list --left-right --count "HEAD...$upstream" 2>/dev/null || echo "")
if [ -n "$counts" ]; then
  ahead=$(echo "$counts" | awk '{print $1}'); behind=$(echo "$counts" | awk '{print $2}')
  echo "vs $upstream (last fetched, no fetch performed): ahead $ahead, behind $behind"
else
  echo "vs $upstream: unknown (no $upstream ref locally)"
fi
echo "--- git log -10 --oneline ---"
git log -10 --oneline
echo "--- git status --short ---"
git status --short

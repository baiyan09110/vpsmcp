#!/bin/bash
# Example vps-look helper: aggregate stats from an audit table.
# Usage: retrieval_stats.sh <days 1..14>
# Adapt the environment defaults to your schema. The invariants that make
# this safe (see README.md in this folder):
#   * aggregate counts and percentiles only, never rows
#   * from the JSON detail column, extract ONLY the one numeric field
#   * query a throwaway copy of the DB, opened read-only
set -u
days="${1:-}"
case "$days" in ''|*[!0-9]*) echo "ERROR: days must be an integer 1..14" >&2; exit 2;; esac
if [ "$days" -lt 1 ] || [ "$days" -gt 14 ]; then echo "ERROR: days must be 1..14" >&2; exit 2; fi
DB="${VPSSTATS_DB:-/opt/your-app/data/app.sqlite3}"
TABLE="${VPSSTATS_TABLE:-audit_events}"
EVENT="${VPSSTATS_EVENT:-retrieval}"
TMPD=$(mktemp -d /tmp/vpslook-stats.XXXXXX)
trap 'rm -rf "$TMPD"' EXIT
cp "$DB" "$TMPD/db.sqlite3"
cp "$DB-wal" "$TMPD/db.sqlite3-wal" 2>/dev/null || true
cp "$DB-shm" "$TMPD/db.sqlite3-shm" 2>/dev/null || true
TABLE="$TABLE" EVENT="$EVENT" python3 - "$days" "$TMPD/db.sqlite3" << 'PYEOF'
import json, math, os, sqlite3, sys
from datetime import datetime, timedelta, timezone

days = int(sys.argv[1])
table, event = os.environ["TABLE"], os.environ["EVENT"]
now = datetime.now(timezone.utc)
cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
con = sqlite3.connect("file:" + sys.argv[2] + "?mode=ro", uri=True)
out = {"window_days": days, "cutoff_utc": cutoff}
out["by_status"] = dict(con.execute(
    f"SELECT status, COUNT(*) FROM {table} WHERE event=? AND created_at >= ? "
    "GROUP BY status ORDER BY COUNT(*) DESC", (event, cutoff)).fetchall())
out["by_platform"] = dict(con.execute(
    f"SELECT platform, COUNT(*) FROM {table} WHERE event=? AND created_at >= ? "
    "GROUP BY platform ORDER BY COUNT(*) DESC", (event, cutoff)).fetchall())
lat = sorted(r[0] for r in con.execute(
    f"SELECT json_extract(detail_json, '$.latency_ms') FROM {table} "
    "WHERE event=? AND created_at >= ?", (event, cutoff))
    if isinstance(r[0], (int, float)))


def pct(p):
    if not lat:
        return None
    return lat[max(0, min(len(lat) - 1, math.ceil(p / 100 * len(lat)) - 1))]


out["latency_ms"] = {"count": len(lat), "p50": pct(50), "p95": pct(95)}
con.close()
print(json.dumps(out, ensure_ascii=False, indent=1))
PYEOF

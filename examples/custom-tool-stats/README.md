# Adding your own read-only tool, safely: aggregate stats

The most useful extra tool in the original deployment reports statistics
from an audit table in the app's SQLite database: how many retrieval
events happened in the last N days, broken down by status and platform,
plus latency percentiles. Two properties make it safe to expose:

1. **Aggregates only.** The tool returns counts and percentiles. It never
   returns a row, a title, or anything a user typed.
2. **Never select the text column.** The audit table has a `detail_json`
   column that contains fragments of user text (query previews). The
   query extracts exactly one numeric field from it —
   `json_extract(detail_json, '$.latency_ms')` — and nothing else. The
   moment a query does `SELECT detail_json`, the tool stops being a stats
   tool and becomes a reading-other-people's-chats tool.

The helper also copies the database (plus its `-wal`/`-shm`) to a temp
directory and opens the copy read-only, so a stats query can never lock
or disturb the live database.

## Wiring it up

- Put `retrieval_stats.sh` next to the other helpers, root-owned.
- Add one sudoers line for it (it takes exactly one argument, days 1..14,
  which the script validates itself).
- Add the tool to `look/server.py` next to the others:

```python
@mcp.tool()
async def retrieval_stats(days: int = 7) -> str:
    """Aggregate audit stats for the last N days (1..14): counts by status
    and platform, latency p50/p95. Numbers only, never content."""
    ok = False
    try:
        if not isinstance(days, int) or days < 1 or days > 14:
            raise ValueError("days must be an integer between 1 and 14")
        r = await _helper("retrieval_stats.sh", str(days))
        ok = True
        return r
    finally:
        _log("retrieval_stats", f"days={days}", ok)
```

- Point the script's environment (`VPSSTATS_DB`, `VPSSTATS_TABLE`, …) at
  your schema from `look/config.toml` via the server's `_helper_env()` —
  or hard-code your values in your private copy. Either way, adapt the
  SQL: your table/column names will differ. The shape is what matters:
  `GROUP BY` counts, one `json_extract`ed number, percentiles in Python.

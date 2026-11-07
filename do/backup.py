#!/usr/bin/env python3
"""vps-do pre-work backup. Root-owned; run via sudo by the vps-do service.

For each configured data directory: every SQLite database is snapshotted
with the SQLite backup API (consistent while the app is live), everything
else is tarred. Keeps the newest N sets. Prints one JSON summary line on
success; exits non-zero on any failure — the caller refuses to start.

Usage: backup.py [config.toml]   (default /etc/vps-do/config.toml)
"""
import json
import sqlite3
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


def load_cfg(path):
    cfg = {"backup": {"data_dirs": ["/opt/your-app/data"],
                      "keep": 10, "root": "/var/backups/vps-do"}}
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        if isinstance(raw.get("backup"), dict):
            cfg["backup"].update(raw["backup"])
        if isinstance(raw.get("paths"), dict) and "backup_root" in raw["paths"]:
            cfg["backup"]["root"] = raw["paths"]["backup_root"]
    except OSError:
        pass
    return cfg["backup"]


def snapshot_dir(src: Path, dest: Path):
    dbs = sorted(p for p in src.rglob("*")
                 if p.is_file() and (p.suffix in (".sqlite3", ".db")))
    for db in dbs:
        rel = db.relative_to(src)
        out = dest / "db" / Path(src.name) / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        src_con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        dst_con = sqlite3.connect(str(out))
        try:
            with dst_con:
                src_con.backup(dst_con)
        finally:
            dst_con.close()
            src_con.close()
    tar_path = dest / f"{src.name}-files.tar.gz"
    r = subprocess.run(
        ["tar", "-czf", str(tar_path), "--warning=no-file-changed",
         "--exclude=*.sqlite3", "--exclude=*.sqlite3-wal",
         "--exclude=*.sqlite3-shm", "--exclude=*.db", "--exclude=*.db-wal",
         "--exclude=*.db-shm", "-C", str(src), "."],
        capture_output=True, text=True)
    if r.returncode not in (0, 1):  # 1 = files changed while reading: fine for live data
        raise RuntimeError(f"tar failed for {src}: rc={r.returncode}: {r.stderr[:300]}")
    return len(dbs)


def main():
    cfg = load_cfg(sys.argv[1] if len(sys.argv) > 1 else "/etc/vps-do/config.toml")
    root = Path(cfg["root"])
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = root / ts
    dest.mkdir(parents=True, exist_ok=False)
    databases = 0
    for d in cfg["data_dirs"]:
        src = Path(d)
        if not src.is_dir():
            print(f"data dir missing: {src}", file=sys.stderr)
            return 1
        databases += snapshot_dir(src, dest)
    total = 0
    files = 0
    for p in dest.rglob("*"):
        if p.is_file():
            total += p.stat().st_size
            files += 1
    keep = int(cfg["keep"])
    sets = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name)
    for old in sets[:-keep]:
        subprocess.run(["rm", "-rf", str(old)], check=True)
    print(json.dumps({"dest": str(dest), "bytes": total, "files": files,
                      "databases": databases,
                      "kept_sets": min(len(sets), keep)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""vps-do Telegram notifier. Root-owned; run via sudo by the vps-do service.

Reads the alert bot token + chat id from the alert env file at call time
(they exist nowhere else and are never printed). The env file location
comes from $VPSDO_ALERT_ENV, default /etc/vps-do/alert.env. Message text
arrives on stdin. An optional API base override (for tests) can be placed
in a file named tg-api-base next to the alert env file.

Exit 0 only when Telegram confirms ok=true; anything else is a failure,
which the caller treats as fail-closed.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path


def load_env(path):
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main():
    env_path = os.environ.get("VPSDO_ALERT_ENV", "/etc/vps-do/alert.env")
    try:
        cfg = load_env(env_path)
    except OSError as e:
        print(f"alert env unreadable: {e}", file=sys.stderr)
        return 3
    token = cfg.get("ALERT_BOT_TOKEN", "")
    chat = cfg.get("ALERT_CHAT_ID", "")
    if not token or not chat:
        print("alert bot token/chat not configured", file=sys.stderr)
        return 3
    base = "https://api.telegram.org"
    override = Path(env_path).parent / "tg-api-base"
    if override.exists():
        base = override.read_text(encoding="utf-8").strip() or base
    text = sys.stdin.read()[:3500]
    body = json.dumps({"chat_id": chat, "text": text,
                       "disable_web_page_preview": True}).encode("utf-8")
    req = urllib.request.Request(f"{base}/bot{token}/sendMessage",
                                 data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"send failed: {e}", file=sys.stderr)
        return 1
    if not data.get("ok"):
        print(f"telegram not ok: {str(data)[:200]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

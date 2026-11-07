#!/usr/bin/env python3
"""vps-do doorbell (optional): start/stop vps-do.service by sending a
numeric code to the same Telegram alert bot vps-do reports to.

Why codes instead of words: digits are identical on every keyboard and in
every language, they don't get autocorrected, and a short numeric code is
a small second factor on top of the real lock — this program only ever
acts on messages from the owner's chat id (in the alert env file).

Behaviour:
- Long-polls getUpdates. Messages from any other chat are ignored.
- START code: if the service is off, replies instantly ("starting…"),
  runs systemctl start (vps-do takes its pre-work backup and posts its
  own report), then confirms. If already on, replies with the time left
  before the automatic stop.
- STOP code: stops the service and confirms; if already off, says so.
- Any other text from the owner gets a one-line status reply. The codes
  are never echoed back.
- Anti-replay: messages older than 5 minutes are ignored, the update
  offset is persisted, and the backlog is discarded at first startup, so
  a restart can never re-fire an old code.

Config: doorbell.env next to the alert env file (see doorbell.env.example)
— START_CODE, STOP_CODE, SERVICE, RUNTIME_LIMIT_SECONDS.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

ALERT_ENV = os.environ.get("VPSDO_ALERT_ENV", "/etc/vps-do/alert.env")
DOORBELL_ENV = os.environ.get(
    "VPSDO_DOORBELL_ENV",
    os.path.join(os.path.dirname(ALERT_ENV), "doorbell.env"))
STATE_DIR = os.environ.get("VPSDO_DOORBELL_STATE", "/var/lib/vps-do-doorbell")
OFFSET_FILE = os.path.join(STATE_DIR, "offset")
MAX_AGE_S = 300


def load_env(path):
    env = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


_cfg = load_env(ALERT_ENV)
TOKEN = _cfg.get("ALERT_BOT_TOKEN", "")
OWNER_CHAT = _cfg.get("ALERT_CHAT_ID", "")
_codes = load_env(DOORBELL_ENV)
START_CODE = _codes.get("START_CODE", "")
STOP_CODE = _codes.get("STOP_CODE", "")
SERVICE = _codes.get("SERVICE", "vps-do.service")
RUNTIME_LIMIT_S = int(_codes.get("RUNTIME_LIMIT_SECONDS", "7200"))


def api_base():
    override = os.path.join(os.path.dirname(ALERT_ENV), "tg-api-base")
    try:
        with open(override, encoding="utf-8") as fh:
            base = fh.read().strip()
            if base:
                return base.rstrip("/")
    except OSError:
        pass
    return "https://api.telegram.org"


def api(method, payload, timeout=70):
    req = urllib.request.Request(
        f"{api_base()}/bot{TOKEN}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send(text):
    try:
        api("sendMessage", {"chat_id": OWNER_CHAT, "text": text}, timeout=20)
    except Exception as exc:  # the doorbell must survive a failed reply
        print(f"send failed: {exc}", flush=True)


def ctl(*args):
    return subprocess.run(["/usr/bin/systemctl", *args],
                          capture_output=True, text=True, timeout=180)


def is_active():
    return ctl("is-active", SERVICE).stdout.strip() == "active"


def remaining_minutes():
    try:
        out = ctl("show", SERVICE, "-p", "ActiveEnterTimestampMonotonic",
                  "--value").stdout.strip()
        enter_s = int(out) / 1e6
        with open("/proc/uptime", encoding="utf-8") as fh:
            now_s = float(fh.read().split()[0])
        return max(0, int((RUNTIME_LIMIT_S - (now_s - enter_s)) // 60))
    except Exception:
        return None


def handle(text):
    text = (text or "").strip()
    if START_CODE and text == START_CODE:
        if is_active():
            left = remaining_minutes()
            tail = f", about {left} minutes before it stops itself" if left is not None else ""
            send(f"💡 vps-do is already awake{tail}.")
            print("START code while active; reported state", flush=True)
            return
        send("🚪 Got it — starting vps-do (backup first, ~15 seconds)…")
        print("START code; starting service", flush=True)
        r = ctl("start", SERVICE)
        if is_active():
            send("✅ vps-do is on. Send the stop code when you're done, or it stops itself in 2 hours.")
        else:
            err = (r.stdout + r.stderr).strip()[:200]
            send(f"⚠️ Start failed, the service did not come up. systemctl said: {err}")
        return
    if STOP_CODE and text == STOP_CODE:
        if not is_active():
            send("💤 vps-do is already asleep.")
            print("STOP code while inactive; reported state", flush=True)
            return
        ctl("stop", SERVICE)
        if not is_active():
            send("🌙 Done — vps-do is off.")
        else:
            send("⚠️ The stop didn't take. Please check the box.")
        print("STOP code; stopped service", flush=True)
        return
    state = "awake" if is_active() else "asleep"
    send(f"🚪 vps-do is {state}. Send the start code to wake it, the stop code to put it back.")


def read_offset():
    try:
        with open(OFFSET_FILE, encoding="utf-8") as fh:
            return int(fh.read().strip())
    except Exception:
        return None


def write_offset(value):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(OFFSET_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(value))
    except Exception as exc:
        print(f"offset write failed: {exc}", flush=True)


def main():
    if not TOKEN or not OWNER_CHAT or not START_CODE or not STOP_CODE:
        print("doorbell: missing token/chat/codes; exit", flush=True)
        sys.exit(1)
    offset = read_offset()
    if offset is None:
        # Discard the backlog: jump past the newest update so a fresh
        # install (or a lost state file) can never replay an old code.
        try:
            r = api("getUpdates", {"offset": -1, "timeout": 0}, timeout=20)
            ups = r.get("result", [])
            offset = (ups[-1]["update_id"] + 1) if ups else 0
        except Exception:
            offset = 0
        write_offset(offset)
    print(f"doorbell up; offset={offset}", flush=True)
    while True:
        try:
            r = api("getUpdates",
                    {"offset": offset, "timeout": 50,
                     "allowed_updates": ["message"]},
                    timeout=70)
        except Exception as exc:
            print(f"poll failed: {exc}", flush=True)
            time.sleep(5)
            continue
        for upd in r.get("result", []):
            offset = upd["update_id"] + 1
            write_offset(offset)
            msg = upd.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            if chat != str(OWNER_CHAT):
                continue
            if time.time() - float(msg.get("date", 0)) > MAX_AGE_S:
                print("stale message ignored", flush=True)
                continue
            print(f"owner message received: {str(msg.get('text', ''))[:20]!r}",
                  flush=True)
            handle(msg.get("text", ""))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""vps-do: a gated work connector — an MCP server whose one real tool is a
shell, wrapped in every guardrail we could think of. Read this before use:

  run() executes commands as a dedicated user with FULL sudo. That is the
  product. The guardrails below reduce accidents and guarantee an off-box
  record; they do not turn this into a sandbox:

  1. The service is OFF by default. It is not enabled at boot; a human (or
     the optional doorbell) starts it, and systemd stops it after
     RuntimeMaxSec (2h) no matter what.
  2. Before it accepts any command it takes a consistent backup of the
     configured data directories and reports the backup to Telegram. If
     the backup or the report fails, the service refuses to start.
  3. Every command is announced to the owner's Telegram BEFORE it runs
     (fail-closed: if the announcement cannot be sent, the command is
     refused) and reported again afterwards with exit code and duration.
  4. A heuristic denylist blocks a small set of catastrophic accidents
     (root wipes, disk formatting, editing the connectors' own files,
     reading .env files or SSH keys, stopping the connectors themselves).
     It is an accident net, NOT a security boundary — a determined caller
     can walk around it, which is why 1–3 exist.
  5. A local append-only audit log mirrors every start, execution, block
     and refusal.

Configuration: TOML (see config.example.toml), from $VPS_DO_CONFIG or
/etc/vps-do/config.toml. Missing file = defaults, so the module stays
importable for tests. The path key arrives via systemd LoadCredential.
"""
import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

_DEFAULTS = {
    "server": {
        "port": 18094,
        "bind": ["127.0.0.1", "172.17.0.1"],
        "public_host": "mcp.example.com",
    },
    "paths": {
        "install_dir": "/usr/local/lib/vps-do",
        "config_dir": "/etc/vps-do",
        "audit_log": "/var/log/vps-do/audit.log",
        "backup_root": "/var/backups/vps-do",
    },
    "backup": {"data_dirs": ["/opt/your-app/data"], "keep": 10},
    "protect": {"extra_paths": []},
    "run": {"user_home": "/home/vpsdo", "max_timeout_seconds": 600},
}

CONFIG_PATH = os.environ.get("VPS_DO_CONFIG", "/etc/vps-do/config.toml")


def _load_config():
    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}
    try:
        with open(CONFIG_PATH, "rb") as fh:
            raw = tomllib.load(fh)
        for section, values in raw.items():
            if isinstance(values, dict):
                cfg.setdefault(section, {}).update(values)
    except OSError:
        pass
    return cfg


CONFIG = _load_config()
_SRV = CONFIG["server"]
_PATHS = CONFIG["paths"]
BIN = os.path.join(_PATHS["install_dir"], "bin")
AUDIT = _PATHS["audit_log"]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _audit(event, detail):
    try:
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write("%s %s %s\n" % (_now(), event, detail.replace("\n", "\\n")[:1200]))
    except OSError:
        pass


def _tg_send(text):
    """Synchronous Telegram send via the root helper. True only on confirmed success."""
    try:
        r = subprocess.run(
            ["sudo", "-n", "python3", f"{BIN}/tg_send.py"],
            input=text, capture_output=True, text=True, timeout=20,
            env={**os.environ,
                 "VPSDO_ALERT_ENV": os.path.join(_PATHS["config_dir"], "alert.env")})
        return r.returncode == 0
    except Exception:
        return False


async def _tg_send_async(text):
    return await asyncio.get_event_loop().run_in_executor(None, _tg_send, text)


# ---------- denylist (heuristic accident net, not a security boundary) ----------
def _rm_root_wipe(cmd):
    if not re.search(r"\brm\b", cmd):
        return False
    if not (re.search(r"-[a-z]*r", cmd) and re.search(r"-[a-z]*f", cmd)):
        return False
    targets = {"/", "/*", "/opt", "/etc", "/var", "/home", "/usr", "~", "$HOME"}
    targets |= set(CONFIG["protect"]["extra_paths"])
    toks = re.split(r"\s+", cmd)
    return any(t in targets or t.rstrip("/") in {x.rstrip("/") for x in targets if x != "/"} and t != "/" or t == "/" for t in toks)


def _own_files_pattern():
    parts = [
        re.escape(_PATHS["install_dir"]), re.escape(_PATHS["config_dir"]),
        r"vps-do\.service", r"vps-do-doorbell", r"/var/log/vps-do",
        re.escape(_PATHS["backup_root"]),
        r"/usr/local/lib/vps-look", r"/etc/vps-look", r"vps-look\.service",
        r"/var/log/vps-look",
    ]
    return "(" + "|".join(parts) + ")"


_DENY = [
    ("recursive delete of a system path", _rm_root_wipe),
    ("format/partition tooling", lambda c: re.search(r"\b(mkfs(\.[a-z0-9]+)?|wipefs|parted|fdisk|sfdisk|cfdisk)\b", c)),
    ("dd onto a block device", lambda c: re.search(r"\bdd\b[^|;&]*\bof=/dev/(sd|vd|xvd|nvme|hd)[a-z0-9]*", c)),
    ("sudoers modification", lambda c: re.search(r"/etc/sudoers|/etc/sudoers\.d|\bvisudo\b", c)),
    ("the connectors' own files", lambda c: re.search(_own_files_pattern(), c)),
    ("stopping/disabling the connectors", lambda c: re.search(r"systemctl\s+(stop|disable|mask|restart)\s+(vps-do|vps-look)", c)),
    ("reading .env files", lambda c: re.search(r"(?<![\w.-])\.env(?![\w-])|\.env\.(?!example)", c)),
    ("reading ssh private keys", lambda c: re.search(r"/root/\.ssh|\bid_(rsa|ed25519|ecdsa|dsa)\b", c)),
    ("reading the alert bot config", lambda c: re.search(r"alert\.env|tg-api-base|doorbell\.env", c)),
]


def _denied(cmd):
    flat = re.sub(r"\s+", " ", cmd)
    for name, fn in _DENY:
        try:
            if fn(flat):
                return name
        except Exception:
            continue
    return None


_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-***"),
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"(?i)\b(token|api[_-]?key|secret|password|passwd)\b\s*[=:]\s*[^\s\"',}]+"), r"\1=***"),
    (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "***"),
]


def _redact(text):
    for pat, rep in _SECRET_PATTERNS:
        text = pat.sub(rep, text)
    return text


def _run_backup():
    r = subprocess.run(["sudo", "-n", "python3", f"{BIN}/backup.py", CONFIG_PATH],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"backup failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout.strip().splitlines()[-1])


def build_app(key):
    host = _SRV["public_host"]
    max_timeout = int(CONFIG["run"]["max_timeout_seconds"])
    mcp = FastMCP(
        "vps-do",
        streamable_http_path=f"/vps-do/{key}/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*",
                           *[f"{a}:*" for a in _SRV["bind"] if a != "127.0.0.1"],
                           host, f"{host}:*"],
            allowed_origins=["https://claude.ai", "http://127.0.0.1:*",
                             *[f"http://{a}:*" for a in _SRV["bind"] if a != "127.0.0.1"],
                             f"https://{host}"],
        ),
    )

    @mcp.tool()
    async def run(command: str, timeout_seconds: int = 120) -> str:
        """Execute a shell command on the VPS as the service user (full sudo available).
        Every command is announced to the owner's Telegram BEFORE it runs; if that
        notification fails the command is refused. A heuristic denylist blocks
        catastrophic accidents. Returns exit code, duration, redacted stdout/stderr."""
        timeout_seconds = max(1, min(int(timeout_seconds), max_timeout))
        rule = _denied(command)
        if rule:
            _audit("BLOCKED", f"rule={rule} cmd={command[:1000]}")
            await _tg_send_async(f"⛔ vps-do blocked a command\n{_now()}\nRule: {rule}\nCommand: {command[:1000]}")
            return f"BLOCKED by the vps-do denylist ({rule}). The command was NOT executed. The block was reported to the owner."
        pre = f"🛠 vps-do is about to run\n{_now()}\nCommand: {command[:1000]}"
        if not await _tg_send_async(pre):
            _audit("REFUSED", f"reason=telegram-pre-send-failed cmd={command[:1000]}")
            return "REFUSED: the Telegram pre-notification failed, so the command was NOT executed (fail-closed)."
        _audit("EXEC", f"cmd={command[:1000]}")
        env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
               "HOME": CONFIG["run"]["user_home"], "LANG": "C.UTF-8", "TERM": "dumb"}
        start = time.monotonic()
        timed_out = False
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", command, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd="/", env=env, start_new_session=True)
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            out_b, err_b = await proc.communicate()
        dur = time.monotonic() - start
        rc = "timeout" if timed_out else proc.returncode
        out = _redact(out_b.decode("utf-8", "replace"))[:20000]
        err = _redact(err_b.decode("utf-8", "replace"))[:20000]
        if len(out) == 20000:
            out += "\n[... truncated at 20000 chars]"
        if len(err) == 20000:
            err += "\n[... truncated at 20000 chars]"
        _audit("DONE", f"rc={rc} dur={dur:.1f}s cmd={command[:200]}")
        mark = "✅" if rc == 0 else "❌"
        post_ok = await _tg_send_async(
            f"{mark} vps-do finished\nExit code: {rc}, duration: {dur:.1f}s\nCommand: {command[:200]}")
        warn = "" if post_ok else "\nWARNING: the post-execution Telegram report failed to send; the local audit log holds the record."
        if not post_ok:
            _audit("POST_SEND_FAILED", f"rc={rc} cmd={command[:200]}")
        return f"exit_code: {rc}\nduration: {dur:.1f}s\n--- stdout ---\n{out}\n--- stderr ---\n{err}{warn}"

    @mcp.tool()
    async def backup_now() -> str:
        """Run the data backup immediately (same as the startup backup) and report it to Telegram."""
        try:
            summary = await asyncio.get_event_loop().run_in_executor(None, _run_backup)
        except Exception as e:
            _audit("BACKUP_NOW_FAILED", str(e)[:300])
            return f"BACKUP FAILED: {e}"
        _audit("BACKUP_NOW", json.dumps(summary))
        await _tg_send_async(
            f"💾 vps-do manual backup complete\n{_now()}\nWhere: {summary['dest']}\n"
            f"Size: {summary['bytes']/1048576:.0f} MB, {summary['files']} files, {summary['databases']} databases")
        return json.dumps(summary, ensure_ascii=False)

    return mcp.streamable_http_app()


def main():
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY", "")
    key = open(os.path.join(cred_dir, "key"), encoding="utf-8").read().strip()
    try:
        summary = _run_backup()
    except Exception as e:
        _audit("START_REFUSED", f"backup: {e}")
        print(f"vps-do: startup backup failed, refusing to start: {e}", file=sys.stderr)
        sys.exit(1)
    report = (f"💾 vps-do starting; pre-work backup complete\n{_now()}\nWhere: {summary['dest']}\n"
              f"Size: {summary['bytes']/1048576:.0f} MB, {summary['files']} files, "
              f"{summary['databases']} databases, keeping {summary['kept_sets']} sets")
    if not _tg_send(report):
        _audit("START_REFUSED", "telegram backup report failed")
        print("vps-do: backup report could not be sent to Telegram, refusing to start", file=sys.stderr)
        sys.exit(1)
    _audit("START", f"backup_dest={summary['dest']}")
    app = build_app(key)
    socks = []
    for addr in _SRV["bind"]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((addr, _SRV["port"]))
        s.listen(128)
        socks.append(s)
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    asyncio.run(uvicorn.Server(config).serve(sockets=socks))


if __name__ == "__main__":
    main()

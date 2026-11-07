#!/usr/bin/env python3
"""vps-look: a read-only VPS status MCP connector.

Design: an LLM gets *exactly* a handful of read-only tools over Streamable
HTTP — no shell, no arbitrary commands. Anything that needs privileges goes
through fixed, root-owned helper scripts that the service may invoke via
sudo with no arguments other than the ones baked into the sudoers rules
(see sudoers.example). Every tool output is passed through secret-pattern
redaction before it leaves the box.

The URL path carries a long random key (see README.md in this folder for
why a path key, and its limits). The key is never written to any log.

Configuration: a TOML file (see config.example.toml). Location comes from
$VPS_LOOK_CONFIG, default /etc/vps-look/config.toml. Missing file = built-in
defaults with placeholder values, so the module stays importable for tests.
"""
import asyncio
import json
import os
import re
import socket
import subprocess
import tomllib
import urllib.request
import urllib.error
from datetime import datetime, timezone

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

_DEFAULTS = {
    "server": {
        "port": 18093,
        "bind": ["127.0.0.1", "172.17.0.1"],
        "public_host": "mcp.example.com",
        "key_file": "/etc/vps-look/key",
        "call_log": "/var/log/vps-look/calls.log",
        "helper_dir": "/usr/local/lib/vps-look/helpers",
    },
    "git": {"repo": "/opt/your-app", "upstream": "origin/main"},
    "services": {"docker_containers": [], "systemd_units": []},
    "env_flags": {"container": "", "names": [], "defaults": {}},
    "json_config": {"path": ""},
    "health": {"url": "http://127.0.0.1:8000/health"},
}


def _load_config():
    path = os.environ.get("VPS_LOOK_CONFIG", "/etc/vps-look/config.toml")
    cfg = {k: dict(v) for k, v in _DEFAULTS.items()}
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
        for section, values in raw.items():
            if isinstance(values, dict):
                cfg.setdefault(section, {}).update(values)
    except OSError:
        pass
    return cfg


CONFIG = _load_config()
_SRV = CONFIG["server"]


def _log(tool, params, ok):
    try:
        with open(_SRV["call_log"], "a", encoding="utf-8") as f:
            f.write("%s tool=%s params=%s result=%s\n" % (
                datetime.now(timezone.utc).isoformat(), tool, params,
                "OK" if ok else "FAIL"))
    except OSError:
        pass


def _helper_env():
    """Configuration handed to the helper scripts via the environment, so
    the helpers themselves stay free of site-specific values."""
    env = dict(os.environ)
    env["VPSLOOK_REPO"] = CONFIG["git"]["repo"]
    env["VPSLOOK_UPSTREAM"] = CONFIG["git"]["upstream"]
    env["VPSLOOK_DOCKER_OK"] = " " + " ".join(
        CONFIG["services"]["docker_containers"]) + " "
    env["VPSLOOK_UNIT_OK"] = " " + " ".join(
        CONFIG["services"]["systemd_units"]) + " "
    env["VPSLOOK_UNITS"] = " ".join(CONFIG["services"]["systemd_units"])
    env["VPSLOOK_FLAG_CONTAINER"] = CONFIG["env_flags"]["container"]
    env["VPSLOOK_FLAG_NAMES"] = "|".join(CONFIG["env_flags"]["names"])
    env["VPSLOOK_JSON_CONFIG"] = CONFIG["json_config"]["path"]
    return env


async def _helper(script, *args, timeout=25):
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", os.path.join(_SRV["helper_dir"], script), *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_helper_env())
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"{script} timed out")
    if proc.returncode != 0:
        lines = err.decode("utf-8", "replace").strip().splitlines()
        if not lines:
            lines = out.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "helper failed")
    return out.decode("utf-8", "replace")


_SECRET_VALUE_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-***"),
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    (re.compile(r"(?i)\b(token|api[_-]?key|secret|password|passwd)\b\s*[=:]\s*[^\s\"',}]+"), r"\1=***"),
    (re.compile(r"\b[A-Za-z0-9_-]{32,}\b"), "***"),
]


def _redact(text):
    for pat, rep in _SECRET_VALUE_PATTERNS:
        text = pat.sub(rep, text)
    return text


def _redact_config(node):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if re.search(r"(?i)(key|token|secret|password)", k):
                out[k] = "***"
            else:
                out[k] = _redact_config(v)
        return out
    if isinstance(node, list):
        return [_redact_config(v) for v in node]
    if isinstance(node, str):
        return _redact(node)
    return node


def build_app(key):
    host = _SRV["public_host"]
    mcp = FastMCP(
        "vps-look",
        streamable_http_path=f"/vps-look/{key}/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1:*", "localhost:*",
                *[f"{addr}:*" for addr in _SRV["bind"] if addr != "127.0.0.1"],
                host, f"{host}:*",
            ],
            allowed_origins=[
                "https://claude.ai",
                "http://127.0.0.1:*",
                *[f"http://{addr}:*" for addr in _SRV["bind"] if addr != "127.0.0.1"],
                f"https://{host}",
            ],
        ),
    )

    @mcp.tool()
    async def git_state() -> str:
        """Your app's git repo: branch, last 10 commits, working-tree status, ahead/behind vs the configured upstream (no fetch)."""
        ok = False
        try:
            r = await _helper("git_state.sh")
            ok = True
            return r
        finally:
            _log("git_state", "-", ok)

    @mcp.tool()
    async def services() -> str:
        """Docker containers (name/image/state/started/restarts) and the configured systemd units (active state, since)."""
        ok = False
        try:
            r = await _helper("services.sh")
            ok = True
            return r
        finally:
            _log("services", "-", ok)

    @mcp.tool()
    async def logs(service: str, lines: int = 100) -> str:
        """Recent logs of one allowlisted service (docker container or systemd unit). lines must be 1..300. Secret-like strings are redacted."""
        ok = False
        try:
            if not isinstance(lines, int) or lines < 1 or lines > 300:
                raise ValueError("lines must be an integer between 1 and 300")
            r = await _helper("logs.sh", service, str(lines))
            ok = True
            return _redact(r)[:20000]
        finally:
            _log("logs", f"service={service} lines={lines}", ok)

    if CONFIG["json_config"]["path"]:
        @mcp.tool()
        async def json_config() -> str:
            """Contents of the configured JSON config file; values of keys containing key/token/secret/password are redacted."""
            ok = False
            try:
                raw = await _helper("json_config.sh")
                cfg = json.loads(raw)
                ok = True
                return json.dumps(_redact_config(cfg), ensure_ascii=False, indent=1)
            finally:
                _log("json_config", "-", ok)

    if CONFIG["env_flags"]["names"]:
        @mcp.tool()
        async def env_flags() -> str:
            """Values of exactly the configured env flag names, read from the configured container. No other variables are ever read."""
            names = CONFIG["env_flags"]["names"]
            defaults = CONFIG["env_flags"]["defaults"]
            ok = False
            try:
                raw = await _helper("env_flags.sh")
                found = {}
                for line in raw.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        if k in names:
                            found[k] = v
                out = []
                for name in names:
                    if name in found:
                        out.append(f"{name}={found[name]}")
                    elif name in defaults:
                        out.append(f"{name}=(not set; code default \"{defaults[name]}\" applies)")
                    else:
                        out.append(f"{name}=(not set)")
                ok = True
                return "\n".join(out)
            finally:
                _log("env_flags", "-", ok)

    @mcp.tool()
    async def health() -> str:
        """GET the configured local health endpoint; returns HTTP status and body."""
        ok = False
        try:
            def _fetch():
                req = urllib.request.Request(CONFIG["health"]["url"])
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        return resp.status, resp.read().decode("utf-8", "replace")
                except urllib.error.HTTPError as e:
                    return e.code, e.read().decode("utf-8", "replace")
            status, body = await asyncio.get_event_loop().run_in_executor(None, _fetch)
            ok = True
            return f"HTTP {status}\n{body[:2000]}"
        finally:
            _log("health", "-", ok)

    @mcp.tool()
    async def system() -> str:
        """Disk (df -h), memory (free -m) and uptime of the VPS."""
        ok = False
        try:
            def _run():
                parts = []
                for label, cmd in [("df -h", ["df", "-h"]), ("free -m", ["free", "-m"]), ("uptime", ["uptime"])]:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                    parts.append(f"--- {label} ---\n{r.stdout.strip()}")
                return "\n".join(parts)
            r = await asyncio.get_event_loop().run_in_executor(None, _run)
            ok = True
            return r
        finally:
            _log("system", "-", ok)

    # Add your own read-only tools here. Keep them fixed-purpose: a tool
    # should take no path, no query and no free-form argument that could be
    # aimed somewhere you did not intend. See examples/ for an aggregate-only
    # database-stats tool that follows this pattern.

    return mcp.streamable_http_app()


def main():
    key = open(_SRV["key_file"], encoding="utf-8").read().strip()
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

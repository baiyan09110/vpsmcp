# vps-look — the read-only half

An MCP connector that lets your LLM **look** at your VPS: git state, service
health, allowlisted logs, a few named env flags, a health endpoint, disk and
memory. Fixed-purpose tools only. There is no shell tool, no file-read tool,
no "run a command" tool — that is the entire point.

## Tools

| tool | what it does |
|---|---|
| `git_state` | branch, last 10 commits, working-tree status, ahead/behind vs upstream (no fetch) |
| `services` | docker containers + your allowlisted systemd units |
| `logs` | last N (≤300) log lines of one allowlisted service, secret-redacted |
| `env_flags` | values of exactly the env var names you configure, from one container |
| `json_config` | one JSON config file, with secret-looking values redacted (optional) |
| `health` | GET your app's local health endpoint |
| `system` | `df -h`, `free -m`, `uptime` |

Want a tool of your own (say, aggregate stats from a database)? Follow the
pattern in [`../examples/custom-tool-stats/`](../examples/custom-tool-stats/):
aggregates only, never raw rows, and never select a column that holds text
people wrote.

## How it stays read-only

- The service runs as a dedicated unprivileged user (`vpslook`) in a
  systemd sandbox (`ProtectSystem=strict`, writable only to its log dir).
- Anything needing privileges goes through root-owned helper scripts in
  `helpers/`, invoked with `sudo -n`. `/etc/sudoers.d/vpslook` allows
  exactly those scripts — the helpers take their targets (repo path,
  allowlists, container name) from configuration the service passes in the
  environment, so the service user cannot widen them.
- Every output passes secret-pattern redaction (API-key shapes, bearer
  tokens, `token=`/`password=` values, long random strings).
- Every call is logged (tool, parameters, success/failure — never output,
  never the key) to a log file with rotation.

## Install (Debian/Ubuntu, Python ≥ 3.11, MCP Python SDK installed)

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin vpslook
sudo mkdir -p /usr/local/lib/vps-look/app /usr/local/lib/vps-look/helpers /etc/vps-look /var/log/vps-look
sudo cp server.py /usr/local/lib/vps-look/app/
sudo cp helpers/*.sh /usr/local/lib/vps-look/helpers/
sudo chmod 755 /usr/local/lib/vps-look/helpers/*.sh   # root-owned, not writable by vpslook
sudo cp config.example.toml /etc/vps-look/config.toml   # then edit it
sudoedit /etc/vps-look/config.toml
# the path key: 43 random url-safe characters
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | sudo tee /etc/vps-look/key >/dev/null
sudo chown vpslook:vpslook /etc/vps-look/key /var/log/vps-look
sudo chmod 600 /etc/vps-look/key
sudo cp sudoers.example /etc/sudoers.d/vps-look && sudo chmod 440 /etc/sudoers.d/vps-look
sudo visudo --check
sudo cp vps-look.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now vps-look
```

Then put it behind your reverse proxy (see `../examples/caddy.example`)
at `https://<public_host>/vps-look/<key>/mcp` and add that URL as a custom
connector in your MCP client, with no sign-in.

## Authentication, honestly

The connector URL contains a long random key in its path. That is a
deliberate trade-off for a personal, single-user deployment: full OAuth
(an authorization server, discovery documents, PKCE, token rotation) is a
lot of machinery to protect a read-only status page. Know the limits:

- Anyone who learns the URL can read what the tools expose. Treat it like
  a password: it never appears in this repo, in the call log, or in the
  proxy's access log in our example setup (keep proxy access logging off
  for this path, as in the examples).
- If the key leaks, rotate it: write a new key file, restart the service,
  re-add the connector.
- If you need real per-user auth, put an authenticating proxy in front or
  use a connector platform that provides OAuth — don't bolt a password
  check onto this code and call it equivalent.

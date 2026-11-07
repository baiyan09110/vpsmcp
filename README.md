# vpsmcp — vps-look + vps-do

**Two MCP connectors for your own VPS: one that lets an LLM look, and one
that lets it fix things — with the second one wrapped in every guardrail
we could think of.**

> 中文摘要：給你的 LLM 一雙眼睛和一雙手。`vps-look` 是只讀的狀態連接器
> （固定工具、白名單、輸出打碼）；`vps-do` 是能執行命令的工作連接器，
> 但默認關閉、開工前先備份、每條命令執行前先 Telegram 報備（發不出去
> 就拒絕執行）、兩小時自動停止、全程審計。另附可選的 Telegram 門鈴，
> 發數字暗號即可開關 vps-do。細節見各子目錄的 README。

Most of us who run a personal server eventually want the same two things
from an AI assistant:

1. *"Is everything OK on my box?"* — a question that needs **eyes**.
2. *"It's broken — go in and fix it."* — a request that needs **hands**.

The naive version of (2) is a root shell behind a chat window. The naive
version of (1) quietly becomes (2) the first time someone adds "just one
little write tool". This repo keeps them separate, on purpose:

| | [vps-look](look/) | [vps-do](do/) |
|---|---|---|
| purpose | status, logs, health | diagnose & repair |
| powers | fixed read-only tools, allowlists everywhere | one shell tool, full sudo |
| runs | always on | **off by default**, 2h auto-stop |
| before acting | — | backup + Telegram report, or it refuses to start |
| per command | call log (tool + params only) | Telegram announcement **before** (fail-closed), report after, append-only audit log |
| worst accident | it reads something it shouldn't (outputs are secret-redacted) | it *does* something it shouldn't — see the warnings in [do/README.md](do/README.md) |

And an optional third piece, the [doorbell](doorbell/): send a numeric
code to your Telegram alert bot to start/stop vps-do without an SSH
session. It only listens to your chat id, ignores stale messages, and
announces everything it does.

## Why this shape

- **Separate the eyes from the hands.** A read-only connector can be
  always-on and low-stakes. A shell cannot. Giving them different
  processes, users, keys and lifecycles lets you be relaxed about one and
  strict about the other.
- **Announce off-box, fail closed.** A log on the same box can be edited
  by the thing it's logging. A Telegram message already on your phone
  cannot. So every vps-do command is sent to Telegram *before* it runs —
  and if that send fails, it doesn't run.
- **Backup before work, automatically.** The best time to snapshot your
  data is the moment before an AI starts repairing it, every time,
  without anyone remembering.
- **Default off + automatic stop.** The safest root shell is the one that
  isn't running. vps-do exists only inside a work session a human opened.

## Authentication, honestly

Both connectors use a long random key *in the URL path* rather than
OAuth. For a single-user personal deployment that trade-off is defensible
(full OAuth means running an authorization server to protect a status
page), but be clear-eyed: **the URL is a password.** It must never appear
in logs (the examples keep proxy access logging off for these paths),
never be committed, and should be rotated if it leaks. If you need real
per-user authentication, don't use this as-is.

## Layout

```
look/       the read-only connector (server, helpers, unit, sudoers)
do/         the gated work connector (server, backup, notifier, unit)
doorbell/   optional Telegram on/off switch for vps-do
examples/   reverse-proxy snippets; how to add your own read-only tool
```

## Requirements

Debian/Ubuntu-ish Linux, Python ≥ 3.11, the
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
(`pip install mcp uvicorn`), systemd, sudo, docker if you want the docker
tools, and a Telegram bot for vps-do's announcements. Setup is per-part:
start with [look/README.md](look/README.md).

## Status

This is the exact design running in production on the author's own box
(generalized: names, paths, allowlists and message texts externalized
into config). It is a personal-operations tool, maintained as time
permits — issues and PRs welcome, no promises.

## License

MIT — see [LICENSE](LICENSE).

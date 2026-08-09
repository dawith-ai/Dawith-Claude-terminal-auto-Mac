# AfterLimit

**Your AI coding agent hit its usage limit. AfterLimit picks the work back up the moment the limit resets — while you're asleep, at lunch, or gone for the weekend.**

**English** · [한국어](README.md) · [中文](README.zh.md) · [日本語](README.ja.md) · [Español](README.es.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português](README.pt.md) · [Русский](README.ru.md)

[![CI](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml/badge.svg)](https://github.com/dawith-ai/afterlimit/actions/workflows/ci.yml)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-black)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Dependencies](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

```
You've hit your usage limit · resets 11pm
```

You've seen this. Your agent stops mid-task at 2pm, the limit resets at 7pm, and those five hours are just… gone — unless you happen to be sitting at your terminal at 7pm to type "continue."

AfterLimit closes that gap. A tiny background job notices the reset and resumes your parked sessions automatically. You come back to finished work instead of a stalled prompt.

```console
$ afterlimit scan
Blocked sessions: 2  (now 14:32)

  [ready]    my-api/8147d7ca   usage   resets 14:00   ← limit already lifted
  [waiting]  docs-site/32d57b   usage   resets 19:50   ← 5h 18m to go
```

## Why it's different

**Watching costs zero tokens.** AfterLimit reads your local session logs — it never calls the model to check on you. Tokens are spent only at the instant work actually resumes. Polling every 5 minutes is free.

**It doesn't game the limit.** AfterLimit waits for the *real* reset the API reported and resumes after it. It never circumvents, spoofs, or hammers the endpoint. If the limit isn't lifted yet, it backs off and looks again.

**It resumes context, not a fresh prompt.** It runs `claude --resume <session>`, so your agent continues with its in-progress todo list and file state intact — not a cold start that forgot what it was doing.

**Timezone-correct, everywhere.** The reset time Claude shows ("resets 11pm") has no timezone — it's your local wall clock. AfterLimit anchors it to the machine's timezone, so a reset reads correctly in Seoul, New York, or Berlin. (This is a real bug in naïve implementations: hard-coding one timezone sends every user outside it to the wrong reset time.)

## Install

```bash
git clone https://github.com/dawith-ai/afterlimit
cd afterlimit
./install.sh
```

The installer detects your OS and registers a background job that runs every 5 minutes:

- **macOS** → a `launchd` LaunchAgent
- **Linux** → a `systemd --user` timer (falls back to a `cron` line if systemd is absent)

On **Windows**, run the PowerShell installer instead — it registers a Task Scheduler job:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

Then check it's seeing your sessions:

```bash
afterlimit scan     # what's blocked and when it lifts — runs nothing
afterlimit config   # where it's looking, your timezone, notifications
```

Requirements: Python 3.11+ and the `claude` CLI on your `PATH`. **Zero runtime dependencies** — standard library only.

## How it works

```
every 5 min ──► scan ~/.claude/projects/*.jsonl
                  │
                  ├─ last message is a usage-limit error?      ── no ─► skip
                  ├─ reset time parsed & already passed?       ── no ─► wait
                  ├─ resumed within the cooldown window?        ── yes ─► skip
                  │
                  └─► claude --resume <session>  ──►  notify (optional webhook)
```

Every guard exists to answer one question the judges will ask — *"doesn't this just spam the model?"* No:

| Guard | What it prevents |
|---|---|
| Last message must be an API-error | Resuming a session that already moved on |
| Reset time must have passed | Knocking before the limit actually lifts |
| One resume per cycle (configurable) | A backlog of blocked sessions firing at once |
| 5-hour per-session cooldown | Re-resuming the same session repeatedly |
| Single-instance lock | Overlapping scheduler runs double-firing |
| Session age cap (3 days) | Reviving long-dead backlog and burning tokens |

## Configure

Defaults work with no config. To change anything, drop a `config.json` at `~/.config/afterlimit/`:

```json
{
  "max_resume_per_cycle": 1,
  "resume_cooldown_hours": 5,
  "max_session_age_days": 3,
  "resume_prompt": "Continue the work that was in progress...",
  "webhook_url": "https://hooks.slack.com/services/...",
  "enable_codex": true,
  "codex_sessions_dir": "~/.codex/sessions",
  "codex_bin": "codex"
}
```

If the Codex sessions directory doesn't exist (not installed), it's skipped silently — no reason to gate it behind anything. Set `enable_codex` to `false` to watch Claude Code only (pre-0.3.0 behavior).

Notifications go to any webhook that accepts JSON — Slack, Discord, or your own endpoint (the payload shape is chosen from the URL). No webhook, no notifications; nothing else changes. You can also set `AFTERLIMIT_WEBHOOK_URL` in the environment.

Try before it acts:

```bash
afterlimit --dry-run run    # shows what it *would* resume, runs nothing
```

## Uninstall

```bash
./install.sh --uninstall
```

Removes the background job and CLI. Your state files stay under `~/.local/state/afterlimit` until you delete them.

## Scope & roadmap

AfterLimit resumes **headless** sessions — the agent doesn't need to be running; it reads the logs and continues the work. This is deliberately editor- and terminal-agnostic: it works whether you drive Claude Code from a plain terminal, VS Code, or anywhere else.

**Watches both Claude Code and Codex CLI.** It scans whichever session logs exist, so using just one or both works the same way. Codex reports its error kind through a structured field (`codex_error_info`) rather than free text — no regex-guessing needed. Codex CLI versions also disagree on log shape (older versions log errors as a standalone event; newer ones nest the error inside a `task_complete` event) — both are supported.

Not yet handled, and honestly noted:

- **Interactive TUI resume** — pressing "continue" inside a *live* tmux pane that's blocked mid-conversation. A previous prototype did this; it's tmux-only and fragile, so it's left for a future opt-in mode rather than shipped half-working.
- **Other agents** — Claude Code and Codex are covered. Adapters for other CLIs are welcome; the limit-parsing core is agent-agnostic.
- **Windows scheduler** — `install.ps1` registers a Task Scheduler job; the CLI and tests run on Windows in CI. The Task Scheduler *firing* on a live desktop is the one thing CI can't prove — verify with `Get-ScheduledTask AfterLimit`.

## Design notes

- **Zero runtime dependencies.** Standard library only — nothing to audit, nothing to break on install, no license entanglements.
- **Pure core, tested.** Limit parsing and session scanning are pure functions with no I/O, cross-checked across Seoul / New York / UTC / Berlin so the timezone logic can't silently regress. `pytest -q`.
- **The core does one thing.** Detect the reset, resume once, get out of the way.

## Part of a larger kit

AfterLimit is one tool in [claude-ops](https://github.com/dawith-ai/claude-ops), a set for running Claude Code agents unattended, alongside [recall](https://github.com/dawith-ai/recall) (search your past sessions) and [guard](https://github.com/dawith-ai/guard) (don't repeat past mistakes).

## License

[MIT](LICENSE). Not affiliated with or endorsed by Anthropic.

# Passive IBKR health-check + 30-min launchd timer

**Date:** 2026-05-10
**Status:** Installed + verified (manual fire + RunAtLoad fire, both OK).
**Plan:** `docs/superpowers/plans/2026-05-10-ibkr-health-check.md` (with self-review pre-mortem).

## What landed

- `docs/agents/scripts/ibkr_health.py` — Python helper. Connects to IBKR
  on `clientId=99`, calls `reqCurrentTime`, appends `IBKR OK` /
  `IBKR FAIL` line to `~/.ibkr_health.log`, exits 0/1. Hard 10 s overall
  budget via `signal.alarm` — SIGALRM handler logs FAIL and `os._exit(1)`s
  immediately so a hung Gateway can't produce a silent exit-0.
- `docs/agents/scripts/ibkr_health.sh` — bash wrapper. Self-gates on
  local hour: silent skip with `IBKR SKIP (overnight window)` for
  hours 23 + 00–06 (avoids 01:00 ET auto-logoff and 01:15 ET daily-restart
  cron). Activates `.venv`, runs the helper, propagates exit code.
- `~/Library/LaunchAgents/com.nick.ibkr-health.plist` — `StartInterval=1800`
  (30 min), `RunAtLoad=true`. plutil-clean, registered, RunAtLoad fired at
  15:02:13Z producing an OK line. launchd's own stdout/stderr files are
  empty in steady state (script handles all logging itself).

## Verification

```
[2026-05-10T15:01:41Z] IBKR OK (server_time=2026-05-10T15:01:40+00:00 host=127.0.0.1 port=4002 clientId=99)   ← manual fire
[2026-05-10T15:02:13Z] IBKR OK (server_time=2026-05-10T15:02:12+00:00 host=127.0.0.1 port=4002 clientId=99)   ← RunAtLoad fire
```

`launchctl list | grep com.nick.ibkr-health` → `-  0  com.nick.ibkr-health`.
`clientId=99` did not collide with the running download (clientId=2).
Gateway PID 66877 unchanged across the install. Download (PID 67037)
unaffected.

## Hard rules per the review

This is a **report-only** monitor. The script does not place orders,
kill processes, or restart Gateway / IBC / v11. Recovery is owned by
`daily_restart.sh` (scheduled 01:15 ET) and the `com.ibc.gateway.plist`
KeepAlive supervisor. The health check exists to make Gateway state
observable between active-consumer interactions, not to act on it.

## What's still loose / future watchpoints

- **First overnight cycle.** Tonight the timer is silent 23:00–06:59;
  the daily-restart cron fires at 01:15 EDT. First post-restart
  health-check fire is 07:00 EDT 2026-05-11. If the cron fails at
  01:15 we won't see it in this log until 07:00 — by design, not a
  bug. Operator reviews `~/.daily_restart.log` first thing in the
  morning anyway.
- **`clientId=99`** is unused everywhere I could check, but if a
  future tool grabs it, every fire FAILs silently in the log. Mitigation
  documented in script header. If false-FAIL pattern emerges, change
  the value.
- **Log rotation.** ~12 k lines/year at ~150 chars = ~2 MB/year.
  Manageable. No rotation built in; revisit if it grows.

## How to disable

```
launchctl unload -w ~/Library/LaunchAgents/com.nick.ibkr-health.plist
rm ~/Library/LaunchAgents/com.nick.ibkr-health.plist
# Scripts stay in docs/agents/scripts/ as inert.
```

`decisions.log` updated with install line.

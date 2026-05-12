# 2026-05-10 — IBKR passive health-check + launchd timer

**Status:** EXECUTING

## What

Install a strictly passive IBKR health monitor:

- `docs/agents/scripts/ibkr_health.py` — Python helper. Opens a fresh
  `ib_insync` connection on `clientId=99` (unused by `download` (`2`)
  / `v11` (defaults from `.env`) / `check_ibkr_permissions.py` (`999`)),
  calls `reqCurrentTime` with a hard 10s overall budget, appends one
  line `[ISO_UTC] IBKR OK (server_time=...)` or `[ISO_UTC] IBKR FAIL
  (reason=...)` to `~/.ibkr_health.log`, exits 0 / 1 accordingly.
  Disconnects cleanly even on error path.
- `docs/agents/scripts/ibkr_health.sh` — bash wrapper. Self-gates on
  local hour: if current hour is in `[23, 0..6]`, log a one-liner
  `SKIPPED (overnight window)` and exit 0 without doing the connect.
  Otherwise activate `.venv`, run the Python helper, propagate exit
  code.
- `~/Library/LaunchAgents/com.nick.ibkr-health.plist` — launchd timer.
  `StartInterval=1800` (30 min), `RunAtLoad=true`, log paths separated
  from the script's own log file.

Hard rules (per the review):
- Reports only. No kill, no restart, no orders, no recovery action.
- Does not touch v11 source, `.env`, `~/ibc/config.ini`.
- Failure on any single fire is informational, not actionable. Operator
  reviews `~/.ibkr_health.log` periodically or after observing other
  symptoms.

## Why

Daily-restart at 01:15 EDT proves liveness once a day. Between fires,
mid-day Gateway zombies (TCP-listening but unresponsive) or network
hiccups currently surface only when an active consumer (download / v11)
hits them. A 30-min passive heartbeat fills that visibility gap with
zero risk: it just talks to Gateway and writes a log line.

The 23:00–07:00 silent window avoids two known-noisy events: the
01:00 ET Gateway auto-logoff and the 01:15 ET cron-driven restart.
False alarms during a planned restart would devalue real alarms.

## Alternatives considered

- **`StartCalendarInterval` with 32 entries (every 30 min, hours 7–22).**
  Rejected — equivalent semantics, more brittle plist, harder to edit if
  the gating window changes. Self-gating in bash is one `case` statement.
- **No timer; rely on download / v11 logs to surface Gateway issues.**
  Rejected — those depend on an active consumer. A passive heartbeat
  catches issues during quiet periods.
- **Higher frequency (5–10 min).** Rejected — 30 min is already
  acceptable lag for "Gateway died mid-day" and avoids log spam.
  16h × 2/h × 365d = ~12k lines/year, ~500 KB. Manageable.
- **Active recovery on FAIL** (e.g. trigger `daily_restart.sh`).
  Rejected by the review explicitly. Recovery is `daily_restart.sh`'s
  job (scheduled) and `com.ibc.gateway.plist` KeepAlive's job (reactive).
  Health check is observation only.

## Risks

- **`clientId=99` collision.** Highly unlikely (99 is unused everywhere
  I've checked) but if something else grabs it, every fire FAILs and
  the log fills with noise. Mitigation: documented in script comment;
  if false-FAIL pattern emerges, change to a less common value.
- **`ib_insync` timeout behavior on a hung Gateway.** A TCP-listening
  but unresponsive Gateway might leave `connect()` hanging past its
  internal timeout. Mitigation: enforce a hard 10s overall budget via
  Python `signal.alarm()` so the helper cannot hang indefinitely
  regardless of `ib_insync`'s internal behavior.
- **Silent exit-0 when something actually broke.** The bash wrapper must
  always propagate the Python helper's exit code (no `|| true`, no
  swallowed errors). The Python helper must `try/except` everything and
  log a FAIL line + exit 1 even on programmer error.
- **Overnight outage invisible until 07:00.** By design — review accepts
  this. If the daily-restart cron fails at 01:15, first health-check
  visibility is at 07:00. Operator reviews `~/.daily_restart.log` first
  thing each morning anyway.
- **Python venv path hard-coded.** Bash wrapper sources
  `.venv/bin/activate` from the repo root. If venv moves, the wrapper
  breaks. Mitigation: the daily-restart script has the same dependency,
  not a new fragility.
- **Log file growth.** ~12k lines/year is fine. No rotation needed for
  Y1; if it becomes an issue, add `logrotate` later.

## Verification

- After `launchctl load -w`: `RunAtLoad=true` triggers an immediate
  first fire. Within 30s, `~/.ibkr_health.log` should contain one OK
  line.
- Manual fire test before launchd install:
  `bash docs/agents/scripts/ibkr_health.sh` → exit 0, OK line in log.
- Negative test: temporarily change `IB_PORT` to a wrong value in a
  copy of the script, run manually, confirm FAIL line + exit 1.
- Gating test: set local hour to 23 (e.g. via a `--simulate-hour=23`
  flag — out of scope), or just observe the log skipping fires after
  23:00.
- After install: `launchctl list | grep com.nick.ibkr-health` shows
  PID `-` and last exit 0.

## Rollback

```
launchctl unload -w ~/Library/LaunchAgents/com.nick.ibkr-health.plist
rm ~/Library/LaunchAgents/com.nick.ibkr-health.plist
# Scripts stay in docs/agents/scripts/ as inert.
# decisions.log: append "ibkr_health timer retired".
```

Reversible in under a minute.

## Self-review

**Pre-mortem.** It's three months from now. The script caused a real
problem. What was the cause?

Most likely vector: **silent exit-0 on a real failure mode I didn't
anticipate.** A specific scenario — `ib_insync.connect()` returns a
connected `IB` object but the underlying TCP socket is half-open
(Gateway accepts but doesn't reply). `reqCurrentTime` then hangs
indefinitely. The Python helper's `signal.alarm()` saves us, but only
if I install the alarm BEFORE the connect call AND ensure the alarm
handler logs FAIL + exits 1 (not just raises an unhandled exception).
If the alarm fires during cleanup and the cleanup itself swallows the
exception, the helper might exit 0 silently while having proven
nothing about Gateway health.

Does the plan as written defend against that failure?
**PARTIALLY.** The mitigation is named ("hard 10s budget via
signal.alarm()") but the implementation needs to be careful — alarm
handler must explicitly `os._exit(1)` not raise, and it must log FAIL
before exiting. Will tighten this in the implementation.

**Per-item check:**
- WHAT — yes, this is the simplest passive monitor. Anything more
  elaborate would cross into territory the review explicitly rejected.
- WHY — yes, the visibility gap is real (mid-day Gateway zombie has no
  current observation path).
- ALTERNATIVES — `StartCalendarInterval` array is genuinely worse. The
  no-timer alternative loses the visibility benefit. Higher frequency
  doesn't add value.
- RISKS — added clientId-collision and silent-exit-0 to the list above.
  Hard timeout via `signal.alarm()` is the key mitigation; tightening
  in implementation per pre-mortem.
- VERIFICATION — pass criterion (one OK line within 30s of load) is
  observable without ambiguity. Negative test is named.
- ROLLBACK — trivial: unload + rm. Scripts can stay or go.

**Verdict:** PROCEED with the implementation tightening from the
pre-mortem (alarm handler must `os._exit(1)` after logging FAIL).

# Daily-restart cron killed v11 it just launched — missing AbandonProcessGroup

**Date:** 2026-05-11
**Status:** Fix applied to plist + reloaded. Validates on tomorrow's 01:15 EDT cron fire.
**Files touched:** `~/Library/LaunchAgents/com.nick.daily-restart.plist`,
`docs/agents/decisions.log`.

## Symptom

First production fire of the daily-restart cron at 2026-05-11 01:15 EDT
worked **perfectly** — Gateway cycled, v11 launched as PID 84016, script
logged `SUCCESS — Gateway PID=84004 v11 PID=84016` at 01:16:28. Then v11
**died 0 seconds later** with `Signal 15 received — shutting down`. v11's
final log line at 01:16:28 EDT was `V11 live trader stopped.`

The 02:00 EDT (06:00 UTC) Asian range calc had no v11 to fire it. Today's
04:00–12:00 EDT XAUUSD ORB trade window opened with no strategy running.
Captured by the 02:15 EDT one-shot watch (`91b50783`).

## Root cause

`com.nick.daily-restart.plist` did not have `AbandonProcessGroup=<true/>`.

launchd's default behavior on **job exit** is to send SIGTERM to every
process with the same process group ID as the job. `daily_restart.sh`
spawned v11 via:

```
nohup caffeinate -i python -m v11.live.run_live --live --no-llm \
    >> ~/.v11_paper.log 2>&1 &
```

`nohup` blocks SIGHUP, not SIGTERM. `caffeinate -i` doesn't detach the
process group. Python inherits the script's PGRP. When the script exits
~30 s later (after step 7's confirmation sleep), launchd's default
sweeper sends SIGTERM to the entire PGRP — including the freshly-launched
v11.

`AbandonProcessGroup=<true/>` tells launchd to leave the PGRP alone on
job exit. With this set, the spawned v11 survives the script's
completion as intended.

## Why the 2026-05-10 dry-run did not catch it

The dry-run was invoked manually from Terminal (`bash docs/agents/scripts/daily_restart.sh`).
**Manual shell invocation does not inherit launchd's PGRP-cleanup semantics.**
Only launchd-spawned executions reproduce the kill path.

This is a real pre-mortem gap. The plan's verification section said:
> Manual fire: `bash docs/agents/scripts/daily_restart.sh` → exit 0, OK line in log.

…but never asked "what fails-mode-wise does cron-fired differ from manual-fired?"
Both invocations execute the same shell script line-by-line, so testing them
felt equivalent — but they differ in their parent process tree and signal
inheritance.

**Rule of thumb to adopt going forward:** when validating a launchd plist
that spawns long-lived children, the only reliable test is a launchd-driven
fire (`launchctl kickstart -k …` or waiting for the schedule), not a
manual shell invocation. Dry-runs from Terminal validate the script's
internal logic, not its lifecycle interaction with the supervisor.

## Fix

Added an annotated block to `com.nick.daily-restart.plist`:

```xml
<key>AbandonProcessGroup</key>
<true/>
```

Plus a comment explaining the 2026-05-11 incident, the kill mechanism,
and why the dry-run didn't catch it. `plutil -lint` clean. Reloaded via
`launchctl unload -w` + `load -w`. `launchctl list` confirms registration.
RunAtLoad=false, so the load did not re-fire the script.

## What still needs to happen

- Nick will manually restart v11 today so it can participate in the
  remaining 04:00–12:00 EDT trade window. (Out of scope for this plist
  fix.)
- Tomorrow's 2026-05-12 01:15 EDT cron fire is the natural validation.
  Expected: v11 spawned by the script survives the script's exit and
  reaches the 02:00 EDT range calc. If it dies again, escalate
  immediately.

## Watchpoints / belt-and-suspenders considered

I considered also using `setsid` in `daily_restart.sh` to put v11 in a
fresh PGRP independent of the shell's PGRP — defense in depth against
this whole class of failure. **Rejected** because:
- macOS does not ship `setsid` by default (Linux util; available via
  Homebrew `util-linux`). Adding a non-default dep widens the failure
  surface.
- `AbandonProcessGroup=<true/>` is the canonical Apple-documented knob
  for this exact scenario. One change, one risk.

If `AbandonProcessGroup` turns out insufficient on tomorrow's cron fire
(unlikely per Apple docs), I'd revisit by adding a Python-level
`os.setsid()` wrapper rather than depending on `util-linux`. Filed as
future-only-if-needed.

## See also

- `docs/journal/2026-05-10_daily_restart_architecture.md` — original
  architecture decision + dry-run that missed this
- `~/.daily_restart.log` — line at 01:16:28 EDT showing SUCCESS
- `v11/live/logs/v11_live_20260511_011559.log` — v11's 29-second life
- `~/Library/LaunchAgents/com.nick.daily-restart.plist` — the fix

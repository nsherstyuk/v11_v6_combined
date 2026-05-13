# Overnight watch + 2026-05-12 stale-breakout skip

**Date:** 2026-05-12 morning
**Status:** v11 operational integrity validated end-to-end unattended.
Strategy correctly skipped the day's setup (stale breakout, by design).
Proof-of-life for the order-placement fixes remains deferred — no
brackets placed = no DIAG block = no live test of A+B+C1 or Phase
1/2/3 paths.

## What happened overnight (~22:00 EDT → ~06:00 EDT)

Claude Code session ran an autonomous /loop watch from 22:02 EDT
through 06:01 EDT. Trail in `~/.v11_overnight_watch.log`. Headline:
**every operational layer worked.**

### 01:15 EDT cron fire — clean

First unattended fire of `com.nick.daily-restart.plist` since the
`AbandonProcessGroup` fix on 2026-05-11 morning. Sequence from
`~/.daily_restart.log`:

```
01:15:05  script started
01:15:07  IBC + Gateway killed
01:15:37  clean state verified
01:15:37  re-loaded com.ibc.gateway plist
01:15:52  port 4002 listening (15s after Gateway restart)
01:15:52  v11 launched, PID=11737
01:16:22  v11 alive check passed
01:16:23  SUCCESS
```

`AbandonProcessGroup=<true/>` held — v11 survived past
`daily_restart.sh` exit. This was the failure mode that bit on
2026-05-11 (PGRP-kill at +29s); the fix is now production-validated
under unattended conditions, not just kickstart.

### Code load + startup — clean

v11 PID 11737 booted on the new code (Phase 1/2/3/5a + A+B+C1).
No import errors, no startup exceptions. Connected to Gateway,
qualified XAUUSD, refreshed daily + 4h LLM context bars, entered
the main loop with state=IDLE.

### Range calc at 02:00:02 EDT — clean

```
02:00:00  ORB: Calculating Asian range (0-6 UTC)
02:00:02  Range from IBKR bars: 4708.85 - 4773.56 (72 bars)
02:00:02  Daily range set: 4708.85 - 4773.56 (size=64.71)
```

72 bars retrieved (the expected number for a 6-hour range at
5-min bar size). Range was set in the context but the strategy
stayed IDLE because `_handle_idle` returns immediately if not in
the trade window — the strategy doesn't pull from context until
08:00 UTC. This is correct.

### 04:00:02 EDT trade window — strategy skipped (by design)

```
04:00:02  ORB state: IDLE -> RANGE_READY
04:01:00  ORB: LLM gate passed - brackets eligible (passthrough)
04:01:03  ORB stale breakout: price=4702.99 outside range
           [4708.85-4773.56], skipping (price already outside
           range at eval time)
04:01:03  state -> DONE_TODAY
          skip_reason: "stale breakout: price already below low
                        at eval time"
```

Between 02:00 EDT range calc and 04:00 EDT trade window open,
XAUUSD dropped below the range low (from inside [4708.85, 4773.56]
to 4702.99). The stale-breakout guard in
`v11/live/orb_adapter.py` correctly refused to place brackets on
a setup that no longer exists.

This is not a bug. It is the strategy declining a stale signal.

## What this means for the no-fill remediation

The morning's A+B+C1 fixes and last night's Phase 1/2/3/5a
hardening remain **untested live** because no brackets were
placed today. Concretely:

- **STP LMT** order placement (C1): not exercised.
- **DIAG `openTrades` block** (A): never emitted — no orders to
  inspect.
- **`buy_entry_id` before sleep** (B): no entry orders submitted.
- **Phase 1 rebind path**: no reconnect happened, so the rebind
  wasn't triggered.
- **Phase 2 safety-flatten convergence**: no position to flatten.
- **Phase 3 emergency_close**: no emergency.

We need a future trade window where XAUUSD stays inside the
06:00 UTC range until at least 08:00 UTC (04:00 EDT). The
stale-breakout guard fires whenever price has already broken
either side during the 02:00–04:00 EDT gap — a real risk to
having any setup at all on volatile open-flow days.

## Operational vs. trading-logic outcome — distinction worth keeping

| Layer | Validated 2026-05-12? |
|---|---|
| IBC + Gateway launchd supervision | ✓ (mid-day KeepAlive + 01:15 cron) |
| `AbandonProcessGroup` v11 lifecycle | ✓ unattended cron fire confirmed |
| v11 startup on new code | ✓ no import / startup errors |
| Range calculation | ✓ 72 bars, plausible range |
| State machine transitions | ✓ IDLE → RANGE_READY → DONE_TODAY |
| LLM gate (passthrough) | ✓ |
| Stale-breakout guard | ✓ (fired correctly) |
| Order placement (A+B+C1 STP LMT) | ✗ not exercised |
| DIAG `openTrades` capture | ✗ not exercised |
| Reconnect rebind (Phase 1) | ✗ not exercised |
| Safety-flatten convergence (Phase 2) | ✗ not exercised |
| Emergency close (Phase 3) | ✗ not exercised |

The first eight rows are the "Phase 0 verification" checklist
from the remediation plan. They passed. The bottom five are
Phase 4 evidence-gathering — still pending a real setup.

## Next forward action

- Hold the system as-is. The remediation work is durable; no code
  changes needed today.
- Watch the next trade window where price stays inside the range
  until 04:00 EDT — that's when A+B+C1 and Phase 1/2/3 get their
  first live exercise.
- No action items from overnight watch other than this journal +
  status update.

## Watchlog trail

`~/.v11_overnight_watch.log` has the full per-probe trail (10+
entries from 22:02 EDT through 06:01 EDT). Probe cadence was
~60-min during dormant hours, tighter around the cron fire
(01:15) and trade window open (04:00). Loop ended at 06:01 EDT
by omitting the next ScheduleWakeup — Nick's "wake me only for
live-position issues" rule never triggered.

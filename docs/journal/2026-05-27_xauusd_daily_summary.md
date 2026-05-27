# XAUUSD ORB daily summary — 2026-05-27

**Outcome:** NO-TRADE (stale-breakout skip). 2nd stale-breakout of the
live era (first was 2026-05-12).

Range computed 4491.68 – 4525.93 (34.25 wide). At 04:01:02 EDT
trade-window eval, price was already 4483.73 — **$7.95 below the
range low**. The stale-breakout guard correctly declined to place
brackets.

## Timeline

```
00:20:13  IB 1100/2110 disconnect cycle (recovered ~30s)
01:00:06  Gateway down — 76s outage (9 failed reconnect attempts)
01:01:23  Reconnected, rebind + reconcile clean (broker flat)
01:15:04  Cron SIGTERM — clean shutdown
01:16:26  Cron SUCCESS — Gateway PID=33661, v11 PID=33683 (new process)
02:00:03  Range from IBKR bars: 4491.68 - 4525.93 (34.25 wide, 72 bars)
04:00:03  Gap filter PASS (vol=0.000330, range=0.684)
04:00:03  ORB state: IDLE -> RANGE_READY
04:01:00  ORB LLM passthrough APPROVED (mechanical)
04:01:02  ORB stale breakout: price=4483.73 outside range
          [4491.68-4525.93], skipping
04:01:02  ORB state: RANGE_READY -> DONE_TODAY
```

No brackets placed. No fill. No PnL. The strategy did exactly what
its design says to do: when the breakout has already happened
*before* the trade window opens, skip — chasing a stale move is
worse expected value than waiting for tomorrow.

## Live record (cumulative)

| Date | Dir | Entry | Exit | PnL | Hold |
|---|---|---|---|---|---|
| 2026-05-13 | SHORT | 4687.63 | 4695.59 | −$7.96 | 6h37m |
| 2026-05-19 | SHORT | 4529.70 | 4509.20 | +$20.50 | 5h17m |
| 2026-05-21 | SHORT | 4518.09 | 4512.87 | +$5.22 | 5h33m |
| 2026-05-26 | SHORT | 4522.81 | 4507.49 | +$15.32 | 7h31m |

**Cumulative net: +$33.08** across 4 trades, 3W/1L. Unchanged.

## Non-trade pattern (live era 2026-05-12 → 2026-05-27)

13 trading days, 4 fills, 9 no-trade days:
- **stale-breakout skips: 2 (5/12, 5/27)**
- 4h-pending cancels: 4 (5/14, 5/18, 5/20, 5/22)
- range-too-wide skips: 1 (5/15)
- window-close cancels: 1 (5/25)
- weekends not counted

Fill rate: 4/13 ≈ **31%** — still hotter than the ~13%/day backtest
baseline, slowly converging as no-trade days accumulate.

## Notable observations

1. **Worst 01:00 ET Gateway disturbance observed yet — 76s full outage.**
   Prior observations were 1-30s flaps; today was a hard refusal-of-
   connection across 9 reconnect attempts spanning 01:00:06 to
   01:01:22 EDT. The v11 reconnect path eventually got through, the
   Phase 1 rebind fired, reconcile saw broker flat — all clean. Then
   the 01:15 EDT cron cycled everything anyway. **Functionally still
   handled, but the magnitude is escalating.** Two consecutive days
   (5/26 and 5/27) of escalated 01:00 ET behavior is worth flagging.
   If the pattern continues, may be worth investigating whether the
   IBKR Gateway version updated or whether IBKR changed its overnight
   restart window.
2. **New `max_pending expiry` log line did NOT exercise today** — only
   fires on successful bracket placement, and today's stale-breakout
   skip never placed brackets. First production verification of
   e4b87eb is still pending the next bracket placement.
3. **The new SL/TP lifecycle tests (4d3df69, +10 tests) cover code
   paths that the live system has yet to exercise** even after 4
   live trades. All 4 fills exited via window-close MARKET; no SL or
   TP has fired live. The tests now lock in the executor-level
   expected behavior for those paths.
4. **8th observed 01:00 ET disturbance**, 2nd consecutive day with
   the elevated-severity shape. Same pattern as 5/26 morning
   (which was a 00:17 EDT TWS↔server flap rather than a clean
   01:00 cycle). May be IBKR-side maintenance changes; nothing on
   our end to do but keep watching.

## What's not happening

- No code/config changes today
- No live position opened
- v11 healthy, sitting in DONE_TODAY until UTC-midnight reset
- Cron tonight 01:15 EDT will cycle as usual

## See also

- `docs/journal/2026-05-12_overnight_watch_and_stale_breakout_skip.md`
  — original (first) stale-breakout skip and the 5a harness validation
  that came out of it
- `docs/journal/2026-05-26_xauusd_daily_summary.md` — yesterday's
  4th live SHORT win (fastest fill yet) and the elevated 00:17 EDT
  Gateway disturbance
